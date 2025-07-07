from flask import Blueprint, request, jsonify, session, url_for, current_app
from flask_login import current_user, login_required
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import os

# Blueprint for Gmail-related routes
gmail_bp = Blueprint('gmail', __name__)

@gmail_bp.route('/gmail/emails')
@login_required
def get_emails():
    # Retrieve stored OAuth token from session
    token = session.get('google_token')
    if not token:
        return jsonify(
            error='Authentication required',
            auth_url=url_for('login', _external=True)
        ), 401

    # Build Credentials object
    creds = Credentials(
        token['access_token'],
        refresh_token=token.get('refresh_token'),
        token_uri=google.metadata['token_endpoint'],
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    )

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            current_app.logger.error("Failed to refresh Gmail credentials: %s", e)
            return jsonify(error='Failed to refresh credentials'), 500

    # Build Gmail service
    try:
        service = build('gmail', 'v1', credentials=creds)
    except Exception as e:
        current_app.logger.error("Failed to initialize Gmail service: %s", e)
        return jsonify(error='Gmail service error'), 500

    # Fetch messages
    query = request.args.get('q', '')
    max_results = int(request.args.get('maxResults', 10))
    try:
        resp = service.users().messages().list(
            userId='me', q=query, maxResults=max_results
        ).execute()
        messages = resp.get('messages', [])
        total = resp.get('resultSizeEstimate', 0)
        return jsonify(messages=messages, total_found=total)
    except Exception as e:
        current_app.logger.error("Gmail API error: %s", e)
        return jsonify(error=str(e)), 500
