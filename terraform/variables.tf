variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "ap-southeast-2"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "domain" {
  description = "Domain for receiving emails"
  type        = string
}

variable "email_bucket_name" {
  description = "S3 bucket name for storing emails"
  type        = string
}

variable "email_retention_days" {
  description = "Number of days to retain emails in S3"
  type        = number
  default     = 90
}

variable "alarm_sns_topic_arn" {
  description = "SNS topic ARN for alarm notifications"
  type        = string
}

variable "alarm_email_count_threshold" {
  description = "Threshold for email count alarm (emails per 5 minutes)"
  type        = number
  default     = 100
}

variable "alarm_rejection_rate_threshold" {
  description = "Threshold for rejection rate alarm (percentage)"
  type        = number
  default     = 50
}

variable "mta_sts_mode" {
  description = "MTA-STS policy mode (testing, enforce, none)"
  type        = string
  default     = "testing"

  validation {
    condition     = contains(["testing", "enforce", "none"], var.mta_sts_mode)
    error_message = "MTA-STS mode must be one of: testing, enforce, none"
  }
}

variable "dmarc_rua_email" {
  description = "Email address for DMARC aggregate reports (optional)"
  type        = string
  default     = ""
}

variable "tlsrpt_rua_email" {
  description = "Email address for TLS failure reports (optional)"
  type        = string
  default     = ""
}
