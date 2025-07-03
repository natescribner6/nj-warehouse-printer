from flask import Blueprint, request, jsonify
from fedex_api import track_fedex

fedex_bp = Blueprint('fedex', __name__, url_prefix='/fedex')

@fedex_bp.route('/track')
def fedex_track():
    tracking_number = request.args.get('trackingNumber')
    if not tracking_number:
        return jsonify(error='No trackingNumber provided'), 400
    try:
        data = track_fedex(tracking_number)
        return jsonify(data)
    except Exception as e:
        return jsonify(error=str(e)), 500