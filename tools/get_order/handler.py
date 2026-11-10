"""get_order tool: read-only view of an order and every refund committed against it.

This is the read-back an agent can use to check what really happened after a
tool error. It uses consistent reads so a refund committed a moment ago shows up.
"""

import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
orders = dynamodb.Table(os.environ["ORDERS_TABLE"])
refunds = dynamodb.Table(os.environ["REFUNDS_TABLE"])


def _plain(value):
    return float(value) if isinstance(value, Decimal) else value


def handler(event, context):
    order_id = event.get("order_id")
    if not order_id:
        return {"ok": False, "error_type": "VALIDATION", "message": "order_id is required"}

    order = orders.get_item(Key={"order_id": order_id}, ConsistentRead=True).get("Item")
    if not order:
        return {"ok": False, "error_type": "NOT_FOUND", "message": f"order {order_id} not found"}

    items = refunds.query(
        KeyConditionExpression=Key("order_id").eq(order_id), ConsistentRead=True
    )["Items"]

    return {
        "ok": True,
        "order_id": order_id,
        "total_usd": _plain(order["total_usd"]),
        "refunds": [
            {"refund_id": r["refund_id"], "amount_usd": _plain(r["amount_usd"])} for r in items
        ],
        "refunded_total_usd": float(sum((r["amount_usd"] for r in items), Decimal("0"))),
    }
