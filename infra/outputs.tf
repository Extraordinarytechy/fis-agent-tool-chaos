output "region" {
  value = var.region
}

output "issue_refund_function" {
  value = aws_lambda_function.issue_refund.function_name
}

output "get_order_function" {
  value = aws_lambda_function.get_order.function_name
}

output "orders_table" {
  value = aws_dynamodb_table.orders.name
}

output "refunds_table" {
  value = aws_dynamodb_table.refunds.name
}

output "fis_config_bucket" {
  value = aws_s3_bucket.fis_config.bucket
}

output "fis_extension_layer_arn" {
  value = nonsensitive(data.aws_ssm_parameter.fis_extension.value)
}

output "experiment_templates" {
  value = { for k, t in aws_fis_experiment_template.fault : k => t.id }
}

output "stop_condition_alarm" {
  value = aws_cloudwatch_metric_alarm.over_refund_budget.alarm_name
}
