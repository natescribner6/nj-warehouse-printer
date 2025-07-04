import re
from flask import Blueprint, request, render_template, session, url_for, current_app
from flask_login import current_user
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import os

from shopify_api import fetch_recent_orders, extract_unique_emails
from app import google, login_required_custom

# regex to pull 4-digit order IDs
ORDER_RE = re.compile(r'\b\d{4}\b')

shopify_gmail_bp = Blueprint('shopify_gmail', __name__, url_prefix='/website-cs')

@shopify_gmail_bp.route('/', methods=['GET'], strict_slashes=False)
@login_required_custom
def shopify_gmail():
    # 1) limit from query-param
    n = request.args.get('limit', default=50, type=int)

    # 2) fetch Shopify orders → dedupe emails
    orders = fetch_recent_orders(n)
    emails = extract_unique_emails(orders)

    # 3) build Gmail query
    gmail_query = "from:(" + " OR ".join(f'"{e}"' for e in emails) + ")"

    # 4) grab OAuth token from session
    token = session.get('google_token')
    if not token:
        return (
            render_template('error.html', 
                message="Gmail auth required", 
                auth_url=url_for('authorize')
            ),
            401
        )

    # 5) build & refresh Credentials
    creds = Credentials(
        token['access_token'],
        refresh_token=token.get('refresh_token'),
        token_uri=google.metadata['token_endpoint'],
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # store the new access token back in session
            session['google_token']['access_token'] = creds.token
        except Exception as e:
            current_app.logger.error("Failed to refresh Gmail token: %s", e)
            return render_template('error.html', message="Failed to refresh Gmail token"), 500

    # 6) build Gmail service
    try:
        service = build('gmail', 'v1', credentials=creds)
    except Exception as e:
        current_app.logger.error("Gmail service init error: %s", e)
        return render_template('error.html', message="Unable to initialize Gmail API"), 500

    # 7) list message IDs
    try:
        resp = service.users().messages().list(
            userId='me', q=gmail_query, maxResults=n
        ).execute()
        raw_msgs = resp.get('messages', [])
        total  = resp.get('resultSizeEstimate', 0)
    except Exception as e:
        current_app.logger.error("Gmail list error: %s", e)
        return render_template('error.html', message="Gmail API list failed"), 500

    # 8) hydrate each
    detailed = []
    for m in raw_msgs:
        try:
            full = service.users().messages().get(
                userId='me',
                id=m['id'],
                format='metadata',
                metadataHeaders=['From','Subject','Date']
            ).execute()
        except Exception as e:
            current_app.logger.warning("Failed to fetch msg %s: %s", m['id'], e)
            continue

        hdrs = {h['name']: h['value'] for h in full['payload']['headers']}
        text = (hdrs.get('Subject','') + " " + full.get('snippet',''))
        order_id = ORDER_RE.search(text)

        detailed.append({
            'from':    hdrs.get('From',''),
            'subject': hdrs.get('Subject',''),
            'date':    hdrs.get('Date',''),
            'snippet': full.get('snippet',''),
            'threadId': m['threadId'],
            'orderIDs': order_id.group(0) if order_id else ''
        })

    # 9) render the template
    return render_template(
        'shopify_gmail.html',
        messages=detailed,
        total_found=total,
        n=n,
        email_list=emails
    )
