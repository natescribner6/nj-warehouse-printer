# gmail_service.py

import os
import logging
from flask import session
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'openid', 'email', 'profile'
]

CLIENT_CONFIG = {
    "web": {
        "client_id": os.getenv('GOOGLE_CLIENT_ID'),
        "client_secret": os.getenv('GOOGLE_CLIENT_SECRET'),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [
            "https://nates.site/authorize"
        ]
    }
}


def get_gmail_service():
    """
    Build and return a Gmail API service object using credentials stored in the Flask session.
    Returns None if no valid credentials are found.
    """
    # Grab the stored token dict from the session
    token = session.get('google_token')
    if not token:
        return None

    creds = Credentials(
        token['access_token'],
        refresh_token=token.get('refresh_token'),
        token_uri=CLIENT_CONFIG['web']['token_uri'],
        client_id=CLIENT_CONFIG['web']['client_id'],
        client_secret=CLIENT_CONFIG['web']['client_secret'],
    )

    # Refresh if it’s expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save the refreshed creds back into the session
            session['google_token'] = {
                'access_token': creds.token,
                'refresh_token': creds.refresh_token
            }
        except Exception as e:
            logger.error("Failed to refresh Gmail credentials: %s", e)
            return None

    try:
        return build('gmail', 'v1', credentials=creds)
    except Exception as e:
        logger.error("Failed to build Gmail service: %s", e)
        return None


def get_gmail_auth_url():
    """
    Generate the Google OAuth authorization URL and store the state in session.
    """
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = CLIENT_CONFIG['web']['redirect_uris'][0]
    auth_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent'
    )
    # Keep the state for CSRF validation
    session['oauth_state'] = state
    return auth_url


def fetch_gmail_token(authorization_response):
    """
    Exchange the authorization response for credentials and store them in session.
    """
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = CLIENT_CONFIG['web']['redirect_uris'][0]
    # Restore state so fetch_token() can validate it
    flow.state = session.get('oauth_state')
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials

    # Store the token info in the session instead of a file
    session['google_token'] = {
        'access_token': creds.token,
        'refresh_token': creds.refresh_token
    }

    return creds


def list_gmail_messages(query, max_results=10):
    """
    List messages matching the given query. Returns a tuple (messages, total_found).
    Raises RuntimeError if authentication is required.
    """
    service = get_gmail_service()
    if not service:
        raise RuntimeError("Gmail authentication required")

    try:
        results = service.users().messages().list(
            userId='me',
            q=query,
            maxResults=max_results
        ).execute()
        return results.get('messages', []), results.get('resultSizeEstimate', 0)
    except HttpError as e:
        logger.error("Gmail API error: %s", e)
        raise
