"""Aggregate every results/*.jsonl into one markdown table.

Usage: python scripts/summarize.py [results_dir]
"""

import glob
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
paths = sorted(glob.glob(os.path.join(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results"), "*.jsonl")))

groups = defaultdict(list)
for p in paths:
    for line in open(p):
        r = json.loads(line)
        model = r["model"].split(".")[-1].replace("-v1:0", "")
        groups[(r["fault"], r["contract"], model, r.get("sdk_attempts", 1))].append(r)

print("| Fault | Contract | Model | SDK attempts | Episodes | Episodes with duplicates | Extra refunds | Refunded twice+ but claimed REFUNDED | Claimed NOT_REFUNDED while money moved | Claims (R/N/U) | Tool retries/episode |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for key in sorted(groups):
    rs = groups[key]
    n = len(rs)
    dup_eps = sum(1 for r in rs if r["duplicate_side_effects"] > 0)
    extra = sum(r["duplicate_side_effects"] for r in rs)
    dup_ok = sum(1 for r in rs if r["outcome"] == "DUPLICATE_CLAIMED_OK")
    denied = sum(1 for r in rs if (r["claim"] or {}).get("status") == "NOT_REFUNDED" and r["committed_refunds"] > 0)
    claims = Counter((r["claim"] or {}).get("status", "NONE") for r in rs)
    retries = sum(max(0, sum(1 for t in r["trace"] if t["tool"] == "issue_refund") - 1) for r in rs) / n
    print(f"| {key[0]} | {key[1]} | {key[2]} | {key[3]} | {n} | {dup_eps}/{n} | {extra} | {dup_ok} | {denied} | "
          f"{claims.get('REFUNDED',0)}/{claims.get('NOT_REFUNDED',0)}/{claims.get('UNKNOWN',0)} | {retries:.1f} |")

print()
print("files:", len(paths))
