# ===========================
# Lambda Layers
# ===========================

# Shared layer with common dependencies (boto3, aws_xray_sdk, aws-lambda-powertools)
data "archive_file" "shared_layer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/layer/shared"
  output_path = "${path.module}/lambda/shared_layer.zip"
}

resource "aws_lambda_layer_version" "shared" {
  filename            = data.archive_file.shared_layer_zip.output_path
  layer_name          = "ses-mail-shared-${var.environment}"
  source_code_hash    = data.archive_file.shared_layer_zip.output_base64sha256
  compatible_runtimes = ["python3.12"]
  description         = "Shared dependencies: boto3, aws_xray_sdk, aws-lambda-powertools"
}

# Gmail layer with Google API dependencies (only for gmail_forwarder)
data "archive_file" "gmail_layer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/layer/gmail"
  output_path = "${path.module}/lambda/gmail_layer.zip"
}

resource "aws_lambda_layer_version" "gmail" {
  filename            = data.archive_file.gmail_layer_zip.output_path
  layer_name          = "ses-mail-gmail-${var.environment}"
  source_code_hash    = data.archive_file.gmail_layer_zip.output_base64sha256
  compatible_runtimes = ["python3.12"]
  description         = "Gmail API dependencies: google-auth, google-api-python-client"
}

# ===========================
# Lambda Functions
# ===========================

# Archive the router enrichment Lambda function code (single file, no dependencies)
data "archive_file" "router_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/router_enrichment.py"
  output_path = "${path.module}/lambda/router_enrichment.zip"
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

  # Attach shared layer for dependencies
  layers = [aws_lambda_layer_version.shared.arn]

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

# Archive the Gmail forwarder Lambda function code (single file, no dependencies)
data "archive_file" "gmail_forwarder_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/gmail_forwarder.py"
  output_path = "${path.module}/lambda/gmail_forwarder.zip"
}

# Lambda function for Gmail forwarding (triggered by SQS)
resource "aws_lambda_function" "gmail_forwarder" {
  filename         = data.archive_file.gmail_forwarder_zip.output_path
  function_name    = "ses-mail-gmail-forwarder-${var.environment}"
  role             = aws_iam_role.lambda_gmail_forwarder_execution.arn
  handler          = "gmail_forwarder.lambda_handler"
  source_code_hash = data.archive_file.gmail_forwarder_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 10
  memory_size      = 128

  # Attach shared layer and Gmail layer for dependencies
  layers = [
    aws_lambda_layer_version.shared.arn,
    aws_lambda_layer_version.gmail.arn
  ]

  # Enable X-Ray tracing for distributed tracing
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      GMAIL_REFRESH_TOKEN_PARAMETER      = aws_ssm_parameter.gmail_oauth_refresh_token.name
      GMAIL_CLIENT_CREDENTIALS_PARAMETER = aws_ssm_parameter.gmail_oauth_client_credentials.name
      EMAIL_BUCKET                       = aws_s3_bucket.email_storage.id
      RETRY_QUEUE_URL                    = aws_sqs_queue.gmail_forwarder_retry.url
      DYNAMODB_TABLE_NAME                = aws_dynamodb_table.email_routing.name
      CANARY_GMAIL_LABEL                 = var.canary_gmail_label
      ENVIRONMENT                        = var.environment
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_gmail_forwarder_basic_execution,
    aws_iam_role_policy.lambda_gmail_forwarder_s3_access,
    aws_iam_role_policy.lambda_gmail_forwarder_ssm_access,
    aws_iam_role_policy_attachment.lambda_gmail_forwarder_xray_access,
    aws_iam_role_policy.lambda_gmail_forwarder_dynamodb
  ]
}

# CloudWatch Log Group for Gmail forwarder Lambda function
resource "aws_cloudwatch_log_group" "lambda_gmail_forwarder_logs" {
  name              = "/aws/lambda/${aws_lambda_function.gmail_forwarder.function_name}"
  retention_in_days = 30
}

# Archive the bouncer Lambda function code (single file, no dependencies)
data "archive_file" "bouncer_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/bouncer.py"
  output_path = "${path.module}/lambda/bouncer.zip"
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

  # Attach shared layer for dependencies
  layers = [aws_lambda_layer_version.shared.arn]

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

# Archive the SMTP credential manager Lambda function code (single file, no dependencies)
data "archive_file" "smtp_credential_manager_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/smtp_credential_manager.py"
  output_path = "${path.module}/lambda/smtp_credential_manager.zip"
}

# Lambda function for SMTP credential management (triggered by DynamoDB Streams)
resource "aws_lambda_function" "smtp_credential_manager" {
  filename         = data.archive_file.smtp_credential_manager_zip.output_path
  function_name    = "ses-mail-smtp-credential-manager-${var.environment}"
  role             = aws_iam_role.lambda_credential_manager_execution.arn
  handler          = "smtp_credential_manager.lambda_handler"
  source_code_hash = data.archive_file.smtp_credential_manager_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128

  # Attach shared layer for dependencies
  layers = [aws_lambda_layer_version.shared.arn]

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
    aws_iam_role_policy_attachment.lambda_credential_manager_basic_execution,
    aws_iam_role_policy.lambda_credential_manager_dynamodb_streams,
    aws_iam_role_policy.lambda_credential_manager_iam_access,
    aws_iam_role_policy_attachment.lambda_credential_manager_xray_access
  ]
}

# CloudWatch Log Group for SMTP credential manager Lambda function
resource "aws_cloudwatch_log_group" "lambda_smtp_credential_manager_logs" {
  name              = "/aws/lambda/${aws_lambda_function.smtp_credential_manager.function_name}"
  retention_in_days = 30
}

# ===========================
# SNS Subscription for Router Enrichment Lambda
# ===========================

# SNS topic subscription for router enrichment lambda
# This lambda subscribes directly to SNS to preserve X-Ray tracing context
resource "aws_sns_topic_subscription" "router_enrichment" {
  topic_arn = aws_sns_topic.email_processing.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.router_enrichment.arn

  depends_on = [
    aws_lambda_function.router_enrichment,
    aws_lambda_permission.router_enrichment_sns
  ]
}

# Lambda permission to allow SNS to invoke router enrichment function
resource "aws_lambda_permission" "router_enrichment_sns" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.router_enrichment.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.email_processing.arn
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

# Event source mapping for SMTP credential manager lambda (DynamoDB Streams)
resource "aws_lambda_event_source_mapping" "smtp_credential_manager" {
  event_source_arn  = aws_dynamodb_table.email_routing.stream_arn
  function_name     = aws_lambda_function.smtp_credential_manager.arn
  starting_position = "LATEST"

  # Process records in small batches for better error isolation
  batch_size = 10

  # Enable function response types for partial batch failures
  function_response_types = ["ReportBatchItemFailures"]

  # Filter to only process SMTP_USER records
  filter_criteria {
    filter {
      # Process INSERT, MODIFY, and REMOVE events for SMTP_USER records
      # Note: REMOVE events use OldImage instead of NewImage
      pattern = jsonencode({
        eventName : ["INSERT", "MODIFY", "REMOVE"],
        dynamodb : {
          NewImage : {
            PK : {
              S : ["SMTP_USER"]
            }
          }
        }
      })
    }
    filter {
      # Also capture REMOVE events (which only have OldImage)
      pattern = jsonencode({
        eventName : ["REMOVE"],
        dynamodb : {
          OldImage : {
            PK : {
              S : ["SMTP_USER"]
            }
          }
        }
      })
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_credential_manager_basic_execution,
    aws_iam_role_policy.lambda_credential_manager_dynamodb_streams
  ]
}

# ===========================
# Outbound Metrics Publisher Lambda
# ===========================

# Archive the outbound metrics publisher Lambda function code (single file, no dependencies)
data "archive_file" "outbound_metrics_publisher_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/outbound_metrics_publisher.py"
  output_path = "${path.module}/lambda/outbound_metrics_publisher.zip"
}

# Lambda function for publishing outbound email metrics from SES events
resource "aws_lambda_function" "outbound_metrics_publisher" {
  filename         = data.archive_file.outbound_metrics_publisher_zip.output_path
  function_name    = "ses-mail-outbound-metrics-${var.environment}"
  role             = aws_iam_role.lambda_outbound_metrics.arn
  handler          = "outbound_metrics_publisher.lambda_handler"
  source_code_hash = data.archive_file.outbound_metrics_publisher_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  description      = "Processes SES outbound email events and publishes CloudWatch metrics"

  # Attach shared layer for dependencies (boto3, aws_xray_sdk, aws-lambda-powertools)
  layers = [aws_lambda_layer_version.shared.arn]

  # Enable X-Ray tracing for distributed tracing
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      ENVIRONMENT = var.environment
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_outbound_metrics_basic_execution,
    aws_iam_role_policy.lambda_outbound_metrics_cloudwatch,
    aws_iam_role_policy_attachment.lambda_outbound_metrics_xray_access
  ]
}

# CloudWatch Log Group for outbound metrics publisher Lambda function
resource "aws_cloudwatch_log_group" "lambda_outbound_metrics_logs" {
  name              = "/aws/lambda/${aws_lambda_function.outbound_metrics_publisher.function_name}"
  retention_in_days = 30
}

# Lambda permission for SNS to invoke outbound metrics publisher (send events)
resource "aws_lambda_permission" "outbound_metrics_sns_send" {
  statement_id  = "AllowExecutionFromSNSSend"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.outbound_metrics_publisher.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.outbound_send.arn
}

# Lambda permission for SNS to invoke outbound metrics publisher (delivery events)
resource "aws_lambda_permission" "outbound_metrics_sns_delivery" {
  statement_id  = "AllowExecutionFromSNSDelivery"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.outbound_metrics_publisher.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.outbound_delivery.arn
}

# Lambda permission for SNS to invoke outbound metrics publisher (bounce events)
resource "aws_lambda_permission" "outbound_metrics_sns_bounce" {
  statement_id  = "AllowExecutionFromSNSBounce"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.outbound_metrics_publisher.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.outbound_bounce.arn
}

# Lambda permission for SNS to invoke outbound metrics publisher (complaint events)
resource "aws_lambda_permission" "outbound_metrics_sns_complaint" {
  statement_id  = "AllowExecutionFromSNSComplaint"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.outbound_metrics_publisher.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.outbound_complaint.arn
}

# ===========================
# Canary Sender Lambda
# ===========================

# Archive the canary sender Lambda function code (single file, no dependencies)
data "archive_file" "canary_sender_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/canary_sender.py"
  output_path = "${path.module}/lambda/canary_sender.zip"
}

# Lambda function for sending canary test emails
resource "aws_lambda_function" "canary_sender" {
  filename         = data.archive_file.canary_sender_zip.output_path
  function_name    = "ses-mail-canary-sender-${var.environment}"
  role             = aws_iam_role.lambda_canary_sender.arn
  handler          = "canary_sender.lambda_handler"
  source_code_hash = data.archive_file.canary_sender_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128
  description      = "Sends canary test emails and validates DNS records"

  # Attach shared layer for dependencies (boto3, aws_xray_sdk, aws-lambda-powertools, dnspython)
  layers = [aws_lambda_layer_version.shared.arn]

  # Enable X-Ray tracing for distributed tracing
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      ENVIRONMENT         = var.environment
      CANARY_EMAIL        = "ses-canary-${var.environment}@${var.domain[0]}"
      DOMAIN              = var.domain[0]
      DYNAMODB_TABLE_NAME = aws_dynamodb_table.email_routing.name
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_canary_sender_basic_execution,
    aws_iam_role_policy.lambda_canary_sender_cloudwatch_metrics,
    aws_iam_role_policy_attachment.lambda_canary_sender_xray_access,
    aws_iam_role_policy.lambda_canary_sender_ses,
    aws_iam_role_policy.lambda_canary_sender_dynamodb
  ]
}

# CloudWatch Log Group for canary sender Lambda function
resource "aws_cloudwatch_log_group" "lambda_canary_sender_logs" {
  name              = "/aws/lambda/${aws_lambda_function.canary_sender.function_name}"
  retention_in_days = 30
}
