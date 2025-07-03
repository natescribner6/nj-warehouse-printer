# routes/gmail_bp.py
from flask import Blueprint, request, redirect, jsonify
from gmail_service import get_gmail_service, get_gmail_auth_url, fetch_gmail_token, list_gmail_messages

gmail_bp = Blueprint('gmail', __name__)

@gmail_bp.route('/authorize')
def gmail_auth():
    auth_url = get_gmail_auth_url()
    return redirect(auth_url)

@gmail_bp.route('/callback')
def gmail_callback():
    try:
        fetch_gmail_token(request.url)
        return jsonify(message='Gmail authentication successful')
    except Exception as e:
        return jsonify(error=str(e)), 500

@gmail_bp.route('/gmail/emails')
def get_emails():
    query = request.args.get('q', '')
    max_results = int(request.args.get('maxResults', 10))
    service = get_gmail_service()
    if not service:
        return jsonify(error='Authentication required', auth_url='/authorize'), 401
    try:
        messages, total = list_gmail_messages(query, max_results)
        return jsonify(messages=messages, total_found=total)
    except Exception as e:
        return jsonify(error=str(e)), 500