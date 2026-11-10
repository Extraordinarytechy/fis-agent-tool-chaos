#!/usr/bin/env bash
# Prove the stop condition works: lower the over-refund budget to $40/minute,
# run a fault that duplicates refunds quickly, and let FIS halt the experiment itself.
# The budget is put back to the default afterwards.
set -u
cd "$(dirname "$0")/.."
PY="${PY:-python}"

( cd infra && terraform apply -auto-approve -input=false -no-color -var over_refund_budget_usd=40 | tail -3 )
"$PY" harness/run_experiment.py --fault inflight --contract v1 --episodes 8 --sdk-attempts 3
echo "alarm state:"
aws cloudwatch describe-alarms --alarm-names fatc-over-refund-budget \
  --query "MetricAlarms[0].[StateValue,StateReason]" --output text
( cd infra && terraform apply -auto-approve -input=false -no-color | tail -3 )
echo "STOP_TEST_DONE"
