data "aws_caller_identity" "current" {}

locals {
  account_id      = data.aws_caller_identity.current.account_id
  fis_config_path = "FisConfigs/"
}

# ---------------------------------------------------------------------------
# S3 bucket the FIS service and the Lambda extension use to talk to each other.
# FIS writes the active fault config under FisConfigs/, the extension polls it.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "fis_config" {
  bucket = "${var.name_prefix}-fis-config-${local.account_id}-${var.region}"

  # FIS leaves config objects behind if an experiment is interrupted.
  # force_destroy lets `terraform destroy` empty the bucket instead of failing.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "fis_config" {
  bucket                  = aws_s3_bucket.fis_config.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "fis_config" {
  bucket = aws_s3_bucket.fis_config.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ---------------------------------------------------------------------------
# DynamoDB. `orders` holds the business data; `refunds` is the ledger the
# checker treats as ground truth: one item per committed refund.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "orders" {
  name         = "${var.name_prefix}-orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "order_id"

  attribute {
    name = "order_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "refunds" {
  name         = "${var.name_prefix}-refunds"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "order_id"
  range_key    = "refund_id"

  attribute {
    name = "order_id"
    type = "S"
  }

  attribute {
    name = "refund_id"
    type = "S"
  }
}
