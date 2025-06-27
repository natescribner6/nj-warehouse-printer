from flask import Flask, jsonify, request, render_template, url_for, send_from_directory, session, redirect
import requests
import base64
import os
import json
import uuid
from pdf2image import convert_from_path
from PIL import Image
import io
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here')

app.permanent_session_lifetime = timedelta(days=7)



# Configuration variables from environment
PRINTNODE_API_KEY = os.getenv('PRINTNODE_API_KEY')
PRINTNODE_URL = "https://api.printnode.com/printjobs"

# ShipStation Configuration
SHIPSTATION_API_KEY = os.getenv('SHIPSTATION_API_KEY')
SHIPSTATION_URL = "https://api.shipstation.com/v2/labels"

# Printer IDs from environment
LABEL_PRINTER_ID = int(os.getenv('LABEL_PRINTER_ID', 74471601))
STICKER_PRINTER_ID = int(os.getenv('STICKER_PRINTER_ID', 74471602))

# Flask Configuration
FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
FLASK_HOST = os.getenv('FLASK_HOST', '0.0.0.0')
FLASK_PORT = int(os.getenv('FLASK_PORT', 8080))

# Validate required environment variables
if not PRINTNODE_API_KEY:
    raise ValueError("PRINTNODE_API_KEY environment variable is required")
if not SHIPSTATION_API_KEY:
    raise ValueError("SHIPSTATION_API_KEY environment variable is required")

# Configuration file path
CONFIG_FILE = "print_config.json"

# Available paper sizes for PrintNode API
PAPER_SIZES = [
    "5 x 7", "6.5 x 10", "5in x 18.75 continuous"
]

# Create directories if they don't exist
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
STATIC_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
IMAGES_FOLDER = os.path.join(STATIC_FOLDER, 'images')
PDF_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pdfs')

for folder in [UPLOAD_FOLDER, STATIC_FOLDER, IMAGES_FOLDER, PDF_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}


def authenticate():
    password = request.form.get('password')
    if password == os.getenv('WAREHOUSE_PASSWORD', 'warehouse123'):
        session['authenticated'] = True
        session.permanent = True  # This line makes it persistent
        return redirect('/ops/warehouse')
    return redirect('/ops?error=1')


def load_print_config():
    """Load print configuration from JSON file"""
    if not os.path.exists(CONFIG_FILE):
        # Create default config if it doesn't exist
        default_config = {
            "products": [
                {
                    "id": "thanks-sticker",
                    "name": "Thank You Sticker",
                    "description": "Customer thank you sticker",
                    "pdf_file": "thanks-for-ordering.pdf",
                    "image": "thanks-sticker.jpg",
                    "printer_id": STICKER_PRINTER_ID,
                    "category": "Stickers",
                    "paper_size": ""  # Empty string means use printer default
                },
                {
                    "id": "play-sand-15lb",
                    "name": "Play Sand 15lb",
                    "description": "15lb play sand label",
                    "pdf_file": "oxidizing-shock-25lb.pdf",
                    "image": "play-sand-15lb.jpg",
                    "printer_id": LABEL_PRINTER_ID,
                    "category": "Labels",
                    "paper_size": ""
                },
                {
                    "id": "play-sand-25lb",
                    "name": "Play Sand 25lb", 
                    "description": "25lb play sand label",
                    "pdf_file": "play-sand-25lb.pdf",
                    "image": "play-sand-25lb.jpg",
                    "printer_id": LABEL_PRINTER_ID,
                    "category": "Labels",
                    "paper_size": ""
                },
                {
                    "id": "play-sand-50lb",
                    "name": "Play Sand 50lb",
                    "description": "50lb play sand label", 
                    "pdf_file": "play-sand-50lb.pdf",
                    "image": "play-sand-50lb.jpg",
                    "printer_id": LABEL_PRINTER_ID,
                    "category": "Labels",
                    "paper_size": ""
                }
            ]
        }
        
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=2)
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            
            # Migrate existing products to include paper_size if missing
            updated = False
            for product in config["products"]:
                if "paper_size" not in product:
                    product["paper_size"] = ""  # Default to empty (printer default)
                    updated = True
            
            # Save updated config if migration occurred
            if updated:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(config, f, indent=2)
                    
            return config
    except Exception as e:
        print(f"Error loading config: {e}")
        return {"products": []}

def save_print_config(config):
    """Save print configuration to JSON file"""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving config: {e}")
        return False

def generate_pdf_thumbnail(pdf_path, output_path):
    """Generate a thumbnail image from the first page of a PDF"""
    try:
        # Convert first page of PDF to image
        pages = convert_from_path(pdf_path, first_page=1, last_page=1, dpi=150)
        
        if pages:
            # Get the first page
            first_page = pages[0]
            
            # Resize to thumbnail size (300x300 max, maintain aspect ratio)
            first_page.thumbnail((300, 300), Image.Resampling.LANCZOS)
            
            # Save as JPG
            first_page.save(output_path, "JPEG", quality=85)
            return True
        else:
            return False
        
    except Exception as e:
        print(f"Error generating thumbnail: {e}")
        return False

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def build_print_options(product, quantity):
    """Build print options dictionary including paper size if specified"""
    options = {"copies": quantity}
    
    # Add paper size if specified (not empty)
    if product.get("paper_size") and product["paper_size"].strip():
        options["paper"] = product["paper_size"]
    
    return options

def check_auth():
    return session.get('authenticated', False)

@app.route('/ops/auth', methods=['POST'])
def authenticate():
    password = request.form.get('password')
    if password == os.getenv('WAREHOUSE_PASSWORD', 'warehouse123'):  # Set this in your .env
        session['authenticated'] = True
        return redirect('/ops/warehouse')
    return redirect('/ops?error=1')

# Modify your existing warehouse route
@app.route('/ops/warehouse')
def warehouse_interface():
    """Protected warehouse printing interface"""
    if not check_auth():
        return redirect('/ops')
    return render_template('warehouse.html')

# Also protect your batch route
@app.route('/ops/batch')
def batch_interface():
    """Protected batch printing interface"""
    if not check_auth():
        return redirect('/ops')
    return render_template('batch.html')

@app.route('/ops')
def ops_login():
    """Login page for warehouse ops"""
    if check_auth():
        return redirect('/ops/warehouse')
    
    return '''
    <html>
    <head><title>Warehouse Login</title></head>
    <body style="font-family: Arial; text-align: center; padding: 50px;">
        <h2>Warehouse Operations</h2>
        <form method="post" action="/ops/auth">
            <input type="password" name="password" placeholder="Access Code" required>
            <br><br>
            <button type="submit">Access</button>
        </form>
    </body>
    </html>
    '''

@app.route('/ops/logout')
def logout():
    session.pop('authenticated', None)
    return redirect('/ops')

@app.route('/')
def home():
    """Public landing page"""
    return '''
    <html>
    <head><title>Nate's Site</title></head>
    <body style="font-family: Arial; text-align: center; padding: 50px;">
        <h1>Welcome to Nate's Site</h1>
        <p>Professional website coming soon...</p>
    </body>
    </html>
    '''

# API endpoint to get all products
@app.route('/api/products')
def get_products():
    """Get all products for the grid interface"""
    config = load_print_config()
    return jsonify(config["products"])

# API endpoint to get available paper sizes
@app.route('/api/paper-sizes')
def get_paper_sizes():
    """Get available paper sizes for dropdowns"""
    return jsonify(PAPER_SIZES)

# Shipping label lookup endpoint
@app.route('/api/shipping-label/<tracking_number>')
def get_shipping_label(tracking_number):
    """Get shipping label by tracking number from ShipStation"""
    try:
        # Make request to ShipStation API
        headers = {
            'api-key': SHIPSTATION_API_KEY
        }
        
        response = requests.get(
            f"{SHIPSTATION_URL}?trackingNumber={tracking_number}",
            headers=headers
        )
        
        if response.status_code == 404:
            return jsonify({"error": "Tracking number not found"}), 404
        
        response.raise_for_status()
        data = response.json()
        
        if not data.get('labels') or len(data['labels']) == 0:
            return jsonify({"error": "No labels found for this tracking number"}), 404
        
        label = data['labels'][0]  # Get the first label
        
        # Extract relevant information
        label_info = {
            "tracking_number": label.get("tracking_number"),
            "status": label.get("status"),
            "carrier_code": label.get("carrier_code"),
            "service_code": label.get("service_code"),
            "ship_date": label.get("ship_date"),
            "pdf_url": label.get("label_download", {}).get("pdf"),
            "png_url": label.get("label_download", {}).get("png"),
            "ship_to": label.get("ship_to", {}),
            "tracking_status": label.get("tracking_status"),
            "label_layout": label.get("label_layout")
        }
        
        return jsonify({
            "success": True,
            "label": label_info
        })
        
    except requests.HTTPError as http_err:
        return jsonify({
            "error": f"ShipStation API error: {http_err}",
            "details": response.text if 'response' in locals() else "No response details"
        }), 500
    except Exception as err:
        return jsonify({
            "error": f"An error occurred: {str(err)}"
        }), 500

# Print shipping label endpoint
@app.route('/api/print-shipping-label', methods=['POST'])
def print_shipping_label():
    """Print a shipping label from PDF URL"""
    try:
        data = request.get_json()
        pdf_url = data.get('pdf_url')
        tracking_number = data.get('tracking_number', 'Unknown')
        quantity = int(data.get('quantity', 1))
        
        if not pdf_url:
            return jsonify({"error": "PDF URL is required"}), 400
        
        # Download the PDF from ShipStation
        pdf_response = requests.get(pdf_url)
        pdf_response.raise_for_status()
        
        # Encode PDF to base64
        pdf_base64 = base64.b64encode(pdf_response.content).decode("utf-8")
        
        # Use sticker printer for shipping labels
        printer_id = STICKER_PRINTER_ID
        
        # Create the payload for the print job request
        payload = {
            "printerId": printer_id,
            "title": f"Shipping Label - {tracking_number} ({quantity} copies)",
            "contentType": "pdf_base64",
            "content": pdf_base64,
            "source": "Warehouse Interface - Shipping Label",
            "options": {"copies": quantity}
        }
        
        # Send the POST request to PrintNode
        response = requests.post(
            PRINTNODE_URL,
            auth=(PRINTNODE_API_KEY, ""),
            headers={"Content-Type": "application/json"},
            json=payload
        )
        response.raise_for_status()
        
        return jsonify({
            "success": True,
            "message": f"Shipping label printed: {quantity}x {tracking_number}",
            "tracking_number": tracking_number,
            "copies": quantity,
            "printnode_response": response.json()
        })
        
    except requests.HTTPError as http_err:
        return jsonify({
            "error": f"HTTP error occurred: {http_err}",
            "details": response.text if 'response' in locals() else "No response details"
        }), 500
    except Exception as err:
        return jsonify({
            "error": f"An error occurred: {str(err)}"
        }), 500

# Print endpoint with dynamic quantity and paper size support
@app.route('/api/print/<product_id>')
def print_product(product_id):
    """Print a specific product by ID with specified quantity and paper size"""
    try:
        # Get quantity from query parameter
        quantity = int(request.args.get('quantity', 1))
        
        config = load_print_config()
        
        # Find the product
        product = None
        for p in config["products"]:
            if p["id"] == product_id:
                product = p
                break
        
        if not product:
            return jsonify({"error": f"Product not found: {product_id}"}), 404
        
        # Check if PDF file exists (try both relative path and pdfs folder)
        pdf_file_path = product["pdf_file"]
        if not os.path.exists(pdf_file_path):
            # Try in pdfs folder
            pdf_file_path = os.path.join(PDF_FOLDER, product["pdf_file"])
            if not os.path.exists(pdf_file_path):
                return jsonify({"error": f"PDF file not found: {product['pdf_file']}"}), 404
        
        # Read and encode the PDF file
        try:
            with open(pdf_file_path, "rb") as pdf_file:
                pdf_bytes = pdf_file.read()
                pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        except Exception as e:
            return jsonify({"error": f"Failed to read PDF file: {str(e)}"}), 500
        
        # Build print options including paper size
        options = build_print_options(product, quantity)
        
        # Create the payload for the print job request
        payload = {
            "printerId": product["printer_id"],
            "title": f"{product['name']} - Warehouse Print ({quantity} copies)",
            "contentType": "pdf_base64",
            "content": pdf_base64,
            "source": "Warehouse Interface",
            "options": options
        }
        
        # Send the POST request to PrintNode
        response = requests.post(
            PRINTNODE_URL,
            auth=(PRINTNODE_API_KEY, ""),
            headers={"Content-Type": "application/json"},
            json=payload
        )
        response.raise_for_status()
        
        # Build response message
        message = f"Print job submitted: {quantity}x {product['name']}"
        if product.get("paper_size") and product["paper_size"].strip():
            message += f" (Paper: {product['paper_size']})"
        
        return jsonify({
            "success": True,
            "message": message,
            "product": product["name"],
            "copies": quantity,
            "paper_size": product.get("paper_size", ""),
            "printnode_response": response.json()
        })
        
    except requests.HTTPError as http_err:
        return jsonify({
            "error": f"HTTP error occurred: {http_err}",
            "details": response.text if 'response' in locals() else "No response details"
        }), 500
    except Exception as err:
        return jsonify({
            "error": f"An error occurred: {str(err)}"
        }), 500

# Batch processing endpoints
@app.route('/api/batch/<batch_number>')
def get_batch_info(batch_number):
    """Get batch information by batch number"""
    try:
        headers = {
            'api-key': SHIPSTATION_API_KEY
        }
        
        response = requests.get(
            f"https://api.shipstation.com/v2/batches?batch_number={batch_number}",
            headers=headers
        )
        
        if response.status_code == 404:
            return jsonify({"error": "Batch number not found"}), 404
        
        response.raise_for_status()
        data = response.json()
        
        if not data.get('batches') or len(data['batches']) == 0:
            return jsonify({"error": "No batch found for this number"}), 404
        
        batch = data['batches'][0]
        
        return jsonify({
            "success": True,
            "batch": {
                "batch_id": batch.get("batch_id"),
                "batch_number": batch.get("batch_number"),
                "batch_notes": batch.get("batch_notes"),
                "completed": batch.get("completed"),
                "count": batch.get("count"),
                "status": batch.get("status"),
                "created_at": batch.get("created_at"),
                "batch_labels_url": batch.get("batch_labels_url", {}).get("href")
            }
        })
        
    except requests.HTTPError as http_err:
        return jsonify({
            "error": f"ShipStation API error: {http_err}",
            "details": response.text if 'response' in locals() else "No response details"
        }), 500
    except Exception as err:
        return jsonify({
            "error": f"An error occurred: {str(err)}"
        }), 500

@app.route('/api/batch/<batch_id>/labels')
def get_batch_labels(batch_id):
    """Get all labels in a batch"""
    try:
        headers = {
            'api-key': SHIPSTATION_API_KEY
        }
        
        response = requests.get(
            f"https://api.shipstation.com/v2/labels?batch_id={batch_id}",
            headers=headers
        )
        
        response.raise_for_status()
        data = response.json()
        
        labels = []
        for label in data.get('labels', []):
            labels.append({
                "label_id": label.get("label_id"),
                "tracking_number": label.get("tracking_number"),
                "status": label.get("status"),
                "carrier_code": label.get("carrier_code"),
                "service_code": label.get("service_code"),
                "pdf_url": label.get("label_download", {}).get("pdf"),
                "ship_to": {
                    "name": label.get("ship_to", {}).get("name"),
                    "address_line1": label.get("ship_to", {}).get("address_line1"),
                    "city_locality": label.get("ship_to", {}).get("city_locality"),
                    "state_province": label.get("ship_to", {}).get("state_province"),
                    "postal_code": label.get("ship_to", {}).get("postal_code")
                }
            })
        
        return jsonify({
            "success": True,
            "total_labels": len(labels),
            "labels": labels
        })
        
    except requests.HTTPError as http_err:
        return jsonify({
            "error": f"ShipStation API error: {http_err}",
            "details": response.text if 'response' in locals() else "No response details"
        }), 500
    except Exception as err:
        return jsonify({
            "error": f"An error occurred: {str(err)}"
        }), 500

@app.route('/api/printnode/job/<job_id>/status')
def check_print_job_status(job_id):
    """Check the status of a PrintNode print job"""
    try:
        response = requests.get(
            f"https://api.printnode.com/printjobs/{job_id}",
            auth=(PRINTNODE_API_KEY, "")
        )
        
        response.raise_for_status()
        job_data = response.json()
        
        return jsonify({
            "success": True,
            "job_id": job_id,
            "state": job_data.get("state"),
            "printer_id": job_data.get("printer", {}).get("id"),
            "title": job_data.get("title"),
            "created_at": job_data.get("createTimestamp"),
            "is_complete": job_data.get("state") in ["done", "error"]
        })
        
    except requests.HTTPError as http_err:
        return jsonify({
            "error": f"PrintNode API error: {http_err}",
            "details": response.text if 'response' in locals() else "No response details"
        }), 500
    except Exception as err:
        return jsonify({
            "error": f"An error occurred: {str(err)}"
        }), 500

@app.route('/api/batch/print-label', methods=['POST'])
def print_batch_label():
    """Print a single label from batch data"""
    try:
        data = request.get_json()
        pdf_url = data.get('pdf_url')
        tracking_number = data.get('tracking_number', 'Unknown')
        label_id = data.get('label_id', 'Unknown')
        
        if not pdf_url:
            return jsonify({"error": "PDF URL is required"}), 400
        
        # Download the PDF from ShipStation
        pdf_response = requests.get(pdf_url)
        pdf_response.raise_for_status()
        
        # Encode PDF to base64
        pdf_base64 = base64.b64encode(pdf_response.content).decode("utf-8")
        
        # Use the batch label printer
        printer_id = STICKER_PRINTER_ID
        
        # Create the payload for the print job request
        payload = {
            "printerId": printer_id,
            "title": f"Batch Label - {tracking_number}",
            "contentType": "pdf_base64",
            "content": pdf_base64,
            "source": "Warehouse Interface - Batch Print",
            "options": {"copies": 1}
        }
        
        # Send the POST request to PrintNode
        response = requests.post(
            PRINTNODE_URL,
            auth=(PRINTNODE_API_KEY, ""),
            headers={"Content-Type": "application/json"},
            json=payload
        )
        response.raise_for_status()
        
        printnode_response = response.json()
        job_id = printnode_response  # PrintNode returns the job ID directly
        
        return jsonify({
            "success": True,
            "message": f"Batch label printed: {tracking_number}",
            "label_id": label_id,
            "tracking_number": tracking_number,
            "job_id": job_id,
            "printer_id": printer_id
        })
        
    except requests.HTTPError as http_err:
        return jsonify({
            "error": f"HTTP error occurred: {http_err}",
            "details": response.text if 'response' in locals() else "No response details"
        }), 500
    except Exception as err:
        return jsonify({
            "error": f"An error occurred: {str(err)}"
        }), 500

@app.route('/api/batch/process', methods=['POST'])
def process_batch():
    """Process an entire batch with print confirmation between each label"""
    try:
        data = request.get_json()
        batch_number = data.get('batch_number')
        
        if not batch_number:
            return jsonify({"error": "Batch number is required"}), 400
        
        # Step 1: Get batch info
        batch_response = get_batch_info(batch_number)
        if batch_response[1] != 200:  # Check if there was an error
            return batch_response
        
        batch_data = batch_response[0].get_json()
        batch_id = batch_data["batch"]["batch_id"]
        batch_notes = batch_data["batch"]["batch_notes"]
        print(batch_notes)
        
        # Step 2: Get all labels in batch
        labels_response = get_batch_labels(batch_id)
        if labels_response[1] != 200:
            return labels_response
        
        labels_data = labels_response[0].get_json()
        labels = labels_data["labels"]
        
        if not labels:
            return jsonify({"error": "No labels found in batch"}), 404
        
        # Step 3: Process each label with confirmation
        results = []
        successful_prints = 0
        failed_prints = 0
        
        for i, label in enumerate(labels):
            try:
                # Print the label
                print_data = {
                    "pdf_url": label["pdf_url"],
                    "tracking_number": label["tracking_number"],
                    "label_id": label["label_id"]
                }
                
                print_response = requests.post(
                    request.url_root + "api/batch/print-label",
                    json=print_data,
                    headers={"Content-Type": "application/json"}
                )
                
                if print_response.status_code == 200:
                    print_result = print_response.json()
                    job_id = print_result.get("job_id")
                    
                    # Wait for confirmation (poll job status)
                    max_wait_time = 30  # seconds
                    poll_interval = 2  # seconds
                    elapsed_time = 0
                    job_completed = False
                    
                    while elapsed_time < max_wait_time:
                        time.sleep(poll_interval)
                        elapsed_time += poll_interval
                        
                        # Check job status
                        status_response = requests.get(
                            request.url_root + f"api/printnode/job/{job_id}/status"
                        )
                        
                        if status_response.status_code == 200:
                            status_data = status_response.json()
                            if status_data.get("is_complete"):
                                job_completed = True
                                job_state = status_data.get("state")
                                break
                    
                    result = {
                        "label_index": i + 1,
                        "label_id": label["label_id"],
                        "tracking_number": label["tracking_number"],
                        "print_success": job_completed and job_state == "done",
                        "job_id": job_id,
                        "job_state": job_state if job_completed else "timeout",
                        "wait_time": elapsed_time
                    }
                    
                    if result["print_success"]:
                        successful_prints += 1
                    else:
                        failed_prints += 1
                        
                else:
                    result = {
                        "label_index": i + 1,
                        "label_id": label["label_id"],
                        "tracking_number": label["tracking_number"],
                        "print_success": False,
                        "error": print_response.text,
                        "job_id": None
                    }
                    failed_prints += 1
                
                results.append(result)
                
            except Exception as label_error:
                result = {
                    "label_index": i + 1,
                    "label_id": label["label_id"],
                    "tracking_number": label["tracking_number"],
                    "print_success": False,
                    "error": str(label_error),
                    "job_id": None
                }
                results.append(result)
                failed_prints += 1
        
        return jsonify({
            "success": True,
            "batch_number": batch_number,
            "batch_id": batch_id,
            "batch_notes": batch_notes,
            "total_labels": len(labels),
            "successful_prints": successful_prints,
            "failed_prints": failed_prints,
            "processing_complete": True,
            "results": results,
            "summary": f"Batch {batch_number}: {successful_prints}/{len(labels)} labels printed successfully"
        })
        
    except Exception as err:
        return jsonify({
            "error": f"Batch processing failed: {str(err)}"
        }), 500

# Upload PDF with automatic thumbnail generation
@app.route('/api/upload-pdf', methods=['POST'])
def upload_pdf():
    """Upload a PDF file and generate thumbnail"""
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "Only PDF files are allowed"}), 400
    
    try:
        # Generate unique filename
        original_filename = file.filename
        file_extension = '.pdf'
        unique_filename = f"{uuid.uuid4().hex}{file_extension}"
        
        # Save PDF to pdfs folder
        pdf_path = os.path.join(PDF_FOLDER, unique_filename)
        file.save(pdf_path)
        
        # Generate thumbnail
        thumbnail_filename = f"{uuid.uuid4().hex}.jpg"
        thumbnail_path = os.path.join(IMAGES_FOLDER, thumbnail_filename)
        
        if generate_pdf_thumbnail(pdf_path, thumbnail_path):
            return jsonify({
                "success": True,
                "message": "PDF uploaded and thumbnail generated",
                "pdf_filename": unique_filename,
                "thumbnail_filename": thumbnail_filename,
                "original_filename": original_filename,
                "pdf_url": f"/pdfs/{unique_filename}",
                "thumbnail_url": f"/static/images/{thumbnail_filename}"
            })
        else:
            # If thumbnail generation fails, still return success for PDF upload
            return jsonify({
                "success": True,
                "message": "PDF uploaded (thumbnail generation failed)",
                "pdf_filename": unique_filename,
                "thumbnail_filename": "",
                "original_filename": original_filename,
                "pdf_url": f"/pdfs/{unique_filename}",
                "thumbnail_url": ""
            })
            
    except Exception as e:
        return jsonify({"error": f"Upload failed: {str(e)}"}), 500

# Serve PDF files
@app.route('/pdfs/<filename>')
def serve_pdf(filename):
    return send_from_directory(PDF_FOLDER, filename)

# Configuration management endpoints
@app.route('/ops/admin')
def admin_interface():
    """Protected admin interface for managing products"""
    if not check_auth():
        return redirect('/ops')
    return render_template('admin.html')

@app.route('/api/products', methods=['POST'])
def add_product():
    """Add a new product"""
    try:
        data = request.get_json()
        required_fields = ['id', 'name', 'pdf_file', 'printer_id']
        
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        config = load_print_config()
        
        # Check if product ID already exists
        for product in config["products"]:
            if product["id"] == data["id"]:
                return jsonify({"error": f"Product ID already exists: {data['id']}"}), 400
        
        # Add new product with paper size support
        new_product = {
            "id": data["id"],
            "name": data["name"],
            "description": data.get("description", ""),
            "pdf_file": data["pdf_file"],
            "image": data.get("image", ""),
            "printer_id": int(data["printer_id"]),
            "category": data.get("category", "General"),
            "paper_size": data.get("paper_size", "")  # Default to empty string (printer default)
        }
        
        config["products"].append(new_product)
        
        if save_print_config(config):
            return jsonify({
                "success": True,
                "message": "Product added successfully",
                "product": new_product
            })
        else:
            return jsonify({"error": "Failed to save configuration"}), 500
            
    except Exception as err:
        return jsonify({"error": f"An error occurred: {str(err)}"}), 500

@app.route('/api/products/<product_id>', methods=['PUT'])
def update_product(product_id):
    """Update an existing product"""
    try:
        data = request.get_json()
        config = load_print_config()
        
        # Find and update the product
        product_found = False
        for i, product in enumerate(config["products"]):
            if product["id"] == product_id:
                # Update fields including paper_size
                for key, value in data.items():
                    if key == "printer_id":
                        config["products"][i][key] = int(value)
                    else:
                        config["products"][i][key] = value
                        
                # Ensure paper_size exists (for backwards compatibility)
                if "paper_size" not in config["products"][i]:
                    config["products"][i]["paper_size"] = ""
                    
                product_found = True
                break
        
        if not product_found:
            return jsonify({"error": f"Product not found: {product_id}"}), 404
        
        if save_print_config(config):
            return jsonify({
                "success": True,
                "message": "Product updated successfully"
            })
        else:
            return jsonify({"error": "Failed to save configuration"}), 500
            
    except Exception as err:
        return jsonify({"error": f"An error occurred: {str(err)}"}), 500

@app.route('/api/products/<product_id>', methods=['DELETE'])
def delete_product(product_id):
    """Delete a product"""
    try:
        config = load_print_config()
        
        # Find and remove the product
        config["products"] = [p for p in config["products"] if p["id"] != product_id]
        
        if save_print_config(config):
            return jsonify({
                "success": True,
                "message": "Product deleted successfully"
            })
        else:
            return jsonify({"error": "Failed to save configuration"}), 500
            
    except Exception as err:
        return jsonify({"error": f"An error occurred: {str(err)}"}), 500

# Serve static files
@app.route('/static/images/<filename>')
def serve_image(filename):
    return send_from_directory(IMAGES_FOLDER, filename)

# Keep existing upload functionality for compatibility
@app.route('/upload')
def upload_page():
    return render_template('dropzone.html')

@app.route('/upload-temp', methods=['POST'])
def upload_temp():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed. Please upload PDF, PNG, or JPG files."}), 400
    
    file_extension = file.filename.rsplit('.', 1)[1].lower()
    unique_filename = f"{uuid.uuid4().hex}.{file_extension}"
    
    file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
    file.save(file_path)
    
    file_url = url_for('uploaded_file', filename=unique_filename)
    
    return jsonify({
        "message": "File uploaded successfully",
        "filename": unique_filename,
        "fileUrl": file_url,
        "fileType": file_extension
    })

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/cleanup', methods=['POST'])
def cleanup_files():
    try:
        file_count = 0
        for filename in os.listdir(UPLOAD_FOLDER):
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                file_count += 1
        
        return jsonify({
            "message": f"Cleanup completed. {file_count} files removed."
        })
    except Exception as e:
        return jsonify({"error": f"Cleanup failed: {str(e)}"}), 500

if __name__ == '__main__':
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)