# gmail_service.py
import os
import pickle
import logging
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
        "redirect_uris": ["http://127.0.0.1:5000/callback"]
    }
}

def get_gmail_service():
    creds = None
    if os.path.exists('gmail_token.pickle'):
        with open('gmail_token.pickle', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.error("Failed to refresh Gmail credentials: %s", e)
                return None
        else:
            return None
    with open('gmail_token.pickle', 'wb') as token:
        pickle.dump(creds, token)
    try:
        service = build('gmail', 'v1', credentials=creds)
        return service
    except Exception as e:
        logger.error("Failed to build Gmail service: %s", e)
        return None

def get_gmail_auth_url():
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = CLIENT_CONFIG['web']['redirect_uris'][0]
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    return auth_url

def fetch_gmail_token(authorization_response):
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = CLIENT_CONFIG['web']['redirect_uris'][0]
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials
    with open('gmail_token.pickle', 'wb') as token:
        pickle.dump(creds, token)
    return creds

def list_gmail_messages(query, max_results=10):
    service = get_gmail_service()
    if not service:
        raise RuntimeError("Gmail authentication required")
    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
        return results.get('messages', []), results.get('resultSizeEstimate', 0)
    except HttpError as e:
        logger.error("Gmail API error: %s", e)
        raise