# Archive the Lambda function code with dependencies
# Note: Dependencies must be installed in lambda/package/ directory first
# Run: pip install -r ../requirements.txt -t lambda/package/
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/package"
  output_path = "${path.module}/lambda/email_processor.zip"
  excludes    = ["__pycache__", "*.pyc", ".DS_Store"]
}

# Lambda function for processing emails
resource "aws_lambda_function" "email_processor" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "ses-mail-email-processor-${var.environment}"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "email_processor.lambda_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      GMAIL_TOKEN_PARAMETER = aws_ssm_parameter.gmail_token.name
      EMAIL_BUCKET          = aws_s3_bucket.email_storage.id
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic_execution,
    aws_iam_role_policy.lambda_s3_access,
    aws_iam_role_policy.lambda_ssm_access
  ]
}

# CloudWatch Log Group for Lambda function
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${aws_lambda_function.email_processor.function_name}"
  retention_in_days = 30
}

# Archive the email validator Lambda function code (single file, no dependencies)
data "archive_file" "validator_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/email_validator.py"
  output_path = "${path.module}/lambda/email_validator.zip"
}

# Lambda function for validating emails (RequestResponse invocation)
resource "aws_lambda_function" "email_validator" {
  filename         = data.archive_file.validator_zip.output_path
  function_name    = "ses-mail-email-validator-${var.environment}"
  role             = aws_iam_role.lambda_validator_execution.arn
  handler          = "email_validator.lambda_handler"
  source_code_hash = data.archive_file.validator_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      BOUNCE_SENDER = "mailer-daemon@${var.domain[0]}"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_validator_basic_execution,
    aws_iam_role_policy.lambda_validator_ses_access
  ]
}

# CloudWatch Log Group for validator Lambda function
resource "aws_cloudwatch_log_group" "lambda_validator_logs" {
  name              = "/aws/lambda/${aws_lambda_function.email_validator.function_name}"
  retention_in_days = 30
}

# Archive the router enrichment Lambda function code with dependencies
# Uses the same package directory as email_processor to share dependencies (aws_xray_sdk, boto3)
data "archive_file" "router_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/package"
  output_path = "${path.module}/lambda/router_enrichment.zip"
  excludes    = ["__pycache__", "*.pyc", ".DS_Store", "email_processor.py"]
}

# Lambda function for router enrichment (used by EventBridge Pipes)
resource "aws_lambda_function" "router_enrichment" {
  filename         = data.archive_file.router_zip.output_path
  function_name    = "ses-mail-router-enrichment-${var.environment}"
  role             = aws_iam_role.lambda_router_execution.arn
  handler          = "router_enrichment.lambda_handler"
  source_code_hash = data.archive_file.router_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128

  # Enable X-Ray tracing for distributed tracing
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      DYNAMODB_TABLE_NAME = aws_dynamodb_table.email_routing.name
      ENVIRONMENT         = var.environment
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_router_basic_execution,
    aws_iam_role_policy.lambda_router_dynamodb_access,
    aws_iam_role_policy.lambda_router_s3_access,
    aws_iam_role_policy_attachment.lambda_router_xray_access
  ]
}

# CloudWatch Log Group for router Lambda function
resource "aws_cloudwatch_log_group" "lambda_router_logs" {
  name              = "/aws/lambda/${aws_lambda_function.router_enrichment.function_name}"
  retention_in_days = 30
}

# Archive the Gmail forwarder Lambda function code with dependencies
# Uses the same package directory as email_processor to share dependencies
data "archive_file" "gmail_forwarder_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/package"
  output_path = "${path.module}/lambda/gmail_forwarder.zip"
  excludes    = ["__pycache__", "*.pyc", ".DS_Store", "email_processor.py", "router_enrichment.py"]
}

# Lambda function for Gmail forwarding (triggered by SQS)
resource "aws_lambda_function" "gmail_forwarder" {
  filename         = data.archive_file.gmail_forwarder_zip.output_path
  function_name    = "ses-mail-gmail-forwarder-${var.environment}"
  role             = aws_iam_role.lambda_gmail_forwarder_execution.arn
  handler          = "gmail_forwarder.lambda_handler"
  source_code_hash = data.archive_file.gmail_forwarder_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 3
  memory_size      = 128

  # Enable X-Ray tracing for distributed tracing
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      GMAIL_TOKEN_PARAMETER = aws_ssm_parameter.gmail_token.name
      EMAIL_BUCKET          = aws_s3_bucket.email_storage.id
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_gmail_forwarder_basic_execution,
    aws_iam_role_policy.lambda_gmail_forwarder_s3_access,
    aws_iam_role_policy.lambda_gmail_forwarder_ssm_access,
    aws_iam_role_policy_attachment.lambda_gmail_forwarder_xray_access
  ]
}

# CloudWatch Log Group for Gmail forwarder Lambda function
resource "aws_cloudwatch_log_group" "lambda_gmail_forwarder_logs" {
  name              = "/aws/lambda/${aws_lambda_function.gmail_forwarder.function_name}"
  retention_in_days = 30
}

# Archive the bouncer Lambda function code with dependencies
# Uses the same package directory as other lambdas to share dependencies
data "archive_file" "bouncer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/package"
  output_path = "${path.module}/lambda/bouncer.zip"
  excludes    = ["__pycache__", "*.pyc", ".DS_Store", "email_processor.py", "router_enrichment.py", "gmail_forwarder.py"]
}

# Lambda function for bouncing emails (triggered by SQS)
resource "aws_lambda_function" "bouncer" {
  filename         = data.archive_file.bouncer_zip.output_path
  function_name    = "ses-mail-bouncer-${var.environment}"
  role             = aws_iam_role.lambda_bouncer_execution.arn
  handler          = "bouncer.lambda_handler"
  source_code_hash = data.archive_file.bouncer_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128

  # Enable X-Ray tracing for distributed tracing
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      BOUNCE_SENDER = "mailer-daemon@${var.domain[0]}"
      ENVIRONMENT   = var.environment
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_bouncer_basic_execution,
    aws_iam_role_policy.lambda_bouncer_ses_access,
    aws_iam_role_policy_attachment.lambda_bouncer_xray_access
  ]
}

# CloudWatch Log Group for bouncer Lambda function
resource "aws_cloudwatch_log_group" "lambda_bouncer_logs" {
  name              = "/aws/lambda/${aws_lambda_function.bouncer.function_name}"
  retention_in_days = 30
}

# ===========================
# SQS Event Source Mappings
# ===========================

# Event source mapping for Gmail forwarder lambda
resource "aws_lambda_event_source_mapping" "gmail_forwarder" {
  event_source_arn = aws_sqs_queue.gmail_forwarder.arn
  function_name    = aws_lambda_function.gmail_forwarder.arn

  # Process one message at a time to ensure proper error handling
  batch_size = 1

  # Maximum concurrent lambda invocations
  scaling_config {
    maximum_concurrency = 10
  }

  # Enable function response types for partial batch failures
  function_response_types = ["ReportBatchItemFailures"]

  depends_on = [
    aws_iam_role_policy_attachment.lambda_gmail_forwarder_basic_execution,
    aws_sqs_queue.gmail_forwarder
  ]
}

# Event source mapping for bouncer lambda
resource "aws_lambda_event_source_mapping" "bouncer" {
  event_source_arn = aws_sqs_queue.bouncer.arn
  function_name    = aws_lambda_function.bouncer.arn

  # Process one message at a time to ensure proper error handling
  batch_size = 1

  # Maximum concurrent lambda invocations
  scaling_config {
    maximum_concurrency = 5
  }

  # Enable function response types for partial batch failures
  function_response_types = ["ReportBatchItemFailures"]

  depends_on = [
    aws_iam_role_policy_attachment.lambda_bouncer_basic_execution,
    aws_sqs_queue.bouncer
  ]
}
