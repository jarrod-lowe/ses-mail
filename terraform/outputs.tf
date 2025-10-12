# Outputs for DNS configuration

output "domain_verification_token" {
  description = "TXT record value for domain verification"
  value       = aws_ses_domain_identity.main.verification_token
}

output "domain_verification_record" {
  description = "Full DNS TXT record for domain verification"
  value = {
    name  = "_amazonses.${var.domain}"
    type  = "TXT"
    value = aws_ses_domain_identity.main.verification_token
  }
}

output "mx_record" {
  description = "MX record for receiving emails"
  value = {
    name     = var.domain
    type     = "MX"
    priority = 10
    value    = "inbound-smtp.${var.aws_region}.amazonaws.com"
  }
}

output "dkim_tokens" {
  description = "DKIM CNAME records for email authentication"
  value = [
    for token in aws_ses_domain_dkim.main.dkim_tokens : {
      name  = "${token}._domainkey.${var.domain}"
      type  = "CNAME"
      value = "${token}.dkim.amazonses.com"
    }
  ]
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

output "ssm_parameter_name" {
  description = "SSM Parameter Store parameter name for Gmail token"
  value       = aws_ssm_parameter.gmail_token.name
}

output "cloudwatch_dashboard_url" {
  description = "URL to the CloudWatch dashboard"
  value       = "https://console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.ses_mail.dashboard_name}"
}

output "dns_configuration_summary" {
  description = "Summary of all DNS records to configure in Route53"
  value = {
    note = "Add these records to your Route53 hosted zone in the other AWS account"
    records = concat(
      [
        {
          name     = "_amazonses.${var.domain}"
          type     = "TXT"
          value    = aws_ses_domain_identity.main.verification_token
          purpose  = "SES domain verification"
        },
        {
          name     = var.domain
          type     = "MX"
          priority = 10
          value    = "inbound-smtp.${var.aws_region}.amazonaws.com"
          purpose  = "Email receiving"
        },
        {
          name    = "_dmarc.${var.domain}"
          type    = "TXT"
          value   = var.dmarc_rua_email != "" ? "v=DMARC1; p=reject; rua=mailto:${var.dmarc_rua_email}" : "v=DMARC1; p=reject"
          purpose = "DMARC policy - prevents domain spoofing"
        }
      ],
      [
        for token in aws_ses_domain_dkim.main.dkim_tokens : {
          name    = "${token}._domainkey.${var.domain}"
          type    = "CNAME"
          value   = "${token}.dkim.amazonses.com"
          purpose = "DKIM authentication"
        }
      ],
      var.mta_sts_mode != "none" ? [
        {
          name    = "_mta-sts.${var.domain}"
          type    = "TXT"
          value   = "v=STSv1; id=${local.mta_sts_policy_id}"
          purpose = "MTA-STS policy ID"
        },
        {
          name    = "mta-sts.${var.domain}"
          type    = "CNAME"
          value   = aws_cloudfront_distribution.mta_sts[0].domain_name
          purpose = "MTA-STS policy endpoint"
        }
      ] : [],
      var.tlsrpt_rua_email != "" ? [
        {
          name    = "_smtp._tls.${var.domain}"
          type    = "TXT"
          value   = "v=TLSRPTv1; rua=mailto:${var.tlsrpt_rua_email}"
          purpose = "TLS reporting"
        }
      ] : [],
      var.mta_sts_mode != "none" ? [
        for record in aws_acm_certificate.mta_sts[0].domain_validation_options : {
          name    = record.resource_record_name
          type    = record.resource_record_type
          value   = record.resource_record_value
          purpose = "ACM certificate validation for mta-sts.${var.domain}"
        }
      ] : []
    )
  }
}
