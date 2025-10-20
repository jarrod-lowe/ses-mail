# X-Ray resource policy to allow SNS to send trace data
# Required for SNS active tracing to work properly

# IAM policy document that grants SNS permissions to send trace data to X-Ray
data "aws_iam_policy_document" "xray_sns_tracing" {
  statement {
    sid = "AllowSNSActiveTracing"

    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }

    actions = [
      "xray:PutTraceSegments",
      "xray:GetSamplingRules",
      "xray:GetSamplingTargets",
    ]

    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "StringLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:sns:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

# X-Ray resource policy resource
# Note: AWS has a limit of 5 X-Ray resource policies per region per account
resource "aws_xray_resource_policy" "sns_tracing" {
  policy_name     = "ses-mail-sns-tracing-${var.environment}"
  policy_document = data.aws_iam_policy_document.xray_sns_tracing.json
}

# ===========================
# X-Ray Transaction Search Configuration
# ===========================
#
# Transaction Search enables querying and analysing X-Ray traces in CloudWatch.
# This is an account-level setting configured via CloudFormation since there's
# no native Terraform resource yet.

# CloudWatch Logs resource policy to allow X-Ray to write span data
resource "aws_cloudwatch_log_resource_policy" "xray_transaction_search" {
  policy_name = "ses-mail-xray-transaction-search-${var.environment}"

  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TransactionSearchXRayAccess"
        Effect = "Allow"
        Principal = {
          Service = "xray.amazonaws.com"
        }
        Action = "logs:PutLogEvents"
        Resource = [
          "arn:aws:logs:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:log-group:aws/spans:*",
          "arn:aws:logs:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:log-group:/aws/application-signals/data:*"
        ]
        Condition = {
          ArnLike = {
            "aws:SourceArn" = "arn:aws:xray:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:*"
          }
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

# CloudFormation stack for X-Ray Transaction Search configuration
# Uses CloudFormation because aws_xray_transaction_search_config doesn't exist in Terraform yet
# This will not apply - enable manually
#resource "aws_cloudformation_stack" "xray_transaction_search" {
#  name = "ses-mail-xray-transaction-search-${var.environment}"
#
#  template_body = jsonencode({
#    AWSTemplateFormatVersion = "2010-09-09"
#    Description              = "X-Ray Transaction Search configuration for SES mail processing pipeline"
#
#    Resources = {
#      TransactionSearchConfig = {
#        Type = "AWS::XRay::TransactionSearchConfig"
#        Properties = {
#          # IndexingPercentage: 1-100
#          # 1% is free tier eligible
#          #IndexingPercentage = 1
#          #Field cannot be set
#        }
#      }
#    }
#
#    Outputs = {
#      IndexingPercentage = {
#        Description = "Percentage of transactions indexed for search"
#        Value = {
#          "Fn::GetAtt" = ["TransactionSearchConfig", "IndexingPercentage"]
#        }
#      }
#    }
#  })
#
#  capabilities = []
#
#  tags = {
#    Name        = "xray-transaction-search-${var.environment}"
#    Environment = var.environment
#    Service     = "ses-mail"
#    Purpose     = "Enable X-Ray Transaction Search for distributed tracing queries"
#  }
#
#  depends_on = [
#    aws_cloudwatch_log_resource_policy.xray_transaction_search
#  ]
#}
