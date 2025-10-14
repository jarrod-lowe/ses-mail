output "dns_configuration_summary" {
  description = "DNS configuration instructions grouped by domain"
  value       = module.ses_mail.dns_configuration_summary
}

output "ses_receipt_rule_set" {
  description = "Name of the SES receipt rule set"
  value       = module.ses_mail.ses_receipt_rule_set
}

output "lambda_function_name" {
  description = "Name of the Lambda function"
  value       = module.ses_mail.lambda_function_name
}

output "s3_bucket_name" {
  description = "Name of the S3 bucket for email storage"
  value       = module.ses_mail.s3_bucket_name
}
