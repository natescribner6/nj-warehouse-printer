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

    data = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret
    }
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
    payload = {
        'includeDetailedScans': True,
        'trackingInfo': [
            {'trackingNumberInfo': {'trackingNumber': tracking_number}}
        ]
    }

    logger.debug(f"Requesting FedEx tracking for {tracking_number}")
    resp = requests.post(track_url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()
