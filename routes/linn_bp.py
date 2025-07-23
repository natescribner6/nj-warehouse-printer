from flask import Blueprint, request, jsonify
from linn_service import (
    get_open_orders,
    run_rules_engine,
    search_shipments
)

linn_bp = Blueprint("linnworks", __name__, url_prefix="/linnworks")


@linn_bp.route("/orders/open", methods=["GET"])
def open_orders():
    days = request.args.get("days", default=25, type=int)
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=50, type=int)
    return jsonify(get_open_orders(last_days=days, page=page, per_page=per_page))


@linn_bp.route("/rules/run", methods=["POST"])
def rules():
    data = request.get_json()
    return jsonify(run_rules_engine(
        rule_id=data["ruleId"],
        order_ids=data["orderIds"]
    ))


@linn_bp.route("/shipments/<order_number>", methods=["GET"])
def shipments(order_number):
    limit = request.args.get("limit", default=50, type=int)
    return jsonify(search_shipments(order_number, limit))