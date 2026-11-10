# ---------------------------------------------------------------------------
# FIS Lambda extension layer. The ARN changes with each extension release, so it
# is read from the public SSM parameter instead of being hardcoded.
# ---------------------------------------------------------------------------
data "aws_ssm_parameter" "fis_extension" {
  name = "/aws/service/fis/lambda-extension/AWS-FIS-extension-${var.lambda_architecture}/1.x.x"
}

data "archive_file" "issue_refund" {
  type        = "zip"
  source_dir  = "${path.module}/../tools/issue_refund"
  output_path = "${path.module}/build/issue_refund.zip"
}

data "archive_file" "get_order" {
  type        = "zip"
  source_dir  = "${path.module}/../tools/get_order"
  output_path = "${path.module}/build/get_order.zip"
}

# ---------------------------------------------------------------------------
# Execution role shared by both tools.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "tools" {
  name               = "${var.name_prefix}-tools-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "tools" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.issue_refund.arn}:*", "${aws_cloudwatch_log_group.get_order.arn}:*"]
  }

  statement {
    sid       = "Ledger"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.orders.arn, aws_dynamodb_table.refunds.arn]
  }

  # The FIS extension lists and reads the active fault config. Read-only, and
  # only under the FisConfigs/ prefix.
  statement {
    sid       = "FisConfigList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.fis_config.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.fis_config_path}*"]
    }
  }

  statement {
    sid       = "FisConfigRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.fis_config.arn}/${local.fis_config_path}*"]
  }
}

resource "aws_iam_role_policy" "tools" {
  name   = "${var.name_prefix}-tools"
  role   = aws_iam_role.tools.id
  policy = data.aws_iam_policy_document.tools.json
}

resource "aws_cloudwatch_log_group" "issue_refund" {
  name              = "/aws/lambda/${var.name_prefix}-issue-refund"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "get_order" {
  name              = "/aws/lambda/${var.name_prefix}-get-order"
  retention_in_days = 7
}

locals {
  table_env = {
    ORDERS_TABLE  = aws_dynamodb_table.orders.name
    REFUNDS_TABLE = aws_dynamodb_table.refunds.name
  }

  fis_env = {
    AWS_LAMBDA_EXEC_WRAPPER            = "/opt/aws-fis/bootstrap"
    AWS_FIS_CONFIGURATION_LOCATION     = "${aws_s3_bucket.fis_config.arn}/${local.fis_config_path}"
    AWS_FIS_POLL_MAX_WAIT_MILLISECONDS = "2000"
    AWS_FIS_SLOW_POLL_INTERVAL_SECONDS = tostring(var.slow_poll_interval_seconds)
    AWS_FIS_EXTENSION_METRICS          = "all"
  }
}

# Fault-injection target. Carries the FIS extension layer and wrapper.
resource "aws_lambda_function" "issue_refund" {
  function_name    = "${var.name_prefix}-issue-refund"
  role             = aws_iam_role.tools.arn
  runtime          = "python3.12"
  architectures    = [var.lambda_architecture]
  handler          = "handler.handler"
  filename         = data.archive_file.issue_refund.output_path
  source_code_hash = data.archive_file.issue_refund.output_base64sha256
  timeout          = var.tool_timeout_seconds
  memory_size      = 256
  layers           = [nonsensitive(data.aws_ssm_parameter.fis_extension.value)]

  environment {
    variables = merge(local.table_env, local.fis_env)
  }

  depends_on = [aws_cloudwatch_log_group.issue_refund, aws_iam_role_policy.tools]
}

# Read-back tool. Not a fault target, so the agent always has a reliable view
# of the ledger and any duplicate reflects a decision not to check it.
resource "aws_lambda_function" "get_order" {
  function_name    = "${var.name_prefix}-get-order"
  role             = aws_iam_role.tools.arn
  runtime          = "python3.12"
  architectures    = [var.lambda_architecture]
  handler          = "handler.handler"
  filename         = data.archive_file.get_order.output_path
  source_code_hash = data.archive_file.get_order.output_base64sha256
  timeout          = 10
  memory_size      = 256

  environment {
    variables = local.table_env
  }

  depends_on = [aws_cloudwatch_log_group.get_order, aws_iam_role_policy.tools]
}
