from flask import Blueprint, request, jsonify
from shipstation import search_shipments
from app import login_required_custom   # ← import your decorator


shipstation_bp = Blueprint('shipstation', __name__, url_prefix='/search')

@shipstation_bp.route('/')
@login_required_custom
def search_orders():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify(error='No query provided'), 400
    results = search_shipments(q)
    return jsonify(results=results, count=len(results))