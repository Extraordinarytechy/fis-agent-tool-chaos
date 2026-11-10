# ---------------------------------------------------------------------------
# Stop condition. It limits the blast radius (over-refunded dollars per minute)
# rather than watching the duplicate count under test, so a run is not halted
# by the first duplicate it is designed to produce.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "over_refund_budget" {
  alarm_name          = "${var.name_prefix}-over-refund-budget"
  alarm_description   = "Duplicated refund dollars in one minute exceeded the experiment budget. FIS stops the experiment."
  namespace           = "FisAgentChaos"
  metric_name         = "OverRefundUSD"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = var.over_refund_budget_usd
  comparison_operator = "GreaterThanThreshold"
  # No episodes means no data. That must not count as a breach.
  treat_missing_data = "notBreaching"
}

# ---------------------------------------------------------------------------
# Role FIS assumes to run the experiments.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "fis_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["fis.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "fis" {
  name               = "${var.name_prefix}-fis-role"
  assume_role_policy = data.aws_iam_policy_document.fis_trust.json
}

data "aws_iam_policy_document" "fis" {
  # FIS writes the fault config the extension reads, and deletes it when the action ends.
  statement {
    sid       = "WriteFaultConfig"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.fis_config.arn}/${local.fis_config_path}*"]
  }

  statement {
    sid       = "ReadTargetFunction"
    actions   = ["lambda:GetFunction"]
    resources = [aws_lambda_function.issue_refund.arn]
  }

  statement {
    sid       = "ResolveTargets"
    actions   = ["tag:GetResources"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "fis" {
  name   = "${var.name_prefix}-fis"
  role   = aws_iam_role.fis.id
  policy = data.aws_iam_policy_document.fis.json
}

# ---------------------------------------------------------------------------
# Three faults, one per template, so each run tests exactly one failure mode.
#
#   precommit : error returned, handler never runs      -> nothing committed
#   lost_ack  : handler runs and commits, caller gets an error
#   inflight  : invocation is held before the handler; the caller gives up,
#               then the handler runs and commits later
# ---------------------------------------------------------------------------
locals {
  faults = {
    precommit = {
      description = "issue_refund fails before the handler runs (nothing is committed)"
      action_id   = "aws:lambda:invocation-error"
      parameters = {
        duration             = var.fault_duration
        invocationPercentage = tostring(var.invocation_percentage)
        preventExecution     = "true"
      }
    }
    lost_ack = {
      description = "issue_refund commits, then the caller receives an error"
      action_id   = "aws:lambda:invocation-error"
      parameters = {
        duration             = var.fault_duration
        invocationPercentage = tostring(var.invocation_percentage)
        preventExecution     = "false"
      }
    }
    inflight = {
      description = "issue_refund is delayed past the caller's timeout, then commits"
      action_id   = "aws:lambda:invocation-add-delay"
      parameters = {
        duration                 = var.fault_duration
        invocationPercentage     = tostring(var.invocation_percentage)
        startupDelayMilliseconds = tostring(var.delay_ms)
      }
    }
  }
}

resource "aws_fis_experiment_template" "fault" {
  for_each = local.faults

  description = each.value.description
  role_arn    = aws_iam_role.fis.arn

  stop_condition {
    source = "aws:cloudwatch:alarm"
    value  = aws_cloudwatch_metric_alarm.over_refund_budget.arn
  }

  action {
    name      = each.key
    action_id = each.value.action_id

    dynamic "parameter" {
      for_each = each.value.parameters
      content {
        key   = parameter.key
        value = parameter.value
      }
    }

    target {
      key   = "Functions"
      value = "refund-tool"
    }
  }

  target {
    name           = "refund-tool"
    resource_type  = "aws:lambda:function"
    selection_mode = "ALL"
    resource_arns  = [aws_lambda_function.issue_refund.arn]
  }

  tags = {
    Name  = "${var.name_prefix}-${each.key}"
    Fault = each.key
  }

  depends_on = [aws_iam_role_policy.fis]
}
