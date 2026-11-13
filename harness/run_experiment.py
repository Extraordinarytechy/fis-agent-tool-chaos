"""Run N agent episodes under one FIS fault (or none) and record what really happened.

Example:
  python harness/run_experiment.py --fault inflight --contract v1 --episodes 10

Flow:
  1. Read stack outputs from Terraform.
  2. Start the FIS experiment for the chosen fault.
  3. Probe issue_refund until the fault is visibly active (the extension polls S3,
     so there is a ramp-up delay).
  4. Run the episodes. For each: create an order, let the agent work, wait for late
     commits, compare the claim with the ledger, publish metrics.
  5. Stop the experiment (you pay per action-minute) and print a summary.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

import boto3

sys.path.insert(0, os.path.dirname(__file__))
from agent import LambdaTools, new_order_id, run_episode  # noqa: E402
from checker import evaluate, publish_metrics  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def tf_outputs(infra_dir):
    out = subprocess.run(["terraform", "output", "-json"], cwd=infra_dir, check=True, capture_output=True, text=True)
    return {k: v["value"] for k, v in json.loads(out.stdout).items()}


def wait_experiment_state(fis, exp_id, wanted, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = fis.get_experiment(id=exp_id)["experiment"]["state"]
        if state["status"] in wanted:
            return state
        if state["status"] in ("failed", "stopped", "completed") and state["status"] not in wanted:
            raise RuntimeError(f"experiment ended early: {state}")
        time.sleep(3)
    raise TimeoutError(f"experiment {exp_id} did not reach {wanted}")


def probe_until_fault(tools, fault, delay_ms, timeout=240):
    """Send no-op probes until the fault shows up. Returns seconds waited."""
    started = time.time()
    while time.time() - started < timeout:
        r = tools.invoke(tools.refund_fn, {"probe": True})
        if fault in ("precommit", "lost_ack") and r["_transport"] == "function_error":
            return time.time() - started
        if fault == "inflight" and (r["_transport"] == "timeout" or r.get("_ms", 0) >= delay_ms * 0.9):
            return time.time() - started
        time.sleep(3)
    raise TimeoutError("fault never became visible to the probe")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fault", choices=["none", "precommit", "lost_ack", "inflight"], required=True)
    p.add_argument("--contract", choices=["v1", "v2", "v3"], default="v1")
    p.add_argument("--model", default="us.amazon.nova-2-lite-v1:0")
    p.add_argument("--episodes", type=int, default=10)
    p.add_argument("--amount", type=float, default=49.99)
    p.add_argument("--read-timeout", type=float, default=4.0, help="client read timeout for tool calls (s)")
    p.add_argument("--sdk-attempts", type=int, default=1, help="botocore total_max_attempts for tool calls")
    p.add_argument("--settle", type=float, default=None, help="seconds to wait for late commits before checking")
    p.add_argument("--delay-ms", type=int, default=8000, help="must match var.delay_ms")
    p.add_argument("--infra-dir", default=os.path.join(ROOT, "infra"))
    p.add_argument("--no-metrics", action="store_true")
    args = p.parse_args()

    out = tf_outputs(args.infra_dir)
    region = out["region"]
    session = boto3.session.Session(region_name=region)
    bedrock = session.client("bedrock-runtime")
    fis = session.client("fis")
    cloudwatch = session.client("cloudwatch")
    ddb = session.resource("dynamodb")
    orders, refunds = ddb.Table(out["orders_table"]), ddb.Table(out["refunds_table"])
    tools = LambdaTools(region, out["issue_refund_function"], out["get_order_function"],
                        args.read_timeout, args.sdk_attempts)
    settle = args.settle if args.settle is not None else (args.delay_ms / 1000 + 6 if args.fault == "inflight" else 3)

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{args.fault}-{args.contract}"
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    results_path = os.path.join(ROOT, "results", f"{run_id}.jsonl")
    log(f"run {run_id}: model={args.model} episodes={args.episodes} read_timeout={args.read_timeout}s "
        f"sdk_attempts={args.sdk_attempts} settle={settle}s")

    exp_id = None
    try:
        if args.fault != "none":
            template = out["experiment_templates"][args.fault]
            exp_id = fis.start_experiment(experimentTemplateId=template, tags={"run": run_id})["experiment"]["id"]
            log(f"started FIS experiment {exp_id} (template {template})")
            wait_experiment_state(fis, exp_id, {"running"})
            waited = probe_until_fault(tools, args.fault, args.delay_ms)
            log(f"fault visible to the probe after {waited:.0f}s of probing")

        counts, usage_total = Counter(), {"inputTokens": 0, "outputTokens": 0}
        for n in range(1, args.episodes + 1):
            order_id = new_order_id()
            orders.put_item(Item={"order_id": order_id, "total_usd": Decimal(str(args.amount))})
            log(f"episode {n}/{args.episodes} order={order_id}")
            ep = run_episode(bedrock=bedrock, model_id=args.model, tools=tools, contract=args.contract,
                             order_id=order_id, amount=args.amount, log=log)
            time.sleep(settle)
            verdict = evaluate(refunds, order_id, args.amount, ep["claim"])
            counts[verdict["outcome"]] += 1
            for k in usage_total:
                usage_total[k] += ep["usage"][k]
            log(f"    claim={ (ep['claim'] or {}).get('status') } committed={verdict['committed_refunds']} "
                f"-> {verdict['outcome']}")
            if not args.no_metrics:
                publish_metrics(cloudwatch, verdict, args.fault, args.contract, args.model)
            with open(results_path, "a") as fh:
                fh.write(json.dumps({"run_id": run_id, "episode": n, "order_id": order_id, "fault": args.fault,
                                     "contract": args.contract, "model": args.model,
                                     "read_timeout": args.read_timeout, "sdk_attempts": args.sdk_attempts,
                                     **verdict, "claim": ep["claim"], "trace": ep["trace"],
                                     "usage": ep["usage"],
                                     "refund_request_id": ep["refund_request_id"]}, default=str) + "\n")
    finally:
        if exp_id:
            state = fis.get_experiment(id=exp_id)["experiment"]["state"]
            if state["status"] in ("running", "initiating"):
                fis.stop_experiment(id=exp_id)
                log(f"stopped FIS experiment {exp_id}")
            else:
                # e.g. FIS already stopped it because the stop-condition alarm fired
                log(f"experiment {exp_id} already {state['status']}: {state.get('reason', '')}")

    log("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    log(f"tokens: {usage_total}")
    log(f"results: {results_path}")


if __name__ == "__main__":
    main()
