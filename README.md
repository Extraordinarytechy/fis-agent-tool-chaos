# fis-agent-tool-chaos

Fault-injection tests for the tools an AI agent calls. AWS Fault Injection Service (FIS)
breaks a Lambda tool in controlled ways, and a checker compares what the agent *reported*
with what *actually* committed.

A Bedrock agent (Converse API with tool use) refunds orders through two Lambda tools.
FIS injects faults into the refund tool. After each conversation the checker reads a
DynamoDB ledger of committed refunds, classifies the outcome, and publishes duplicate
side effects and false claims as CloudWatch metrics. A CloudWatch alarm on refunded
dollars acts as the FIS stop condition.

![Architecture: a local harness calls Amazon Bedrock and two Lambda tools; AWS FIS injects faults into issue_refund through an S3 configuration prefix; a checker compares the agent's claim with a DynamoDB ledger and publishes CloudWatch metrics that drive the FIS stop condition](docs/architecture.png)

## Faults

| Template | FIS action | What the tool does | What the caller sees |
|---|---|---|---|
| `precommit` | `aws:lambda:invocation-error`, `preventExecution=true` | Nothing | An error |
| `lost_ack` | `aws:lambda:invocation-error`, `preventExecution=false` | Commits the refund | An error |
| `inflight` | `aws:lambda:invocation-add-delay` (default 8 s) | Commits the refund after the delay | A timeout (default client read timeout 4 s) |

## Tool contracts

| Contract | Behaviour |
|---|---|
| `v1` | No idempotency key. Tool errors are passed to the model as raw text. |
| `v2` | The tool schema requires an `idempotency_key` chosen by the model. Errors are structured and report `commit_status: UNKNOWN`. |
| `v3` | Same as `v2`, but the harness attaches a deterministic key per refund request, so the model cannot omit or change it. |

`issue_refund` turns the key into the ledger item's sort key and writes with
`attribute_not_exists`, so a replay returns the original refund instead of creating a new one.

## Repository layout

```
infra/      Terraform: DynamoDB tables, Lambda tools with the FIS extension, FIS templates,
            stop-condition alarm, CloudWatch dashboard
tools/      Lambda handlers for issue_refund and get_order
harness/    agent.py (Converse tool loop), checker.py (ledger comparison and metrics),
            run_experiment.py (starts a fault, runs episodes, stops the fault)
scripts/    run_matrix.sh, stop_condition_test.sh, summarize.py, cleanup.sh
iam/        Additional IAM permissions needed by the operator running the tests
```

## Prerequisites

- An AWS account and CLI credentials that can manage Lambda, DynamoDB, S3, IAM, Bedrock,
  FIS, CloudWatch, CloudWatch Logs and read SSM public parameters.
  `iam/operator-policy.json` lists the FIS, CloudWatch, Logs and SSM statements that are
  often missing from developer roles.
- Access to an on-demand Bedrock model that supports tool use. The default is
  `us.amazon.nova-2-lite-v1:0`; pass `--model` to use another.
- Terraform 1.6 or later, Python 3.10 or later.

## Deploy

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-east-1
cd infra
terraform init
terraform apply
cd ..
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

The FIS Lambda extension layer ARN is read from the public SSM parameter
`/aws/service/fis/lambda-extension/AWS-FIS-extension-<arch>/1.x.x`, so it always matches
the current release for the Region.

## Run

```bash
# Baseline, no fault
python harness/run_experiment.py --fault none --contract v1 --episodes 2 --no-metrics

# In-flight fault, naive contract vs harness-attached key
python harness/run_experiment.py --fault inflight --contract v1 --episodes 5
python harness/run_experiment.py --fault inflight --contract v3 --episodes 5

# Full fault x contract x model matrix
bash scripts/run_matrix.sh

# Summary table of every run in results/
python scripts/summarize.py
```

Each run writes `results/<run-id>.jsonl` with the agent's claim, the ledger contents and
the full tool trace for every episode.

To confirm the stop condition halts an experiment, `scripts/stop_condition_test.sh`
lowers the over-refund budget to $40 per minute, runs a fault that duplicates refunds,
and restores the budget afterwards.

`terraform output dashboard_url` prints a link to a CloudWatch dashboard showing duplicate
refunds per minute by contract and model, and over-refunded dollars per minute.

## Operational notes

- **SDK retries.** `harness/agent.py` sets botocore `total_max_attempts` from
  `--sdk-attempts` (default 1). With retries enabled, a client-side read timeout on
  `Invoke` is retried by the SDK before the agent sees an error, and each retry can
  commit another refund. Keep SDK retries off for non-idempotent tools, or make the
  tool idempotent.
- **Fault ramp-up.** The FIS extension polls S3 for fault configuration. From idle this
  can take up to `AWS_FIS_SLOW_POLL_INTERVAL_SECONDS` (set to 15 here, AWS default 60)
  plus extension start-up. `run_experiment.py` sends no-op probes and waits until the
  fault is observed before starting episodes.
- **Late commits.** In the `inflight` fault the handler runs after the caller has given
  up. The checker waits `delay_ms + 6 s` before reading the ledger so late commits are
  counted.
- **Stop condition scope.** The alarm watches over-refunded dollars, not the duplicate
  count being measured, so a test run is not halted by the first duplicate.
- The FIS extension does not support Lambda response streaming, and SnapStart can delay
  fault pickup.

## Clean up

```bash
bash scripts/cleanup.sh
```

The script stops any running experiment from this stack and runs `terraform destroy`.
The FIS configuration bucket uses `force_destroy`, so leftover fault configuration
objects do not block deletion. Custom metrics in the `FisAgentChaos` namespace expire
on their own.

## Cost

FIS charges $0.10 per action-minute. `run_experiment.py` stops each experiment as soon
as its episodes finish, so a five-episode run uses a few action-minutes. Bedrock token
usage for Nova 2 Lite is a few cents for the full matrix. Lambda, DynamoDB on-demand
and S3 usage is negligible at this scale. Check current pricing for your Region.

## License

MIT. See [LICENSE](LICENSE).
