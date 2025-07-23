from flask import Blueprint, request, jsonify
from shipstation import search_shipments, update_order_warehouse
from flask_login import login_required

shipstation_bp = Blueprint('shipstation', __name__, url_prefix='/search')

@shipstation_bp.route('/')
@login_required
def search_orders():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify(error='No query provided'), 400
    results = search_shipments(q)
    return jsonify(results=results, count=len(results))


@shipstation_bp.route('/hazmat/update_db', methods=['GET'])
@login_required
def hazmat_update_db():
    # grab as string since in your JSON it's stored as text
    order_id = request.args.get('orderId')
    # warehouseId really should be an int
    warehouse_id = request.args.get('warehouseId', type=int)

    if not order_id or warehouse_id is None:
        return jsonify({
            'status': 'error',
            'message': 'Both orderId and warehouseId query params are required'
        }), 400

    try:
        update_order_warehouse(order_id, warehouse_id)
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Failed to update warehouse: {e}'
        }), 500

    return jsonify({
        'status': 'success',
        'orderId': order_id,
        'warehouseId': warehouse_id
    }), 200