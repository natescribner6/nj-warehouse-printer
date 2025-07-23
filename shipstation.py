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

def update_order_warehouse(order_id: str, warehouse_id: int):
    """
    Update the advancedOptions.warehouseId in shipstation_orders_raw.payload
    for the given order_id.
    """
    sql = """
    UPDATE shipstation_orders_raw
    SET payload = jsonb_set(
        payload,
        '{advancedOptions,warehouseId}',         -- path into the JSON
        to_jsonb(%s::int),                       -- new value as JSON (an integer)
        true                                      -- create missing keys if needed
    )
    WHERE payload->>'orderId' = %s;
    """

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, (warehouse_id, order_id))
        conn.commit()
    finally:
        conn.close()

