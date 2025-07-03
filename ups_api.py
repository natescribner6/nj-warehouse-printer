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
    headers = {
        'Authorization': f"Basic {b64}",
        'x-merchant-id': merchant_id,
        'Content-Type': 'application/x-www-form-urlencoded'
    }
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
    headers = {
        'Authorization': f"Bearer {token}",
        'x-merchant-id': merchant_id,
        'Accept': 'application/json',
        'transID': f'{tracking_number}_',
        'transactionSrc': 'nates.site'
    }

    logger.debug(f"Requesting UPS tracking for {tracking_number}")
    resp = requests.get(track_url, headers=headers)
    resp.raise_for_status()
    return resp.json()