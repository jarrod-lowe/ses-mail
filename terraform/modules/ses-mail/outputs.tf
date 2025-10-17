# Outputs for DNS configuration (grouped by domain)

output "domain_verification_tokens" {
  description = "TXT record values for domain verification (by domain)"
  value = {
    for domain, identity in aws_ses_domain_identity.main :
    domain => identity.verification_token
  }
}

output "dkim_tokens" {
  description = "DKIM tokens for email authentication (by domain)"
  value = {
    for domain, dkim in aws_ses_domain_dkim.main :
    domain => dkim.dkim_tokens
  }
}

# Additional outputs for reference

output "s3_bucket_name" {
  description = "Name of the S3 bucket storing emails"
  value       = aws_s3_bucket.email_storage.id
}

output "s3_bucket_arn" {
  description = "ARN of the S3 bucket storing emails"
  value       = aws_s3_bucket.email_storage.arn
}

output "lambda_function_name" {
  description = "Name of the email processor Lambda function"
  value       = aws_lambda_function.email_processor.function_name
}

output "lambda_function_arn" {
  description = "ARN of the email processor Lambda function"
  value       = aws_lambda_function.email_processor.arn
}

output "ses_receipt_rule_set" {
  description = "Name of the SES receipt rule set"
  value       = aws_ses_receipt_rule_set.main.rule_set_name
}

output "ssm_parameter_name" {
  description = "SSM Parameter Store parameter name for Gmail token"
  value       = aws_ssm_parameter.gmail_token.name
}

output "cloudwatch_dashboard_url" {
  description = "URL to the CloudWatch dashboard"
  value       = "https://console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.ses_mail.dashboard_name}"
}

output "dynamodb_routing_table_name" {
  description = "Name of the DynamoDB routing rules table"
  value       = aws_dynamodb_table.email_routing.name
}

output "dynamodb_routing_table_arn" {
  description = "ARN of the DynamoDB routing rules table"
  value       = aws_dynamodb_table.email_routing.arn
}

output "dns_configuration_summary" {
  description = "Summary of all DNS records to configure in Route53, grouped by domain"
  value = {
    note = "Add these records to your Route53 hosted zone in the other AWS account"
    domains = {
      for domain in var.domain : domain => concat(
        [
          {
            name    = "_amazonses.${domain}"
            type    = "TXT"
            value   = aws_ses_domain_identity.main[domain].verification_token
            purpose = "SES domain verification"
          },
          {
            name     = domain
            type     = "MX"
            priority = 10
            value    = "inbound-smtp.${var.aws_region}.amazonaws.com"
            purpose  = "Email receiving"
          },
          {
            name    = "_dmarc.${domain}"
            type    = "TXT"
            value   = var.dmarc_rua_prefix != "" ? "v=DMARC1; p=reject; rua=mailto:${var.dmarc_rua_prefix}@${domain}" : "v=DMARC1; p=reject"
            purpose = "DMARC policy - prevents domain spoofing"
          }
        ],
        [
          for token in aws_ses_domain_dkim.main[domain].dkim_tokens : {
            name    = "${token}._domainkey.${domain}"
            type    = "CNAME"
            value   = "${token}.dkim.amazonses.com"
            purpose = "DKIM authentication"
          }
        ],
        var.mta_sts_mode != "none" ? [
          {
            name    = "_mta-sts.${domain}"
            type    = "TXT"
            value   = "v=STSv1; id=${local.mta_sts_policy_id}"
            purpose = "MTA-STS policy ID"
          },
          {
            name    = "mta-sts.${domain}"
            type    = "CNAME"
            value   = aws_cloudfront_distribution.mta_sts[domain].domain_name
            purpose = "MTA-STS policy endpoint"
          }
        ] : [],
        var.tlsrpt_rua_prefix != "" ? [
          {
            name    = "_smtp._tls.${domain}"
            type    = "TXT"
            value   = "v=TLSRPTv1; rua=mailto:${var.tlsrpt_rua_prefix}@${domain}"
            purpose = "TLS reporting"
          }
        ] : [],
        var.mta_sts_mode != "none" ? [
          for record in aws_acm_certificate.mta_sts[domain].domain_validation_options : {
            name    = record.resource_record_name
            type    = record.resource_record_type
            value   = record.resource_record_value
            purpose = "ACM certificate validation for mta-sts.${domain}"
          }
        ] : []
      )
    }
  }
}
