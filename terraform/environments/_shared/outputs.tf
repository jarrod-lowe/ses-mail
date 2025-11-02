# DNS and Domain Configuration

output "dns_configuration_summary" {
  description = "DNS configuration instructions grouped by domain"
  value       = module.ses_mail.dns_configuration_summary
}

output "spf_record" {
  description = "Recommended SPF record for each domain to authorize SES for outbound email"
  value       = module.ses_mail.spf_record
}

# SMTP Configuration

output "smtp_endpoint" {
  description = "SES SMTP server endpoint for email client configuration"
  value       = module.ses_mail.smtp_endpoint
}

output "smtp_ports" {
  description = "Recommended SMTP ports and security settings"
  value       = module.ses_mail.smtp_ports
}

output "smtp_region" {
  description = "AWS region for SES SMTP endpoint"
  value       = module.ses_mail.smtp_region
}

# Infrastructure Resources

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

# AWS Resource Organization

output "resource_group_name" {
  description = "Name of the AWS Resource Group for this environment"
  value       = module.ses_mail.resource_group_name
}

output "resource_group_url" {
  description = "URL to view the Resource Group in AWS Console"
  value       = module.ses_mail.resource_group_url
}

output "appregistry_application_id" {
  description = "ID of the AWS Service Catalog AppRegistry application"
  value       = module.ses_mail.appregistry_application_id
}

output "myapplications_url" {
  description = "URL to view this application in AWS Console myApplications"
  value       = module.ses_mail.myapplications_url
}

# Cognito User Pool Configuration

output "cognito_user_pool_id" {
  description = "ID of the Cognito User Pool for token management authentication"
  value       = module.ses_mail.cognito_user_pool_id
}

output "cognito_user_pool_client_id" {
  description = "Client ID for the Cognito User Pool web UI application"
  value       = module.ses_mail.cognito_user_pool_client_id
}

output "cognito_hosted_ui_url" {
  description = "URL for the Cognito hosted UI login page"
  value       = module.ses_mail.cognito_hosted_ui_url
}

output "cognito_callback_urls" {
  description = "Configured callback URLs for OAuth authentication"
  value       = module.ses_mail.cognito_callback_urls
}
