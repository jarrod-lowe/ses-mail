# Environment-specific variables
# Common variables are in variables-shared.tf (symlinked from _shared/)

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "prod"
}
