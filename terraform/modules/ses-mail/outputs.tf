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

output "resource_group_name" {
  description = "Name of the AWS Resource Group for this environment"
  value       = aws_resourcegroups_group.ses_mail.name
}

output "resource_group_arn" {
  description = "ARN of the AWS Resource Group for this environment"
  value       = aws_resourcegroups_group.ses_mail.arn
}

output "resource_group_url" {
  description = "URL to view the Resource Group in AWS Console"
  value       = "https://console.aws.amazon.com/resource-groups/group/${aws_resourcegroups_group.ses_mail.name}"
}

output "appregistry_application_id" {
  description = "ID of the AWS Service Catalog AppRegistry application"
  value       = aws_servicecatalogappregistry_application.ses_mail.id
}

output "appregistry_application_arn" {
  description = "ARN of the AWS Service Catalog AppRegistry application"
  value       = aws_servicecatalogappregistry_application.ses_mail.arn
}

output "appregistry_application_tag" {
  description = "Application tag that can be used to associate resources with this application"
  value       = aws_servicecatalogappregistry_application.ses_mail.application_tag
}

output "myapplications_url" {
  description = "URL to view this application in AWS Console myApplications"
  value       = "https://console.aws.amazon.com/systems-manager/appmanager/application/AWS_AppRegistry_Application-${aws_servicecatalogappregistry_application.ses_mail.id}"
}

output "eventbridge_event_bus_name" {
  description = "Name of the EventBridge Event Bus for email routing"
  value       = aws_cloudwatch_event_bus.email_routing.name
}

output "eventbridge_event_bus_arn" {
  description = "ARN of the EventBridge Event Bus for email routing"
  value       = aws_cloudwatch_event_bus.email_routing.arn
}

output "eventbridge_gmail_rule_name" {
  description = "Name of the EventBridge rule for Gmail forwarding"
  value       = aws_cloudwatch_event_rule.gmail_forwarder.name
}

output "eventbridge_gmail_rule_arn" {
  description = "ARN of the EventBridge rule for Gmail forwarding"
  value       = aws_cloudwatch_event_rule.gmail_forwarder.arn
}

output "eventbridge_bouncer_rule_name" {
  description = "Name of the EventBridge rule for bouncer"
  value       = aws_cloudwatch_event_rule.bouncer.name
}

output "eventbridge_bouncer_rule_arn" {
  description = "ARN of the EventBridge rule for bouncer"
  value       = aws_cloudwatch_event_rule.bouncer.arn
}

# SMTP Configuration Outputs

output "smtp_endpoint" {
  description = "SES SMTP server endpoint for email client configuration"
  value       = "email-smtp.${var.aws_region}.amazonaws.com"
}

output "smtp_ports" {
  description = "Recommended SMTP ports and security settings"
  value = {
    recommended = {
      port     = 587
      security = "STARTTLS"
      note     = "Recommended for most email clients (Gmail, Outlook, Thunderbird)"
    }
    alternative = {
      port     = 465
      security = "TLS Wrapper"
      note     = "Alternative TLS connection method"
    }
    legacy = {
      port     = 25
      security = "STARTTLS"
      note     = "Often blocked by ISPs, not recommended"
    }
  }
}

output "smtp_region" {
  description = "AWS region for SES SMTP endpoint"
  value       = var.aws_region
}

output "spf_record" {
  description = "Recommended SPF record for each domain to authorize SES for outbound email"
  value = {
    for domain in var.domain : domain => {
      name = domain
      type = "TXT"
      value = join(" ", concat(
        ["v=spf1"],
        [for a_record in var.spf_a_records : "a:${a_record}"],
        [for mx_record in var.spf_mx_records : "mx:${mx_record}"],
        ["include:amazonses.com"],
        [for include in var.spf_include_domains : "include:${include}"],
        [var.spf_policy == "fail" ? "-all" : "~all"]
      ))
      purpose = "Authorize SES to send emails on behalf of ${domain}"
      note    = var.spf_policy == "fail" ? "Hard fail (-all) - unauthorized senders will be rejected" : "Soft fail (~all) - unauthorized senders marked as suspicious but accepted"
    }
  }
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
            purpose  = "Primary email receiving (SES)"
          },
        ],
        [
          for mx in var.backup_mx_records : {
            name     = domain
            type     = "MX"
            priority = mx.priority
            value    = mx.hostname
            purpose  = "Backup email receiving (priority ${mx.priority})"
          }
        ],
        [
          {
            name = domain
            type = "TXT"
            value = join(" ", concat(
              ["v=spf1"],
              [for a_record in var.spf_a_records : "a:${a_record}"],
              [for mx_record in var.spf_mx_records : "mx:${mx_record}"],
              ["include:amazonses.com"],
              [for include in var.spf_include_domains : "include:${include}"],
              [var.spf_policy == "fail" ? "-all" : "~all"]
            ))
            purpose = "SPF - authorize SES to send emails (${var.spf_policy == "fail" ? "hard fail" : "soft fail"})"
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
        [
          {
            name     = "${var.mail_from_subdomain}.${domain}"
            type     = "MX"
            priority = 10
            value    = "feedback-smtp.${var.aws_region}.amazonses.com"
            purpose  = "Custom MAIL FROM domain - handles bounce notifications"
          },
          {
            name    = "${var.mail_from_subdomain}.${domain}"
            type    = "TXT"
            value   = "v=spf1 include:amazonses.com ${var.spf_policy == "fail" ? "-all" : "~all"}"
            purpose = "SPF for custom MAIL FROM domain"
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

# Cognito User Pool Outputs

output "cognito_user_pool_id" {
  description = "ID of the Cognito User Pool for token management authentication"
  value       = aws_cognito_user_pool.main.id
}

output "cognito_user_pool_arn" {
  description = "ARN of the Cognito User Pool"
  value       = aws_cognito_user_pool.main.arn
}

output "cognito_user_pool_endpoint" {
  description = "Endpoint name of the Cognito User Pool"
  value       = aws_cognito_user_pool.main.endpoint
}

output "cognito_user_pool_client_id" {
  description = "Client ID for the Cognito User Pool web UI application"
  value       = aws_cognito_user_pool_client.main.id
}

output "cognito_user_pool_domain" {
  description = "Cognito User Pool domain for hosted UI"
  value       = aws_cognito_user_pool_domain.main.domain
}

output "cognito_hosted_ui_url" {
  description = "URL for the Cognito hosted UI login page"
  value       = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.aws_region}.amazoncognito.com/login?client_id=${aws_cognito_user_pool_client.main.id}&response_type=code&redirect_uri=https://mta-sts.${var.domain[0]}/token-management/callback"
}

output "cognito_callback_urls" {
  description = "Configured callback URLs for OAuth authentication"
  value       = local.cognito_callback_urls
}

output "cognito_logout_urls" {
  description = "Configured logout URLs for OAuth"
  value       = local.cognito_logout_urls
}

# ==============================================================================
# Token Management API Gateway Outputs (Task 6.1)
# ==============================================================================

output "token_api_endpoint" {
  description = "API Gateway invoke URL for token management API"
  value       = aws_apigatewayv2_api.token_management.api_endpoint
}

output "token_api_id" {
  description = "API Gateway ID for token management API (for CloudFront integration)"
  value       = aws_apigatewayv2_api.token_management.id
}

output "token_api_execution_arn" {
  description = "Execution ARN for token management API (for permissions)"
  value       = aws_apigatewayv2_api.token_management.execution_arn
}
