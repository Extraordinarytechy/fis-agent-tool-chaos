# CloudWatch dashboard: duplicate refunds per minute by contract and model, and
# over-refunded dollars per minute (the metric behind the FIS stop condition).
variable "dashboard_models" {
  description = "Bedrock model IDs to plot on the dashboard."
  type        = list(string)
  default     = ["us.amazon.nova-2-lite-v1:0", "us.amazon.nova-pro-v1:0"]
}

variable "dashboard_fault" {
  description = "Fault template whose duplicate refunds are plotted."
  type        = string
  default     = "inflight"
}

locals {
  dashboard_series = flatten([
    for mi, model in var.dashboard_models : [
      for contract in ["v1", "v2", "v3"] : {
        id       = "m${mi}${contract}"
        model    = model
        contract = contract
      }
    ]
  ])
}

resource "aws_cloudwatch_dashboard" "results" {
  dashboard_name = "${var.name_prefix}-results"

  dashboard_body = jsonencode({
    start = "-PT3H"
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 24
        height = 9
        properties = {
          title   = "Duplicate refunds per minute, ${var.dashboard_fault} fault"
          view    = "timeSeries"
          stacked = false
          region  = var.region
          stat    = "Sum"
          period  = 60
          legend  = { position = "bottom" }
          yAxis   = { left = { min = 0, label = "extra refunds", showUnits = false } }
          # Episodes do not report every minute; FILL draws missing minutes as 0.
          metrics = concat(
            [for s in local.dashboard_series :
              [{ expression = "FILL(${s.id}, 0)", id = "e_${s.id}", label = "${s.model}, ${s.contract}" }]
            ],
            [for s in local.dashboard_series :
              ["FisAgentChaos", "DuplicateSideEffects", "Fault", var.dashboard_fault,
              "Contract", s.contract, "Model", s.model, { id = s.id, visible = false }]
            ]
          )
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 9
        width  = 24
        height = 6
        properties = {
          title  = "Over-refunded dollars per minute (FIS stop condition metric)"
          view   = "timeSeries"
          region = var.region
          stat   = "Sum"
          period = 60
          legend = { position = "bottom" }
          metrics = [
            [{ expression = "FILL(m1, 0)", id = "e1", label = "OverRefundUSD", color = "#d62728" }],
            ["FisAgentChaos", "OverRefundUSD", { id = "m1", visible = false }],
          ]
          annotations = {
            horizontal = [{ label = "stop-condition budget", value = var.over_refund_budget_usd, color = "#9467bd" }]
          }
        }
      },
    ]
  })
}

output "dashboard_url" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.results.dashboard_name}"
}
