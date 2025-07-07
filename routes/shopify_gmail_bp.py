# routes/shopify_gmail_bp.py
import re
from flask import Blueprint, request, render_template, redirect, url_for, session
from shopify_api import fetch_recent_orders, extract_unique_emails
from gmail_service import get_gmail_service, list_gmail_messages

# regex to pull 4-digit order IDs from subject/snippet
ORDER_RE = re.compile(r'\b\d{4}\b')

shopify_gmail_bp = Blueprint('shopify_gmail', __name__, url_prefix='/website-cs')

@shopify_gmail_bp.route('/', methods=['GET'], strict_slashes=False)
def shopify_gmail():
    # 1) limit from query-param
    if 'google_token' not in session:
        return redirect(url_for('login', _external=True))
    

    # 1) limit from query-param
    n = request.args.get('limit', default=50, type=int)

    # 2) fetch Shopify orders → dedupe emails
    orders = fetch_recent_orders(n)
    emails = extract_unique_emails(orders)

    # 3) build Gmail query and fetch message IDs
    gmail_query = "from:(" + " OR ".join(f'"{e}"' for e in emails) + ")"
    raw_msgs, total = list_gmail_messages(gmail_query, max_results=n)

    # 4) hydrate each message with metadata + snippet
    service = get_gmail_service()
    detailed = []
    for m in raw_msgs:
        full = service.users().messages().get(
            userId='me',
            id=m['id'],
            format='metadata',
            metadataHeaders=['From','Subject','Date']
        ).execute()

        # pull headers into a dict
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

    # 5) render the template
    return render_template(
        'shopify_gmail.html',
        messages=detailed,
        total_found=total,
        n=n,
        email_list=emails
    )
