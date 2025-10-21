# Environment-specific variables
# Common variables are in variables-shared.tf (symlinked from _shared/)

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "test"
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
