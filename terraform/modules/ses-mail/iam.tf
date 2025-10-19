# SSM Parameter for storing Gmail OAuth token
resource "aws_ssm_parameter" "gmail_token" {
  name        = "/ses-mail/${var.environment}/gmail-token"
  description = "Gmail OAuth token for inserting emails via API (${var.environment})"
  type        = "SecureString"
  value       = "PLACEHOLDER - Update this value after deployment"

  lifecycle {
    ignore_changes = [value]
  }
}

# IAM policy document for Lambda assume role
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# IAM role for Lambda function
resource "aws_iam_role" "lambda_execution" {
  name               = "ses-mail-lambda-execution-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# IAM policy document for Lambda S3 access
data "aws_iam_policy_document" "lambda_s3_access" {
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:DeleteObject",
      "s3:ListBucket"
    ]
    resources = [
      aws_s3_bucket.email_storage.arn,
      "${aws_s3_bucket.email_storage.arn}/*"
    ]
  }
}

# IAM policy for Lambda to access S3
resource "aws_iam_role_policy" "lambda_s3_access" {
  name   = "lambda-s3-access-${var.environment}"
  role   = aws_iam_role.lambda_execution.id
  policy = data.aws_iam_policy_document.lambda_s3_access.json
}

# IAM policy document for Lambda SSM access
data "aws_iam_policy_document" "lambda_ssm_access" {
  statement {
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:PutParameter"
    ]
    resources = [aws_ssm_parameter.gmail_token.arn]
  }
}

# IAM policy for Lambda to access SSM Parameter Store
resource "aws_iam_role_policy" "lambda_ssm_access" {
  name   = "lambda-ssm-access-${var.environment}"
  role   = aws_iam_role.lambda_execution.id
  policy = data.aws_iam_policy_document.lambda_ssm_access.json
}

# Attach AWS managed policy for basic Lambda execution (CloudWatch Logs)
resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# IAM role for router enrichment Lambda function (used by EventBridge Pipes)
resource "aws_iam_role" "lambda_router_execution" {
  name               = "ses-mail-lambda-router-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Attach AWS managed policy for basic Lambda execution (CloudWatch Logs) to router
resource "aws_iam_role_policy_attachment" "lambda_router_basic_execution" {
  role       = aws_iam_role.lambda_router_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# IAM policy document for router Lambda DynamoDB access
data "aws_iam_policy_document" "lambda_router_dynamodb_access" {
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query"
    ]
    resources = [
      aws_dynamodb_table.email_routing.arn
    ]
  }
}

# IAM policy for router Lambda to access DynamoDB routing table
resource "aws_iam_role_policy" "lambda_router_dynamodb_access" {
  name   = "lambda-router-dynamodb-access-${var.environment}"
  role   = aws_iam_role.lambda_router_execution.id
  policy = data.aws_iam_policy_document.lambda_router_dynamodb_access.json
}

# IAM policy for router Lambda to access S3 (to read email metadata)
resource "aws_iam_role_policy" "lambda_router_s3_access" {
  name   = "lambda-router-s3-access-${var.environment}"
  role   = aws_iam_role.lambda_router_execution.id
  policy = data.aws_iam_policy_document.lambda_s3_access.json
}

# Attach X-Ray write access for router Lambda (for distributed tracing)
resource "aws_iam_role_policy_attachment" "lambda_router_xray_access" {
  role       = aws_iam_role.lambda_router_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# IAM policy document for router Lambda CloudWatch metrics access
data "aws_iam_policy_document" "lambda_router_cloudwatch_metrics" {
  statement {
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricData"
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["SESMail/${var.environment}"]
    }
  }
}

# IAM policy for router Lambda to publish CloudWatch metrics
resource "aws_iam_role_policy" "lambda_router_cloudwatch_metrics" {
  name   = "lambda-router-cloudwatch-metrics-${var.environment}"
  role   = aws_iam_role.lambda_router_execution.id
  policy = data.aws_iam_policy_document.lambda_router_cloudwatch_metrics.json
}

# IAM role for tag-sync starter Lambda function
resource "aws_iam_role" "lambda_tag_sync_execution" {
  name               = "ses-mail-lambda-tag-sync-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Attach basic Lambda execution role for tag-sync Lambda (CloudWatch Logs)
resource "aws_iam_role_policy_attachment" "lambda_tag_sync_basic_execution" {
  role       = aws_iam_role.lambda_tag_sync_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# IAM policy document for tag-sync Lambda to access AppRegistry and Resource Groups
data "aws_iam_policy_document" "lambda_tag_sync_access" {
  statement {
    effect = "Allow"
    actions = [
      "servicecatalog:GetApplication"
    ]
    resources = ["*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "resource-groups:StartTagSyncTask",
      "resource-groups:GetGroup",
      "resource-groups:CreateGroup",
      "resource-groups:Tag",
      "resource-groups:UpdateAccountSettings",
      "resource-groups:GetAccountSettings",
      "cloudformation:ListStackResources",
      "cloudformation:DescribeStacks",
      "iam:CreateServiceLinkedRole",
      "events:PutRule",
      "events:PutTargets",
      "events:DescribeRule",
      "events:ListTargetsByRule",
      "tag:GetResources",
      "tag:TagResources",
      "tag:UntagResources",
    ]
    resources = ["*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "iam:PassRole"
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/ses-mail-tag-sync-role-${var.environment}"
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["resource-groups.amazonaws.com"]
    }
  }
}

# IAM policy for tag-sync Lambda
resource "aws_iam_role_policy" "lambda_tag_sync_access" {
  name   = "lambda-tag-sync-access-${var.environment}"
  role   = aws_iam_role.lambda_tag_sync_execution.id
  policy = data.aws_iam_policy_document.lambda_tag_sync_access.json
}

# Note: The service-linked role AWSServiceRoleForAWSServiceCatalogAppRegistry
# is automatically created by AWS when you first use AppRegistry.
# We don't manage it with Terraform to avoid conflicts.

# IAM role for tag-sync (separate from service-linked role)
# This role allows Resource Groups to discover and tag resources
data "aws_iam_policy_document" "tag_sync_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["resource-groups.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "tag_sync" {
  name               = "ses-mail-tag-sync-role-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.tag_sync_assume_role.json
  description        = "Role for AppRegistry tag-sync to discover and tag resources"
}

# IAM policy document for tag-sync role permissions
data "aws_iam_policy_document" "tag_sync_permissions" {
  statement {
    effect = "Allow"
    actions = [
      "tag:GetResources",
      "tag:TagResources",
      "tag:UntagResources",
      "resource-groups:*",
      "cloudformation:ListStackResources",
      "cloudformation:DescribeStacks",
      "iam:CreateServiceLinkedRole",
      "events:PutRule",
      "events:PutTargets",
      "events:DescribeRule",
      "events:ListTargetsByRule",
      "servicecatalog:TagResource",
      "ssm:AddTagsToResource",
      "ssm:GetParameters",
    ]
    resources = ["*"]
  }
}

# Attach permissions to tag-sync role
resource "aws_iam_policy" "tag_sync_permissions" {
  name   = "tag-sync-permissions-${var.environment}"
  policy = data.aws_iam_policy_document.tag_sync_permissions.json
}

resource "aws_iam_role_policy_attachment" "tag_sync_custom" {
  role       = aws_iam_role.tag_sync.name
  policy_arn = aws_iam_policy.tag_sync_permissions.arn
}

resource "aws_iam_role_policy_attachment" "tag_sync_tag1" {
  role       = aws_iam_role.tag_sync.name
  policy_arn = "arn:aws:iam::aws:policy/ResourceGroupsTaggingAPITagUntagSupportedResources"
}

resource "aws_iam_role_policy_attachment" "tag_sync_tag2" {
  role       = aws_iam_role.tag_sync.name
  policy_arn = "arn:aws:iam::aws:policy/ResourceGroupsandTagEditorFullAccess"

}

# IAM role for Gmail forwarder Lambda function
resource "aws_iam_role" "lambda_gmail_forwarder_execution" {
  name               = "ses-mail-lambda-gmail-forwarder-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Attach AWS managed policy for basic Lambda execution (CloudWatch Logs) to Gmail forwarder
resource "aws_iam_role_policy_attachment" "lambda_gmail_forwarder_basic_execution" {
  role       = aws_iam_role.lambda_gmail_forwarder_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# IAM policy for Gmail forwarder Lambda to access S3
resource "aws_iam_role_policy" "lambda_gmail_forwarder_s3_access" {
  name   = "lambda-gmail-forwarder-s3-access-${var.environment}"
  role   = aws_iam_role.lambda_gmail_forwarder_execution.id
  policy = data.aws_iam_policy_document.lambda_s3_access.json
}

# IAM policy for Gmail forwarder Lambda to access SSM Parameter Store
resource "aws_iam_role_policy" "lambda_gmail_forwarder_ssm_access" {
  name   = "lambda-gmail-forwarder-ssm-access-${var.environment}"
  role   = aws_iam_role.lambda_gmail_forwarder_execution.id
  policy = data.aws_iam_policy_document.lambda_ssm_access.json
}

# Attach X-Ray write access for Gmail forwarder Lambda (for distributed tracing)
resource "aws_iam_role_policy_attachment" "lambda_gmail_forwarder_xray_access" {
  role       = aws_iam_role.lambda_gmail_forwarder_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# IAM policy document for Gmail forwarder Lambda SQS access
# Note: This will be used once the SQS queue is created in Task 7
data "aws_iam_policy_document" "lambda_gmail_forwarder_sqs_access" {
  statement {
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes"
    ]
    resources = [
      # This resource will be created in Task 7
      "arn:aws:sqs:*:*:ses-gmail-forwarder-${var.environment}"
    ]
  }
}

# IAM policy for Gmail forwarder Lambda to access SQS
# Note: This will be attached once the SQS queue is created in Task 7
resource "aws_iam_role_policy" "lambda_gmail_forwarder_sqs_access" {
  name   = "lambda-gmail-forwarder-sqs-access-${var.environment}"
  role   = aws_iam_role.lambda_gmail_forwarder_execution.id
  policy = data.aws_iam_policy_document.lambda_gmail_forwarder_sqs_access.json
}

# IAM role for bouncer Lambda function
resource "aws_iam_role" "lambda_bouncer_execution" {
  name               = "ses-mail-lambda-bouncer-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Attach AWS managed policy for basic Lambda execution (CloudWatch Logs) to bouncer
resource "aws_iam_role_policy_attachment" "lambda_bouncer_basic_execution" {
  role       = aws_iam_role.lambda_bouncer_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# IAM policy document for bouncer Lambda SES access
data "aws_iam_policy_document" "lambda_bouncer_ses_access" {
  statement {
    effect = "Allow"
    actions = [
      "ses:SendEmail",
      "ses:SendRawEmail"
    ]
    resources = ["*"]
  }
}

# IAM policy for bouncer Lambda to send bounce emails via SES
resource "aws_iam_role_policy" "lambda_bouncer_ses_access" {
  name   = "lambda-bouncer-ses-access-${var.environment}"
  role   = aws_iam_role.lambda_bouncer_execution.id
  policy = data.aws_iam_policy_document.lambda_bouncer_ses_access.json
}

# Attach X-Ray write access for bouncer Lambda (for distributed tracing)
resource "aws_iam_role_policy_attachment" "lambda_bouncer_xray_access" {
  role       = aws_iam_role.lambda_bouncer_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# IAM policy document for bouncer Lambda SQS access
# Note: This will be used once the SQS queue is created in Task 7
data "aws_iam_policy_document" "lambda_bouncer_sqs_access" {
  statement {
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes"
    ]
    resources = [
      # This resource will be created in Task 7
      "arn:aws:sqs:*:*:ses-bouncer-${var.environment}"
    ]
  }
}

# IAM policy for bouncer Lambda to access SQS
# Note: This will be attached once the SQS queue is created in Task 7
resource "aws_iam_role_policy" "lambda_bouncer_sqs_access" {
  name   = "lambda-bouncer-sqs-access-${var.environment}"
  role   = aws_iam_role.lambda_bouncer_execution.id
  policy = data.aws_iam_policy_document.lambda_bouncer_sqs_access.json
}