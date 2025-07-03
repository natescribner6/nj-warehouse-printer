# shopify_api.py
import os
import requests

"""
Handles fetching recent orders from Shopify and extracting unique customer emails.
"""
# Shopify credentials from environment
SHOPIFY_STORE = os.getenv('SHOPIFY_STORE')
SHOPIFY_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN')
API_VERSION = os.getenv('SHOPIFY_API_VERSION', '2024-01')
MAX_PAGE_LIMIT = 250


def fetch_recent_orders(limit):
    """
    Fetch up to `limit` orders from Shopify (paginated if > 250).
    Returns a list of order dicts.
    """
    if not SHOPIFY_STORE or not SHOPIFY_TOKEN:
        raise RuntimeError("Missing SHOPIFY_STORE or SHOPIFY_ACCESS_TOKEN environment variables")

    orders_url = f'https://{SHOPIFY_STORE}/admin/api/{API_VERSION}/orders.json'
    headers = {
        'X-Shopify-Access-Token': SHOPIFY_TOKEN,
        'Content-Type': 'application/json'
    }
    all_orders = []
    n = limit

    # first page
    params1 = {'limit': min(n, MAX_PAGE_LIMIT), 'status': 'any'}
    resp1 = requests.get(orders_url, headers=headers, params=params1)
    resp1.raise_for_status()
    orders1 = resp1.json().get('orders', [])
    all_orders.extend(orders1)

    # second page if needed
    if len(orders1) == MAX_PAGE_LIMIT and n > MAX_PAGE_LIMIT:
        remaining = n - MAX_PAGE_LIMIT
        since_id = orders1[-1].get('id')
        if since_id:
            params2 = {'limit': min(remaining, MAX_PAGE_LIMIT), 'status': 'any', 'since_id': since_id}
            resp2 = requests.get(orders_url, headers=headers, params=params2)
            resp2.raise_for_status()
            all_orders.extend(resp2.json().get('orders', []))

    return all_orders


def extract_unique_emails(orders):
    """
    Given a list of Shopify order dicts, extract and dedupe customer emails.
    Returns a list of unique email strings.
    """
    seen = set()
    unique = []
    for order in orders:
        email = order.get('email')
        if email and email not in seen:
            seen.add(email)
            unique.append(email)
    return unique


# db.py
import os
import psycopg2
from psycopg2 import OperationalError

def get_db_connection():
    """
    Establish a new database connection using environment variables.
    Returns a psycopg2 connection object.
    """
    try:
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=int(os.getenv('DB_PORT', 5432)),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            sslmode='require'
        )
        return conn
    except OperationalError:
        raise


# shipstation.py
from db import get_db_connection

def search_shipments(order_number, limit=50):
    """
    Query ShipStation shipments and orders for the given order_number.
    Returns a list of shipment dicts.
    """
    sql = """
        SELECT
            s.serial,
            s.type,
            s.payload,
            s.created_at,
            o.payload AS orderPayload
        FROM shipstation_shipments_raw s
        LEFT JOIN shipstation_orders_raw o
            ON s.payload->>'orderId' = o.payload->>'orderId'
        WHERE s.payload->>'orderNumber' = %s
        ORDER BY s.created_at DESC
        LIMIT %s
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (order_number, limit))
            rows = cursor.fetchall()
    finally:
        conn.close()

    results = []
    for serial, type_, payload, created_at, order_payload in rows:
        results.append({
            'serial': serial,
            'orderNumber': payload.get('orderNumber', 'N/A'),
            'trackingNumber': payload.get('trackingNumber', 'N/A'),
            'carrierCode': payload.get('carrierCode', 'N/A'),
            'serviceCode': payload.get('serviceCode', 'N/A'),
            'shipDate': payload.get('shipDate', 'N/A'),
            'shipmentCost': payload.get('shipmentCost', 0),
            'customerEmail': payload.get('customerEmail', 'N/A'),
            'voided': payload.get('voided', False),
            'shipTo': payload.get('shipTo', {}),
            'orderItemId': payload.get('orderItemId', 'N/A'),
            'orderId': payload.get('orderId', 'N/A'),
            'fulfillment': (type_ == 'f'),
            'shipmentItems': payload.get('shipmentItems', []),
            'orderItems': order_payload.get('items', []) if isinstance(order_payload, dict) else []
        })
    return results


# ups_api.py
import os
import base64
import requests
import logging

logger = logging.getLogger(__name__)

def get_ups_token():
    """Fetch an OAuth token from UPS."""
    client_id = os.getenv('UPS_CLIENT_ID')
    client_secret = os.getenv('UPS_CLIENT_SECRET')
    merchant_id = os.getenv('UPS_MERCHANT_ID', '')
    token_url = os.getenv('UPS_TOKEN_URL', 'https://wwwcie.ups.com/security/v1/oauth/token')

    if not client_id or not client_secret:
        raise RuntimeError("Missing UPS_CLIENT_ID or UPS_CLIENT_SECRET")

    raw = f"{client_id}:{client_secret}".encode('utf-8')
    b64 = base64.b64encode(raw).decode('ascii')
    headers = {'Authorization': f"Basic {b64}", 'x-merchant-id': merchant_id, 'Content-Type': 'application/x-www-form-urlencoded'}
    data = {'grant_type': 'client_credentials'}

    logger.debug("Requesting UPS token")
    resp = requests.post(token_url, headers=headers, data=data)
    resp.raise_for_status()
    return resp.json().get('access_token')


def track_ups(tracking_number):
    """Retrieve tracking details from UPS."""
    if not tracking_number:
        raise ValueError("tracking_number is required")
    token = get_ups_token()
    merchant_id = os.getenv('UPS_MERCHANT_ID', '')
    track_url = f"https://onlinetools.ups.com/api/track/v1/details/{tracking_number}?returnSignature=true"
    headers = {'Authorization': f"Bearer {token}", 'x-merchant-id': merchant_id, 'Accept': 'application/json'}

    logger.debug(f"Requesting UPS tracking for {tracking_number}")
    resp = requests.get(track_url, headers=headers)
    resp.raise_for_status()
    return resp.json()


# fedex_api.py
import os
import requests
import logging

logger = logging.getLogger(__name__)

def get_fedex_token():
    """Fetch an OAuth token from FedEx."""
    client_id = os.getenv('FEDEX_CLIENT_ID')
    client_secret = os.getenv('FEDEX_CLIENT_SECRET')
    token_url = os.getenv('FEDEX_TOKEN_URL', 'https://apis.fedex.com/oauth/token')

    if not client_id or not client_secret:
        raise RuntimeError("Missing FEDEX_CLIENT_ID or FEDEX_CLIENT_SECRET")

    data = {'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': client_secret}
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    logger.debug("Requesting FedEx token")
    resp = requests.post(token_url, headers=headers, data=data)
    resp.raise_for_status()
    return resp.json().get('access_token')

def track_fedex(tracking_number):
    """Retrieve tracking details from FedEx."""
    if not tracking_number:
        raise ValueError("tracking_number is required")
    token = get_fedex_token()
    track_url = os.getenv('FEDEX_TRACK_URL', 'https://apis.fedex.com/track/v1/trackingnumbers')
    headers = {
        'Authorization': f"Bearer {token}",
        'Content-Type': 'application/json',
        'x-local': os.getenv('FEDEX_LOCALE', 'en_us'),
        'x-account-number': os.getenv('FEDEX_ACCOUNT_NUMBER', '')
    }
    payload = {'includeDetailedScans': True, 'trackingInfo': [{'trackingNumberInfo': {'trackingNumber': tracking_number}}]}

    logger.debug(f"Requesting FedEx tracking for {tracking_number}")
    resp = requests.post(track_url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()

# routes/shopify_gmail_bp.py
from flask import Blueprint, request, render_template
from shopify_api import fetch_recent_orders, extract_unique_emails
from gmail_service import list_gmail_messages

shopify_gmail_bp = Blueprint('shopify_gmail', __name__)

@shopify_gmail_bp.route('/', methods=['GET'])
def shopify_gmail():
    n = request.args.get('limit', default=50, type=int)
    orders = fetch_recent_orders(n)
    emails = extract_unique_emails(orders)
    gmail_query = "from:(" + " OR ".join(f'\"{e}\"' for e in emails) + ")"
    messages, total = list_gmail_messages(gmail_query, max_results=n)
    return render_template('shopify_gmail.html', messages=messages, total_found=total, n=n, email_list=emails)
