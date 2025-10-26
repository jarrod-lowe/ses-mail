variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "ap-southeast-2"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}

variable "domain" {
  description = "List of domains for receiving emails"
  type        = list(string)

  validation {
    condition     = length(var.domain) > 0
    error_message = "At least one domain must be provided"
  }
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

variable "dmarc_rua_prefix" {
  description = "Email prefix for DMARC aggregate reports (domain will be appended automatically)"
  type        = string
  default     = "dmarc"
}

variable "tlsrpt_rua_prefix" {
  description = "Email prefix for TLS failure reports (domain will be appended automatically)"
  type        = string
  default     = "tlsrpt"
}

variable "spf_include_domains" {
  description = "Additional domains to include in SPF record (e.g., _spf.google.com for Google Workspace). amazonses.com is always included."
  type        = list(string)
  default     = []
}

variable "spf_a_records" {
  description = "Additional A record hostnames to authorize in SPF (e.g., mail.example.com). Use for specific mail servers."
  type        = list(string)
  default     = []
}

variable "spf_mx_records" {
  description = "Additional MX record hostnames to authorize in SPF (e.g., mail-in.example.com). Use only if these servers SEND email on behalf of your domain."
  type        = list(string)
  default     = []
}

variable "spf_policy" {
  description = "SPF policy for unauthorized senders: softfail (~all) for testing, fail (-all) for production"
  type        = string
  default     = "softfail"

  validation {
    condition     = contains(["softfail", "fail"], var.spf_policy)
    error_message = "SPF policy must be either 'softfail' (~all) or 'fail' (-all)"
  }
}

variable "backup_mx_records" {
  description = "Backup MX records for email receiving. List of objects with hostname and priority (lower priority = higher preference)."
  type = list(object({
    hostname = string
    priority = number
  }))
  default = []
}

variable "mail_from_subdomain" {
  description = "Subdomain to use for custom MAIL FROM domain (e.g., 'bounce' creates bounce.example.com)"
  type        = string
  default     = "bounce"
}
