import os
import time
import requests
from db import get_db_connection

# ─── MODULE-LEVEL AUTH CACHE ──────────────────────────────────────────────────
_auth_cache = {
    "token":     None,
    "expires_at": 0
}

# ─── CREDENTIALS VIA ENVIRONMENT ─────────────────────────────────────────────
LNW_APP_ID     = os.getenv("LINNWORKS_APP_ID")
LNW_APP_SECRET = os.getenv("LINNWORKS_APP_SECRET")
LNW_APP_TOKEN  = os.getenv("LINNWORKS_APP_TOKEN")  # initial & refresh token

# ─── ENDPOINTS ────────────────────────────────────────────────────────────────
AUTH_URL = "https://api.linnworks.net/api/Auth/AuthorizeByApplication"
BASE_URL = "https://us-ext.linnworks.net/api"


def _authorize():
    """Fetch a new auth token and cache it with expiry."""
    payload = {
        "ApplicationId":     LNW_APP_ID,
        "ApplicationSecret": LNW_APP_SECRET,
        "Token":             LNW_APP_TOKEN
    }
    resp = requests.post(AUTH_URL, json=payload)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("Token")
    # Linnworks tokens default to 120 minutes
    expires_in = data.get("ExpiresIn", 2 * 3600)
    # cache and refresh 60s early
    _auth_cache.update({
        "token": token,
        "expires_at": time.time() + expires_in - 60
    })
    return token


def _get_token():
    """Return a valid auth token, refreshing if expired or missing."""
    if not _auth_cache["token"] or time.time() > _auth_cache["expires_at"]:
        return _authorize()
    return _auth_cache["token"]


def _get_headers():
    token = _get_token()
    return {
        "Accept":        "application/json",
        "Content-Type":  "application/json",
        "Authorization": token
    }


def get_open_orders(last_days=25, page=1, per_page=50):
    headers = _get_headers()
    payload = {
        "filters": {
            "DateFields": [{
                "Type":      "LastDays",
                "Value":     last_days,
                "FieldCode": "GENERAL_INFO_DATE"
            }],
            "BooleanFields": [{
                "FieldCode":"GENERAL_INFO_PARKED",
                "Value":    False
            }]
        },
        "entriesPerPage": per_page,
        "pageNumber":     page
    }
    return requests.post(
        f"{BASE_URL}/Orders/GetOpenOrders",
        json=payload, headers=headers
    ).json()


def run_rules_engine(rule_id, order_ids):
    headers = _get_headers()
    payload = {"ruleId": rule_id, "orderIds": order_ids}
    return requests.post(
        f"{BASE_URL}/Orders/RunRulesEngine",
        json=payload, headers=headers
    ).json()


def search_shipments(order_number, limit=50):
    """Query Postgres for shipments + joined order items."""
    sql = """
    SELECT s.serial, s.type, s.payload, s.created_at,
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
        with conn.cursor() as cur:
            cur.execute(sql, (order_number, limit))
            rows = cur.fetchall()
    finally:
        conn.close()

    results = []
    for serial, type_, payload, created_at, order_payload in rows:
        results.append({
            "serial":         serial,
            "orderNumber":    payload.get("orderNumber"),
            "trackingNumber": payload.get("trackingNumber"),
            "shipDate":       payload.get("shipDate"),
            "shipmentCost":   payload.get("shipmentCost", 0),
            "voided":         payload.get("voided", False),
            "fulfillment":    (type_ == "f"),
            "shipmentItems":  payload.get("shipmentItems", []),
            "orderItems":     (order_payload.get("items")
                                if isinstance(order_payload, dict)
                                else [])
        })
    return results