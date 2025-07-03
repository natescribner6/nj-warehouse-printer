from flask import Blueprint, request, jsonify
from ups_api import track_ups

ups_bp = Blueprint('ups', __name__, url_prefix='/ups')

@ups_bp.route('/track')
def ups_track():
    tracking_number = request.args.get('trackingNumber')
    if not tracking_number:
        return jsonify(error='No trackingNumber provided'), 400
    try:
        data = track_ups(tracking_number)
        return jsonify(data)
    except Exception as e:
        return jsonify(error=str(e)), 500