from flask import Flask, jsonify, request, render_template, url_for, send_from_directory, session, redirect, send_file, abort
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
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter, Transformation
import hashlib
import psycopg2
from authlib.integrations.flask_client import OAuth
from flask_login import (
    LoginManager, UserMixin,
    login_user, login_required,
    logout_user, current_user
)
from functools import wraps
from hazmat_classes import ShipStationManager
from routes.shipstation_bp import shipstation_bp
from routes.ups_bp         import ups_bp
from routes.fedex_bp       import fedex_bp
from routes.shopify_gmail_bp import shopify_gmail_bp
from werkzeug.middleware.proxy_fix import ProxyFix



# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config['PREFERRED_URL_SCHEME'] = 'https'

app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here')

app.permanent_session_lifetime = timedelta(days=7)

print('loading')

shipstation = ShipStationManager(logger=app.logger)


def login_required_custom(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Flask-Login setup
login_manager = LoginManager(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, sub, email):
        self.id = sub
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    info = session.get("user")
    if info and "sub" in info and "email" in info and info["sub"] == user_id:
        return User(info["sub"], info["email"])
    return None

# OAuth setup
oauth = OAuth(app)
google = oauth.register(
  name="google",
  client_id=os.getenv("GOOGLE_CLIENT_ID"),
  client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
  server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
  client_kwargs={
    "scope": "openid email profile https://www.googleapis.com/auth/gmail.readonly"
  },
  access_token_params={
    "access_type": "offline",
    "prompt": "consent"
  }
)

app.register_blueprint(shipstation_bp)   # mounts at /search
app.register_blueprint(ups_bp)           # mounts at /ups
app.register_blueprint(fedex_bp)         # mounts at /fedex
app.register_blueprint(shopify_gmail_bp, url_prefix='/website-cs')


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
    "5 x 7", "6.5 x 10", "5in x 18.75 continuous", "5 x continuous"
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


def get_device_fingerprint():
    """Create a device fingerprint from browser characteristics"""
    user_agent = request.headers.get('User-Agent', '')
    accept_language = request.headers.get('Accept-Language', '')
    accept_encoding = request.headers.get('Accept-Encoding', '')
    
    # Combine multiple headers for better uniqueness
    fingerprint_data = f"{user_agent}|{accept_language}|{accept_encoding}"
    fingerprint = hashlib.md5(fingerprint_data.encode()).hexdigest()
    
    # Console log for debugging (you'll see this in your server logs)
    print(f"Device Fingerprint: {fingerprint}")
    print(f"User-Agent: {user_agent}")
    
    return fingerprint

def is_whitelisted_device():
    """Check if current device is in the whitelist"""
    device_id = get_device_fingerprint()
    
    # Get whitelisted devices from environment variable
    whitelist_env = os.getenv('WHITELISTED_DEVICES', '')
    whitelisted_devices = [device.strip() for device in whitelist_env.split(',') if device.strip()]
    
    print(f"🔍 Checking device: {device_id}")
    print(f"📋 Whitelist: {whitelisted_devices}")
    
    return device_id in whitelisted_devices


def is_authorized():
    """Check if request is authorized via device fingerprint or city location"""
    try:
        # Step 1: Check device fingerprint whitelist
        if is_whitelisted_device():
            print(f"✅ Authorized device: {get_device_fingerprint()}")
            return True
        
        # Step 2: Check if in correct city (zip 08094)
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in ip:
            ip = ip.split(',')[0].strip()
        
        # Skip local/private IPs during development
        if ip in ['127.0.0.1', 'localhost'] or ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.'):
            print("✅ Local/private IP - allowing access")
            return True
            
        # Use geolocation API to check city/zip
        response = requests.get(f'http://ip-api.com/json/{ip}', timeout=3)
        data = response.json()
        
        city = data.get('city', '').lower()
        zip_code = data.get('zip', '')
        region = data.get('region', '')
        
        print(f"🌍 Location check - City: {city}, Zip: {zip_code}, State: {region}")
        
        # Check for your specific area (08094 is Glassboro, NJ)
        if (zip_code == '08094' or 
            city in ['glassboro', 'sewell', 'washington township'] or
            (region == 'New Jersey' and city in ['glassboro'])):
            print("✅ Authorized by location")
            return True
        
        print(f"❌ Location not authorized - IP: {ip}")
        return False
        
    except Exception as e:
        print(f"⚠️ Auth check error: {e} - allowing access")
        # On error, allow access (fail open)
        return True


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
#new
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
    if not is_authorized():
        abort(403)
        
    password = request.form.get('password')
    if password == os.getenv('WAREHOUSE_PASSWORD', 'warehouse123'):
        session['authenticated'] = True
        return redirect('/ops/warehouse')
    return redirect('/ops?error=1')

@app.route('/ops/warehouse')
def warehouse_interface():
    """Protected warehouse printing interface"""
    if not is_authorized():
        abort(403)
    if not check_auth():
        return redirect('/ops')
    return render_template('warehouse.html')

@app.route('/ops/batch')
def batch_interface():
    """Protected batch printing interface"""
    if not is_authorized():
        abort(403)
    if not check_auth():
        return redirect('/ops')
    return render_template('batch.html')

@app.route('/ops')
def ops_login():
    if not is_authorized():
        abort(403)
        
    print('loading login')
    """Login page for warehouse ops"""
    
    # If whitelisted device, skip password and go straight to warehouse
    if is_whitelisted_device():
        session['authenticated'] = True
        return redirect('/ops/warehouse')
    
    # Otherwise, check if already authenticated
    if check_auth():
        return redirect('/ops/warehouse')
    
    error_msg = '❌ Invalid access code' if request.args.get('error') else ''
    
    return f'''
    <html>
    <head>
        <title>Warehouse Login</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                margin: 0; padding: 0; height: 100vh;
                display: flex; align-items: center; justify-content: center;
            }}
            .container {{
                background: white; padding: 40px; border-radius: 16px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.1);
                text-align: center; min-width: 300px;
            }}
            h2 {{ color: #333; margin-bottom: 30px; }}
            input {{
                width: 100%; padding: 12px; border: 2px solid #e1e5e9;
                border-radius: 8px; font-size: 16px; margin-bottom: 20px;
                box-sizing: border-box;
            }}
            input:focus {{
                outline: none; border-color: #667eea;
            }}
            button {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white; border: none; padding: 12px 30px;
                border-radius: 8px; font-size: 16px; cursor: pointer;
                width: 100%; transition: transform 0.2s;
            }}
            button:hover {{ transform: translateY(-2px); }}
            .error {{ color: #e74c3c; margin-bottom: 15px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>🏭 Warehouse Operations</h2>
            {f'<div class="error">{error_msg}</div>' if error_msg else ''}
            <form method="post" action="/ops/auth">
                <input type="password" name="password" placeholder="Enter Access Code" required>
                <button type="submit">Access System</button>
            </form>
        </div>
    </body>
    </html>
    '''

# Debug route to see device fingerprint and location
@app.route('/ops/debug')
def debug_info():
    """Debug route to see device fingerprint and location info"""
    device_id = get_device_fingerprint()
    
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ',' in ip:
        ip = ip.split(',')[0].strip()
    
    try:
        response = requests.get(f'http://ip-api.com/json/{ip}', timeout=3)
        location_data = response.json()
    except:
        location_data = {"error": "Could not fetch location"}
    
    return f'''
    <html>
    <body style="font-family: Arial; padding: 20px;">
        <h2>Debug Info</h2>
        <p><strong>Device Fingerprint:</strong> <code>{device_id}</code></p>
        <p><strong>Your IP:</strong> {ip}</p>
        <p><strong>Location Data:</strong></p>
        <pre>{location_data}</pre>
        <hr>
        <p>Copy your device fingerprint and add it to the whitelist!</p>
        <a href="/ops">← Back to Ops</a>
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
    <head>
        <title>Nate's Site</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
                margin: 0; padding: 0; height: 100vh;
                display: flex; align-items: center; justify-content: center;
                color: white;
            }
            .container {
                text-align: center; max-width: 500px; padding: 40px;
            }
            h1 {
                font-size: 3em; margin-bottom: 20px;
                background: linear-gradient(45deg, #fff, #a8edea);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }
            p { font-size: 1.2em; margin-bottom: 40px; opacity: 0.9; }
            .go-btn {
                display: inline-block;
                background: linear-gradient(135deg, #ff6b6b 0%, #ee5a52 100%);
                color: white; text-decoration: none;
                padding: 15px 40px; border-radius: 50px;
                font-size: 18px; font-weight: 600;
                transition: all 0.3s ease;
                box-shadow: 0 10px 30px rgba(255, 107, 107, 0.3);
            }
            .go-btn:hover {
                transform: translateY(-3px);
                box-shadow: 0 15px 40px rgba(255, 107, 107, 0.4);
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Nate's Site</h1>
            <p>Professional website coming soon...</p>
            <a href="/ops" class="go-btn">GO →</a>
        </div>
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

@app.route('/api/sign-pdf', methods=['POST'])
def sign_pdf():
    """Sign a PDF with predefined signature positions"""
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        
        uploaded_file = request.files['file']
        
        if uploaded_file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        if not uploaded_file.filename.lower().endswith('.pdf'):
            return jsonify({"error": "Only PDF files are allowed"}), 400
        
        # Read uploaded PDF
        reader = PdfReader(uploaded_file)
        first = reader.pages[0]
        w = float(first.mediabox.width)
        h = float(first.mediabox.height)
        
        # Signature positions (in inches) → points
        spots_in = [(0.80, 7.97), (4.7, 7.97), (0.80, 4.55)]
        spots_pt = [(x*72, y*72) for x, y in spots_in]
        
        # Build overlay
        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=(w, h))
        c.setFont("Helvetica", 10)
        for x, y in spots_pt:
            c.drawString(x, y, "Tim Marker")
        c.save()
        packet.seek(0)
        overlay = PdfReader(packet).pages[0]
        
        # Optional shift (adjust as needed)
        shift_x = 0.25 * 28.35  # 0.25 cm in points
        transform = Transformation().translate(tx=shift_x, ty=0)
        
        # Merge onto every page
        writer = PdfWriter()
        for page in reader.pages:
            page.merge_page(overlay)
            writer.add_page(page)
        
        # Return signed PDF
        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        
        return send_file(
            output,
            as_attachment=True,
            download_name=f"signed_{uploaded_file.filename}",
            mimetype="application/pdf"
        )
        
    except Exception as err:
        return jsonify({"error": f"PDF signing failed: {str(err)}"}), 500
    
#shipstation v1 what's this
@app.route('/ops/whats-this')
def whats_this_interface():
    """Protected shipment lookup interface"""
    if not is_authorized():
        abort(403)
    if not check_auth():
        return redirect('/ops')
    return render_template('whats_this.html')

#orders_route

@app.route('/api/06611904-26f5-4d21-bf72-3cb888359b84', methods=['POST'])
def orders_route():
    print("=== INCOMING SHIPSTATION WEBHOOK ===")
    
    try:
        # Get the webhook payload
        webhook_data = request.get_json()
        resource_url = webhook_data.get('resource_url')
        
        if not resource_url:
            return jsonify({"error": "No resource_url in webhook"}), 400
        
        print(f"Fetching orders from: {resource_url}")
        
        # Fetch the actual order data from ShipStation
        headers = {
            'content-type': 'application/json',
            'Authorization': f"Basic {os.getenv('SHIPSTATION_V1_API_KEY')}"
        }
        response = requests.get(resource_url, headers=headers)
        orders_data = response.json()
        
        print(f"Got {orders_data.get('total', 0)} orders")
        
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', 5432),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            sslmode='require'
        )
        cursor = conn.cursor()
        
        inserted_count = 0
        
        # Insert each order as a separate row
        for order in orders_data.get('orders', []):
            try:
                cursor.execute(
                    "INSERT INTO shipstation_orders_raw (payload) VALUES (%s)",
                    (json.dumps(order),)
                )
                inserted_count += 1
                print(f"Inserted order {order.get('orderNumber', 'unknown')}")
            except Exception as e:
                print(f"Error inserting order {order.get('orderNumber', 'unknown')}: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"Successfully inserted {inserted_count} orders")
        
        return jsonify({
            "status": "success",
            "orders_processed": inserted_count,
            "total_orders": orders_data.get('total', 0)
        })
        
    except Exception as e:
        print(f"Error processing webhook: {e}")
        return jsonify({"error": str(e)}), 500
    
#shipm,ents route (v1)
@app.route('/api/40be2d89-d69d-44eb-954f-f099d0138082', methods=['POST'])
def shipments_route():
    print("=== INCOMING SHIPSTATION WEBHOOK ===")
    
    try:
        # Get the webhook payload
        webhook_data = request.get_json()
        resource_url = webhook_data.get('resource_url')
        
        if not resource_url:
            return jsonify({"error": "No resource_url in webhook"}), 400
        
        print(f"Fetching orders from: {resource_url}")
        
        # Fetch the actual order data from ShipStation
        headers = {
            'content-type': 'application/json',
            'Authorization': f"Basic {os.getenv('SHIPSTATION_V1_API_KEY')}"
        }
        response = requests.get(resource_url, headers=headers)
        orders_data = response.json()
        
        print(f"Got {orders_data.get('total', 0)} orders")
        
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', 5432),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            sslmode='require'
        )
        cursor = conn.cursor()
        
        inserted_count = 0
        
        # Insert each order as a separate row
        for order in orders_data.get('shipments', []):
            try:
                cursor.execute(
                    "INSERT INTO shipstation_shipments_raw (payload) VALUES (%s)",
                    (json.dumps(order),)
                )
                inserted_count += 1
                print(f"Inserted order {order.get('orderNumber', 'unknown')}")
            except Exception as e:
                print(f"Error inserting order {order.get('orderNumber', 'unknown')}: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"Successfully inserted {inserted_count} orders")
        
        return jsonify({
            "status": "success",
            "orders_processed": inserted_count,
            "total_orders": orders_data.get('total', 0)
        })
        
    except Exception as e:
        print(f"Error processing webhook: {e}")
        return jsonify({"error": str(e)}), 500
        
 #fulfillments route (v1)
@app.route('/api/dff0863d-5302-4ecd-a9ad-baabf68e8546', methods=['POST'])
def fulfillments_route():
    print("=== INCOMING SHIPSTATION WEBHOOK ===")
    
    try:
        # Get the webhook payload
        webhook_data = request.get_json()
        resource_url = webhook_data.get('resource_url')
        
        if not resource_url:
            return jsonify({"error": "No resource_url in webhook"}), 400
        
        print(f"Fetching orders from: {resource_url}")
        
        # Fetch the actual order data from ShipStation
        headers = {
            'content-type': 'application/json',
            'Authorization': f"Basic {os.getenv('SHIPSTATION_V1_API_KEY')}"
        }
        response = requests.get(resource_url, headers=headers)
        orders_data = response.json()
        
        print(f"Got {orders_data.get('total', 0)} orders")
        
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', 5432),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            sslmode='require'
        )
        cursor = conn.cursor()
        
        inserted_count = 0
        
        # Insert each order as a separate row
        for order in orders_data.get('fulfillments', []):
            try:
                cursor.execute(
                    "INSERT INTO shipstation_shipments_raw (payload, type) VALUES (%s, %s)",
                    (json.dumps(order), "f")
                )
                inserted_count += 1
                print(f"Inserted order {order.get('orderNumber', 'unknown')}")
            except Exception as e:
                print(f"Error inserting order {order.get('orderNumber', 'unknown')}: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"Successfully inserted {inserted_count} orders")
        
        return jsonify({
            "status": "success",
            "orders_processed": inserted_count,
            "total_orders": orders_data.get('total', 0)
        })
        
    except Exception as e:
        print(f"Error processing webhook: {e}")
        return jsonify({"error": str(e)}), 500
    
       
#shipstation v1 routes
@app.route('/api/shipment/<tracking_number>')
def get_shipment_info(tracking_number):
    """Get shipment information by tracking number from ShipStation v1 API"""
    try:
        # Get pre-encoded Basic Auth token from environment
        shipstation_v1_auth = os.getenv('SHIPSTATION_V1_API_KEY')
        
        if not shipstation_v1_auth:
            return jsonify({"error": "ShipStation v1 API credentials not configured"}), 500
        
        # Make request to ShipStation v1 Shipments API
        response = requests.get(
            f"https://ssapi.shipstation.com/shipments?trackingNumber={tracking_number}&includeShipmentItems=true",
            headers={
                "Authorization": f"Basic {shipstation_v1_auth}",
                "Content-Type": "application/json"
            }
        )
        
        if response.status_code == 404:
            return jsonify({"error": "Tracking number not found"}), 404
        
        response.raise_for_status()
        data = response.json()
        
        # Debug logging
        print(f"ShipStation API Response Status: {response.status_code}")
        print(f"ShipStation API Response Data: {data}")
        
        # Handle different response structures
        shipments = data.get('shipments', [])
        if not shipments:
            # Try alternative structure
            if 'shipment' in data:
                shipments = [data['shipment']]
            elif isinstance(data, list):
                shipments = data
            else:
                return jsonify({"error": "No shipments found for this tracking number"}), 404
        
        if len(shipments) == 0:
            return jsonify({"error": "No shipments found for this tracking number"}), 404
            
        shipment = shipments[0]  # Get the first shipment
        
        # Extract relevant information from v1 API response
        shipment_info = {
            "shipment_id": shipment.get("shipmentId"),
            "order_id": shipment.get("orderId"),
            "order_key": shipment.get("orderKey"),
            "order_number": shipment.get("orderNumber"),
            "tracking_number": shipment.get("trackingNumber"),
            "carrier_code": shipment.get("carrierCode"),
            "service_code": shipment.get("serviceCode"),
            "ship_date": shipment.get("shipDate"),
            "delivery_date": shipment.get("deliveryDate"),
            "void_date": shipment.get("voidDate"),
            "voided": shipment.get("voided"),
            
            # Customer info
            "ship_to": {
                "name": shipment.get("shipTo", {}).get("name"),
                "company": shipment.get("shipTo", {}).get("company"),
                "street1": shipment.get("shipTo", {}).get("street1"),
                "street2": shipment.get("shipTo", {}).get("street2"),
                "street3": shipment.get("shipTo", {}).get("street3"),
                "city": shipment.get("shipTo", {}).get("city"),
                "state": shipment.get("shipTo", {}).get("state"),
                "postal_code": shipment.get("shipTo", {}).get("postalCode"),
                "country": shipment.get("shipTo", {}).get("country"),
                "phone": shipment.get("shipTo", {}).get("phone"),
                "residential": shipment.get("shipTo", {}).get("residential")
            },
            
            # Shipping cost info
            "shipment_cost": shipment.get("shipmentCost"),
            "insurance_cost": shipment.get("insuranceCost"),
            "weight": {
                "value": shipment.get("weight", {}).get("value"),
                "units": shipment.get("weight", {}).get("units")
            },
            "dimensions": shipment.get("dimensions", {}),
            
            # Items in the shipment
            "items": []
        }
        
        # Process items with better null checking
        shipment_items = shipment.get("shipmentItems", []) or []
        for item in shipment_items:
            if item:  # Make sure item is not None
                shipment_info["items"].append({
                    "order_item_id": item.get("orderItemId"),
                    "line_item_key": item.get("lineItemKey"),
                    "sku": item.get("sku"),
                    "name": item.get("name"),
                    "image_url": item.get("imageUrl"),
                    "weight": {
                        "value": item.get("weight", {}).get("value") if item.get("weight") else None,
                        "units": item.get("weight", {}).get("units") if item.get("weight") else None
                    },
                    "quantity": item.get("quantity", 0),
                    "unit_price": item.get("unitPrice", 0),
                    "warehouse_location": item.get("warehouseLocation"),
                    "options": item.get("options", []) or []
                })
        
        # Calculate totals with better null checking
        total_items = 0
        total_value = 0
        
        for item in shipment_info["items"]:
            quantity = item.get("quantity", 0) or 0
            unit_price = item.get("unit_price", 0) or 0
            total_items += quantity
            total_value += (quantity * unit_price)
        
        total_weight = 0
        weight_data = shipment_info.get("weight", {})
        if weight_data and weight_data.get("value"):
            total_weight = weight_data["value"]
        
        shipment_info["totals"] = {
            "total_items": total_items,
            "total_weight": total_weight,
            "total_value": total_value,
            "item_count": len(shipment_info["items"])
        }
        
        return jsonify({
            "success": True,
            "shipment": shipment_info
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
       
@app.route('/testing')
def testing():
    return render_template('testing.html') 
        
@app.route("/login")
def login():
    # force the redirect URI to https so it matches your GCP console
    redirect_uri = url_for("authorize",
                           _external=True,
                           _scheme="https")
    return google.authorize_redirect(redirect_uri)


@app.route("/authorize")
def authorize():
    try:
        token = google.authorize_access_token()
        userinfo = google.userinfo()
        
        if not userinfo or "sub" not in userinfo or "email" not in userinfo:
            return "Authentication failed: Missing user information", 400
        
        session["user"] = {"sub": userinfo["sub"], "email": userinfo["email"]}
        login_user(User(userinfo["sub"], userinfo["email"]))
        return redirect(url_for("verified"))  # or wherever you want to redirect
    except Exception as e:
        return f"Authentication failed: {str(e)}", 400

@app.route("/logout")
def logout_v2():
    logout_user()
    session.pop("user", None)
    return redirect(url_for("unverified"))  # or wherever
    
    
@app.route('/hazmat_shipping')
@login_required_custom
def hazmat_shipping():
    return render_template('hazmat_shipping.html')

@app.route('/hazmat/orders')
@login_required_custom
def hazmat_orders():
    tag_id = request.args.get('tagId', 30829, type=int)
    url    = 'https://ssapi.shipstation.com/orders/listbytag'
    headers = {
        'Authorization': f"Basic {os.getenv('SHIPSTATION_V1_API_KEY')}"
    }
    params = {
        'orderStatus': 'awaiting_shipment',
        'tagId': tag_id
    }
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    # return just the list of orders
    return jsonify(resp.json().get('orders', []))

@app.route('/hazmat_manager')
@login_required_custom
def index():
    """Display all hazmat orders with tracking information."""
    processed_orders = shipstation.get_processed_orders()
    return render_template('hazmat_index.html', orders=processed_orders)


@app.route('/mark_shipped', methods=['POST'])
@login_required_custom
def mark_shipped():
    """API endpoint to mark an order as shipped."""
    data = request.get_json()
    order_id = data.get('orderId')
    tracking_number = data.get('trackingNumber')
    carrier_code = data.get('carrierCode', 'usps')
    
    if not order_id or not tracking_number:
        return jsonify({'error': 'Order ID and tracking number are required'}), 400
    
    result = shipstation.mark_order_as_shipped(order_id, tracking_number, carrier_code)
    
    if result:
        return jsonify({'success': True, 'result': result})
    else:
        return jsonify({'error': 'Failed to mark order as shipped'}), 500


@app.route('/refresh_orders')
@login_required_custom
def refresh_orders():
    """API endpoint to refresh the orders list."""
    orders = shipstation.get_hazmat_orders()
    return jsonify({'orders': orders})

@app.route('/db_browser')
def db_browser():
    """Browse PostgreSQL database tables with order search functionality."""
    table = request.args.get('table', 'shipstation_orders_raw')
    page = int(request.args.get('page', 1))
    search_order = request.args.get('search_order', '').strip()
    per_page = 20
    offset = (page - 1) * per_page
    
    # Validate table name for security
    allowed_tables = ['shipstation_orders_raw', 'shipstation_shipments_raw']
    if table not in allowed_tables:
        table = 'shipstation_orders_raw'
    
    try:
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', 5432),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            sslmode='require'
        )
        cursor = conn.cursor()
        
        # Handle order search
        if search_order:
            # Search for the specific order
            cursor.execute("""
                SELECT serial, payload, created_at 
                FROM shipstation_orders_raw 
                WHERE payload->>'orderNumber' = %s
                ORDER BY created_at DESC 
                LIMIT 1
            """, (search_order,))
            
            order_record = cursor.fetchone()
            if order_record:
                order_payload = order_record[1]
                
                # Find related shipments by orderNumber
                cursor.execute("""
                    SELECT serial, payload, created_at 
                    FROM shipstation_shipments_raw 
                    WHERE payload->>'orderNumber' = %s
                    ORDER BY created_at DESC
                """, (search_order,))
                
                shipment_records = cursor.fetchall()
                shipments = [record[1] for record in shipment_records]
                
                cursor.close()
                conn.close()
                
                return render_template('db_browser.html',
                                     search_order=search_order,
                                     order_details=order_payload,
                                     shipments=shipments,
                                     table=table,
                                     allowed_tables=allowed_tables)
            else:
                cursor.close()
                conn.close()
                
                return render_template('db_browser.html',
                                     search_order=search_order,
                                     order_details=None,
                                     shipments=None,
                                     table=table,
                                     allowed_tables=allowed_tables)
        
        # Regular table browsing (when not searching)
        # Get total count
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        total_count = cursor.fetchone()[0]
        
        # Get records with pagination
        cursor.execute(f"""
            SELECT serial, payload, created_at 
            FROM {table} 
            ORDER BY created_at DESC 
            LIMIT %s OFFSET %s
        """, (per_page, offset))
        
        records = cursor.fetchall()
        
        # Process records for display
        processed_records = []
        for record in records:
            record_id, payload, created_at = record
            
            # Extract key info from payload for preview
            if table == 'shipstation_orders_raw':
                preview = {
                    'orderNumber': payload.get('orderNumber', 'N/A'),
                    'orderStatus': payload.get('orderStatus', 'N/A'),
                    'customerEmail': payload.get('customerEmail', 'N/A'),
                    'orderTotal': payload.get('orderTotal', 0),
                    'orderDate': payload.get('orderDate', 'N/A')
                }
            else:  # shipments
                preview = {
                    'trackingNumber': payload.get('trackingNumber', 'N/A'),
                    'carrierCode': payload.get('carrierCode', 'N/A'),
                    'shipDate': payload.get('shipDate', 'N/A'),
                    'voided': payload.get('voided', False),
                    'shipmentCost': payload.get('shipmentCost', 0)
                }
            
            processed_records.append({
                'id': record_id,
                'payload': payload,
                'created_at': created_at,
                'preview': preview
            })
        
        cursor.close()
        conn.close()
        
        # Calculate pagination info
        total_pages = (total_count + per_page - 1) // per_page
        has_prev = page > 1
        has_next = page < total_pages
        
        return render_template('db_browser.html', 
                             records=processed_records,
                             table=table,
                             page=page,
                             total_pages=total_pages,
                             total_count=total_count,
                             has_prev=has_prev,
                             has_next=has_next,
                             allowed_tables=allowed_tables,
                             search_order=None)
        
    except Exception as e:
        app.logger.error(f"Database error: {e}")
        return render_template('db_browser.html', 
                             error=str(e),
                             table=table,
                             allowed_tables=allowed_tables)
        
        
@app.route('/search-page')
@login_required_custom   # if you want only logged-in users to see it
def search_page():
    # assumes you have templates/search.html in your templates/ folder
    return render_template('search.html')


@app.route('/verified')
@login_required_custom   # if you want only logged-in users to see it
def verified():
    # assumes you have templates/search.html in your templates/ folder
    return render_template('verified_nav.html')

@app.route('/unverified')
def unverified():
    # if the user is already logged in (and you consider that "verified"):
    if current_user.is_authenticated:
        return redirect(url_for('verified'))

    # otherwise show the unverified page
    return render_template('unverified_nav.html')

if __name__ == '__main__':
    if not os.path.exists('templates'):
        os.makedirs('templates')
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)