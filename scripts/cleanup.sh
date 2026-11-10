#!/usr/bin/env bash
# Tear everything down. Safe to run more than once.
set -u
cd "$(dirname "$0")/../infra"
REGION="$(terraform output -raw region 2>/dev/null || echo us-east-1)"

echo "1/3 stopping any FIS experiment still running for this project"
for id in $(aws fis list-experiments --region "$REGION" \
    --query "experiments[?state.status=='running' || state.status=='initiating'].id" --output text); do
  tmpl=$(aws fis get-experiment --region "$REGION" --id "$id" --query experiment.experimentTemplateId --output text)
  if terraform output -json experiment_templates 2>/dev/null | grep -q "$tmpl"; then
    aws fis stop-experiment --region "$REGION" --id "$id" >/dev/null && echo "   stopped $id"
  fi
done

echo "2/3 terraform destroy (the FIS config bucket has force_destroy, so leftover fault configs don't block it)"
terraform destroy -auto-approve

echo "3/3 custom metrics in the FisAgentChaos namespace cannot be deleted; they expire on their own (no charge once no new data arrives)."
echo "If you attached iam/operator-policy.json to your IAM user or role for this project, remove it now."
