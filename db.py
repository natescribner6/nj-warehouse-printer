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
    except OperationalError as e:
        # Log or re-raise as needed
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
