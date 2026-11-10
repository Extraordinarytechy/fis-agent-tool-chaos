#!/usr/bin/env bash
# Runs the fault x contract x model matrix, one FIS experiment per row.
# Each row starts its own experiment and stops it when the episodes finish.
# Rough cost: about 2-4 FIS action-minutes per row at $0.10/action-minute, plus Bedrock tokens.
set -u
cd "$(dirname "$0")/.."
PY="${PY:-python}"
LITE=us.amazon.nova-2-lite-v1:0
PRO=us.amazon.nova-pro-v1:0

run() {
  echo "===== $* ====="
  "$PY" harness/run_experiment.py "$@" || echo "ROW FAILED: $*"
}

run --fault inflight  --contract v3 --episodes 5 --model "$LITE"
run --fault inflight  --contract v2 --episodes 5 --model "$LITE"
run --fault inflight  --contract v1 --episodes 5 --model "$LITE"
run --fault lost_ack  --contract v1 --episodes 5 --model "$LITE"
run --fault lost_ack  --contract v3 --episodes 3 --model "$LITE"
run --fault precommit --contract v1 --episodes 3 --model "$LITE"
# Does the SDK retry a timed-out Invoke on its own? Same fault, SDK allowed 3 attempts.
run --fault inflight  --contract v1 --episodes 3 --model "$LITE" --sdk-attempts 3
# Is it the model? Same faults with a stronger model.
run --fault inflight  --contract v1 --episodes 3 --model "$PRO"
run --fault inflight  --contract v3 --episodes 3 --model "$PRO"
echo "MATRIX_DONE"
