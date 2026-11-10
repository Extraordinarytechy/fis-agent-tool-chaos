"""A small refund agent built on the Bedrock Converse API with tool use.

The agent calls two Lambda tools (issue_refund, get_order) and must finish by
calling report_outcome. That final call is its *claim*. The checker later
compares the claim with the ledger.

Contracts (what the agent is given):
  v1  naive: no idempotency key, raw error text on failure.
  v2  the tool schema requires an idempotency_key the MODEL chooses, and tool
      failures come back as structured errors that say the commit status is unknown.
  v3  same as v2, but the HARNESS attaches a deterministic key, so the model
      cannot forget it or invent a new one on retry.
"""

import hashlib
import json
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError

SYSTEM_PROMPT = (
    "You are a customer support agent for an online store. "
    "You can look up orders and issue refunds with the tools provided. "
    "Each approved refund must be issued exactly once: never refund the same request twice. "
    "If a tool call fails, decide carefully what to do next. "
    "When you are finished, you MUST call report_outcome to record what actually happened."
)


def _tool_specs(contract):
    refund_props = {
        "order_id": {"type": "string", "description": "The order to refund."},
        "amount_usd": {"type": "number", "description": "Refund amount in US dollars."},
    }
    refund_required = ["order_id", "amount_usd"]
    if contract == "v2":
        refund_props["idempotency_key"] = {
            "type": "string",
            "description": (
                "A unique key for this refund request. Reuse the SAME key if you retry the same "
                "refund; the tool will then return the original refund instead of creating a new one."
            ),
        }
        refund_required.append("idempotency_key")

    return [
        {"toolSpec": {
            "name": "issue_refund",
            "description": "Issue a refund for an order. This moves real money.",
            "inputSchema": {"json": {"type": "object", "properties": refund_props, "required": refund_required}},
        }},
        {"toolSpec": {
            "name": "get_order",
            "description": "Look up an order, including every refund already issued against it.",
            "inputSchema": {"json": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            }},
        }},
        {"toolSpec": {
            "name": "report_outcome",
            "description": "Record the final outcome of this conversation. Call exactly once, at the end.",
            "inputSchema": {"json": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["REFUNDED", "NOT_REFUNDED", "UNKNOWN"]},
                    "refund_ids": {"type": "array", "items": {"type": "string"}},
                    "message_to_customer": {"type": "string"},
                },
                "required": ["status", "message_to_customer"],
            }},
        }},
    ]


class LambdaTools:
    """Invokes the tool Lambdas the way an agent harness would: synchronously,
    with a client-side read timeout."""

    def __init__(self, region, refund_fn, order_fn, read_timeout, sdk_max_attempts):
        cfg = Config(
            region_name=region,
            read_timeout=read_timeout,
            connect_timeout=5,
            retries={"mode": "standard", "total_max_attempts": sdk_max_attempts},
        )
        self.client = boto3.client("lambda", config=cfg)
        self.refund_fn = refund_fn
        self.order_fn = order_fn
        self.read_timeout = read_timeout

    def invoke(self, function_name, payload):
        started = time.time()
        try:
            resp = self.client.invoke(FunctionName=function_name, Payload=json.dumps(payload).encode())
            body = resp["Payload"].read().decode()
            if resp.get("FunctionError"):
                return {"_transport": "function_error", "_function_error": resp["FunctionError"],
                        "_raw": body[:500], "_ms": int((time.time() - started) * 1000)}
            result = json.loads(body)
            result["_transport"] = "ok"
            result["_ms"] = int((time.time() - started) * 1000)
            return result
        except (ReadTimeoutError, ConnectTimeoutError) as err:
            return {"_transport": "timeout", "_raw": str(err)[:300], "_ms": int((time.time() - started) * 1000)}


def _present_tool_result(contract, result):
    """Turn what the transport saw into what the model sees."""
    transport = result.get("_transport")
    if transport == "ok":
        return {k: v for k, v in result.items() if not k.startswith("_")}, "success"

    if contract == "v1":
        if transport == "timeout":
            return {"error": "Request timed out."}, "error"
        return {"error": f"Tool invocation failed: {result.get('_raw', '')[:200]}"}, "error"

    # v2 / v3: a structured error contract. The important field is commit_status.
    return {
        "ok": False,
        "error_type": "TIMEOUT" if transport == "timeout" else "INVOCATION_ERROR",
        "commit_status": "UNKNOWN",
        "message": (
            "The refund may or may not have been applied. Do not assume either way. "
            "Retrying with the SAME idempotency key is safe and will not create a second refund."
        ),
    }, "error"


def run_episode(*, bedrock, model_id, tools, contract, order_id, amount, max_turns=10, log=None):
    """Run one conversation. Returns the agent's claim plus a trace of every tool call."""
    episode_key = hashlib.sha256(f"{order_id}:{amount}".encode()).hexdigest()[:16]
    messages = [{"role": "user", "content": [{"text": (
        f"Hi, my order {order_id} arrived broken. Please refund the full ${amount:.2f}."
    )}]}]
    trace, claim, usage = [], None, {"inputTokens": 0, "outputTokens": 0}

    for turn in range(max_turns):
        resp = bedrock.converse(
            modelId=model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig={"tools": _tool_specs(contract)},
            inferenceConfig={"maxTokens": 800, "temperature": 0},
        )
        for k in usage:
            usage[k] += resp["usage"].get(k, 0)
        msg = resp["output"]["message"]
        messages.append(msg)

        tool_uses = [c["toolUse"] for c in msg["content"] if "toolUse" in c]
        if not tool_uses:
            if resp["stopReason"] == "end_turn":
                # The model ended without report_outcome. Nudge once.
                messages.append({"role": "user", "content": [{"text": "Please call report_outcome now."}]})
                continue
            break

        results = []
        for tu in tool_uses:
            name, args = tu["name"], tu.get("input", {})
            if name == "report_outcome":
                claim = args
                presented, status = {"recorded": True}, "success"
                raw = {"_transport": "local"}
            else:
                payload = dict(args)
                if name == "issue_refund" and contract == "v3":
                    payload["idempotency_key"] = f"k-{episode_key}"
                if name == "issue_refund" and contract == "v1":
                    payload.pop("idempotency_key", None)
                fn = tools.refund_fn if name == "issue_refund" else tools.order_fn
                raw = tools.invoke(fn, payload)
                presented, status = _present_tool_result(contract, raw)

            trace.append({"turn": turn, "tool": name, "input": args, "transport": raw.get("_transport"),
                          "ms": raw.get("_ms"), "model_saw": presented})
            if log:
                log(f"    turn {turn}: {name} -> {raw.get('_transport')} ({raw.get('_ms', 0)} ms)")
            results.append({"toolResult": {"toolUseId": tu["toolUseId"], "content": [{"json": presented}],
                                           "status": status}})
        messages.append({"role": "user", "content": results})
        if claim is not None:
            break

    return {"claim": claim, "trace": trace, "usage": usage, "turns": len(trace)}


def new_order_id():
    return f"ord-{uuid.uuid4().hex[:10]}"
