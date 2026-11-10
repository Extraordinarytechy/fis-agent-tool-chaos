variable "region" {
  description = "AWS Region. FIS needs one config bucket per Region."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for every resource name."
  type        = string
  default     = "fatc"
}

variable "lambda_architecture" {
  description = "arm64 or x86_64. Must match the FIS extension layer."
  type        = string
  default     = "arm64"
}

variable "tool_timeout_seconds" {
  description = "Lambda timeout for the tools. Must be longer than delay_ms, otherwise the delayed invocation dies before it commits and you lose the in-flight case."
  type        = number
  default     = 30
}

variable "fault_duration" {
  description = "How long each FIS action stays active (ISO 8601). The harness stops the experiment early when it is done, and you pay per action-minute."
  type        = string
  default     = "PT10M"
}

variable "invocation_percentage" {
  description = "Percentage of issue_refund invocations that get the fault."
  type        = number
  default     = 100
}

variable "delay_ms" {
  description = "Startup delay injected by the in-flight experiment. Keep it longer than the caller's read timeout and shorter than tool_timeout_seconds."
  type        = number
  default     = 8000
}

variable "slow_poll_interval_seconds" {
  description = "How often the FIS extension checks S3 for a fault while idle. The default is 60. Lower means faster ramp-up but more S3 GETs."
  type        = number
  default     = 15
}

variable "over_refund_budget_usd" {
  description = "Stop condition. If duplicated refunds exceed this many dollars inside one minute, FIS halts the experiment."
  type        = number
  default     = 500
}
