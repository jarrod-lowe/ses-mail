# AWS Service Catalog AppRegistry application for myApplications integration
# This creates an application-level view in the AWS Console myApplications section
#
# The AppRegistry application automatically discovers resources via tag-sync
# All resources tagged with Application=ses-mail-{environment} will be included
# This integrates with the Resource Group to provide application-centric management

resource "aws_servicecatalogappregistry_application" "ses_mail" {
  name        = "ses-mail-${var.environment}"
  description = "SES Mail - Email receiving and processing system for ${var.environment} environment. Processes emails through SES, stores in S3, routes via EventBridge, and forwards to Gmail or bounces based on DynamoDB routing rules."

  tags = {
    Name    = "ses-mail-${var.environment}"
    Purpose = "AppRegistry application for myApplications integration"
  }
}

# Archive the tag-sync starter Lambda function code (single file, no dependencies)
data "archive_file" "tag_sync_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/tag_sync_starter.py"
  output_path = "${path.module}/lambda/tag_sync_starter.zip"
}

# Lambda function for starting tag-sync task
resource "aws_lambda_function" "tag_sync_starter" {
  filename         = data.archive_file.tag_sync_zip.output_path
  function_name    = "ses-mail-tag-sync-starter-${var.environment}"
  role             = aws_iam_role.lambda_tag_sync_execution.arn
  handler          = "tag_sync_starter.lambda_handler"
  source_code_hash = data.archive_file.tag_sync_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128

  description = "Starts tag-sync task for AppRegistry application to auto-discover resources"

  environment {
    variables = {
      TAG_SYNC_ROLE_ARN = aws_iam_role.tag_sync.arn
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_tag_sync_basic_execution,
    aws_iam_role_policy.lambda_tag_sync_access,
    aws_iam_role.tag_sync
  ]
}

# CloudWatch Log Group for tag-sync Lambda function
resource "aws_cloudwatch_log_group" "lambda_tag_sync_logs" {
  name              = "/aws/lambda/${aws_lambda_function.tag_sync_starter.function_name}"
  retention_in_days = 7 # Short retention since this is a one-time setup function
}

# Invoke the Lambda to configure tag-sync after AppRegistry application is created
resource "aws_lambda_invocation" "configure_tag_sync" {
  function_name = aws_lambda_function.tag_sync_starter.function_name

  input = jsonencode({
    application_arn = "arn:aws:resource-groups:${data.aws_region.current.id}:${data.aws_caller_identity.current.account_id}:group/ses-mail-${var.environment}/${aws_servicecatalogappregistry_application.ses_mail.id}"
    tag_key         = "Application"
    tag_value       = "ses-mail-${var.environment}"
  })

  depends_on = [
    aws_servicecatalogappregistry_application.ses_mail,
    aws_lambda_function.tag_sync_starter,
    aws_iam_role_policy_attachment.tag_sync_custom,
    aws_iam_role_policy_attachment.tag_sync_tag1,
    aws_iam_role_policy_attachment.tag_sync_tag2,
  ]

  # Re-run if the application ARN or IAM policy changes
  triggers = {
    application_arn      = aws_servicecatalogappregistry_application.ses_mail.arn
    iam_policy_hash      = data.aws_iam_policy_document.lambda_tag_sync_access.json
    source_code_hash     = data.archive_file.tag_sync_zip.output_base64sha256
    lambda_iam_policy_id = aws_iam_role_policy.lambda_tag_sync_access.id
    tag_sync_policy_hash = data.aws_iam_policy_document.tag_sync_permissions.json
  }
}

# Note: The tag-sync task automatically associates resources with this AppRegistry
# application by monitoring for resources tagged with Application=ses-mail-{environment}.
# Once configured, any new or existing resources with this tag will automatically appear
# in myApplications.
