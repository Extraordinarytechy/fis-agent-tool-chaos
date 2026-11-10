"""issue_refund tool.

Writes one refund item to the ledger table. Each item represents a refund that
was committed, independent of what the agent later reports.

Both contracts are served by the same function, so an experiment changes only
the caller:

* Without idempotency_key (contract v1): every call creates a new refund_id,
  so a retry after a lost acknowledgement creates a second refund.
* With idempotency_key (contracts v2 and v3): the key becomes the refund_id and
  the write is conditional, so a replay returns the original refund instead of
  creating a new one.
"""

import json
import os
import time
import uuid
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
orders = dynamodb.Table(os.environ["ORDERS_TABLE"])
refunds = dynamodb.Table(os.environ["REFUNDS_TABLE"])


def _log(event_name, **fields):
    print(json.dumps({"tool": "issue_refund", "event": event_name, **fields}, default=str))


def handler(event, context):
    # The harness sends a probe before each run to confirm the fault is live.
    # Probes touch no data, so they never show up in the ledger.
    if event.get("probe"):
        return {"ok": True, "probe": True}

    order_id = event.get("order_id")
    amount = event.get("amount_usd")
    idempotency_key = event.get("idempotency_key")

    if not order_id or amount is None:
        return {"ok": False, "error_type": "VALIDATION", "message": "order_id and amount_usd are required"}

    order = orders.get_item(Key={"order_id": order_id}, ConsistentRead=True).get("Item")
    if not order:
        return {"ok": False, "error_type": "NOT_FOUND", "message": f"order {order_id} not found"}

    amount = Decimal(str(amount))
    if amount <= 0 or amount > order["total_usd"]:
        return {"ok": False, "error_type": "VALIDATION", "message": "amount must be > 0 and <= order total"}

    refund_id = idempotency_key or f"rf-{uuid.uuid4().hex[:12]}"
    item = {
        "order_id": order_id,
        "refund_id": refund_id,
        "amount_usd": amount,
        "committed_at_ms": int(time.time() * 1000),
        "request_id": context.aws_request_id,
        "contract": "v2" if idempotency_key else "v1",
    }

    if idempotency_key:
        try:
            refunds.put_item(Item=item, ConditionExpression="attribute_not_exists(refund_id)")
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            existing = refunds.get_item(
                Key={"order_id": order_id, "refund_id": refund_id}, ConsistentRead=True
            )["Item"]
            _log("replay", order_id=order_id, refund_id=refund_id, request_id=context.aws_request_id)
            return {
                "ok": True,
                "status": "ALREADY_APPLIED",
                "refund_id": refund_id,
                "amount_usd": float(existing["amount_usd"]),
            }
    else:
        refunds.put_item(Item=item)

    _log("committed", order_id=order_id, refund_id=refund_id, amount_usd=amount,
         request_id=context.aws_request_id, contract=item["contract"])
    return {"ok": True, "status": "REFUNDED", "refund_id": refund_id, "amount_usd": float(amount)}
