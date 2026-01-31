# ===========================
# JMAP Integration
# ===========================
# All resources in this file are conditional on var.jmap_deployment != null
# This integrates with jmap-service-core for email delivery via JMAP protocol

# ===========================
# Data Sources for JMAP Service Discovery
# ===========================

# Read JMAP API Gateway URL from SSM (published by jmap-service-core)
data "aws_ssm_parameter" "jmap_api_url" {
  count = var.jmap_deployment != null ? 1 : 0
  name  = "/jmap-service-core/${var.jmap_deployment}/api-gateway-invoke-url"
}

# Read JMAP DynamoDB table name from SSM (for plugin registration)
data "aws_ssm_parameter" "jmap_table_name" {
  count = var.jmap_deployment != null ? 1 : 0
  name  = "/jmap-service-core/${var.jmap_deployment}/dynamodb-table-name"
}

# Read JMAP DynamoDB table ARN from SSM (for IAM policy)
data "aws_ssm_parameter" "jmap_table_arn" {
  count = var.jmap_deployment != null ? 1 : 0
  name  = "/jmap-service-core/${var.jmap_deployment}/dynamodb-table-arn"
}

# Local to extract API Gateway ID from invoke URL and construct ARN
# Invoke URL format: https://{api-id}.execute-api.{region}.amazonaws.com/{stage}
locals {
  # Extract API Gateway ID from URL like: https://abc123.execute-api.ap-southeast-2.amazonaws.com/prod
  jmap_api_id = var.jmap_deployment != null ? regex("https://([^.]+)\\.execute-api", data.aws_ssm_parameter.jmap_api_url[0].value)[0] : ""
  # Construct execute-api ARN for IAM policy
  jmap_api_arn = var.jmap_deployment != null ? "arn:aws:execute-api:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${local.jmap_api_id}" : ""
}

# ===========================
# Plugin Registration
# ===========================

# Timestamp for plugin registration
resource "time_static" "jmap_plugin_registered" {
  count = var.jmap_deployment != null ? 1 : 0
}

# Register ses-mail-ingest as a plugin in JMAP's DynamoDB table
# This grants the Lambda role permission to call JMAP APIs
resource "aws_dynamodb_table_item" "jmap_plugin_registration" {
  count      = var.jmap_deployment != null ? 1 : 0
  table_name = data.aws_ssm_parameter.jmap_table_name[0].value
  hash_key   = "pk"
  range_key  = "sk"

  item = jsonencode({
    pk               = { S = "PLUGIN#" }
    sk               = { S = "PLUGIN#ses-mail-ingest" }
    pluginId         = { S = "ses-mail-ingest" }
    clientPrincipals = { L = [{ S = aws_iam_role.lambda_jmap_deliverer_execution[0].arn }] }
    capabilities     = { M = {} }
    methods          = { M = {} }
    registeredAt     = { S = time_static.jmap_plugin_registered[0].rfc3339 }
    version          = { S = "1.0.0" }
  })

  lifecycle {
    ignore_changes = [item]
  }
}

# ===========================
# SQS Queue Infrastructure
# ===========================

# Dead letter queue for JMAP deliverer
resource "aws_sqs_queue" "jmap_deliverer_dlq" {
  count = var.jmap_deployment != null ? 1 : 0
  name  = "ses-mail-jmap-deliverer-dlq-${var.environment}"

  # Retain messages for 14 days to allow time for investigation
  message_retention_seconds = 1209600

  tags = {
    Name        = "ses-mail-jmap-deliverer-dlq-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Dead letter queue for JMAP deliverer handler"
  }
}

# JMAP deliverer queue (receives messages from EventBridge Event Bus)
resource "aws_sqs_queue" "jmap_deliverer" {
  count = var.jmap_deployment != null ? 1 : 0
  name  = "ses-mail-jmap-deliverer-${var.environment}"

  # Message retention: 4 days (default)
  message_retention_seconds = 345600

  # Visibility timeout: 60 seconds (JMAP API calls may take longer than Gmail)
  visibility_timeout_seconds = 60

  # Dead letter queue configuration
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jmap_deliverer_dlq[0].arn
    maxReceiveCount     = 3
  })

  tags = {
    Name        = "ses-mail-jmap-deliverer-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Handler queue for JMAP delivery actions"
  }
}

# ===========================
# EventBridge Rule and Target
# ===========================

# EventBridge rule for routing to JMAP deliverer
resource "aws_cloudwatch_event_rule" "jmap_deliverer" {
  count          = var.jmap_deployment != null ? 1 : 0
  name           = "ses-mail-route-to-jmap-${var.environment}"
  description    = "Route emails with deliver-to-jmap action to JMAP deliverer queue"
  event_bus_name = aws_cloudwatch_event_bus.email_routing.name

  # Match events from router enrichment with deliver-to-jmap action
  event_pattern = jsonencode({
    source      = ["ses.email.router"]
    detail-type = ["Email Routing Decision"]
    detail = {
      actions = {
        deliver-to-jmap = {
          count = [{ numeric = [">", 0] }]
        }
      }
    }
  })

  tags = {
    Name        = "ses-mail-route-to-jmap-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Route deliver-to-jmap actions to JMAP deliverer queue"
  }
}

# Target for JMAP deliverer rule (sends to SQS queue)
resource "aws_cloudwatch_event_target" "jmap_deliverer" {
  count          = var.jmap_deployment != null ? 1 : 0
  rule           = aws_cloudwatch_event_rule.jmap_deliverer[0].name
  event_bus_name = aws_cloudwatch_event_bus.email_routing.name
  target_id      = "jmap-deliverer-queue"
  arn            = aws_sqs_queue.jmap_deliverer[0].arn
  role_arn       = aws_iam_role.eventbridge_sqs.arn

  # Dead letter queue configuration for failed event deliveries
  dead_letter_config {
    arn = aws_sqs_queue.jmap_deliverer_dlq[0].arn
  }

  # Retry policy for transient failures
  retry_policy {
    maximum_event_age_in_seconds = 3600 # 1 hour
    maximum_retry_attempts       = 2
  }
}

# IAM policy document for JMAP deliverer queue to allow EventBridge
data "aws_iam_policy_document" "jmap_deliverer_eventbridge_access" {
  count = var.jmap_deployment != null ? 1 : 0

  statement {
    sid    = "AllowEventBridgeToSendMessage"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions = [
      "SQS:SendMessage"
    ]

    resources = [
      aws_sqs_queue.jmap_deliverer[0].arn
    ]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.jmap_deliverer[0].arn]
    }
  }
}

# SQS queue policy to allow EventBridge to send messages to JMAP deliverer queue
resource "aws_sqs_queue_policy" "jmap_deliverer_eventbridge" {
  count     = var.jmap_deployment != null ? 1 : 0
  queue_url = aws_sqs_queue.jmap_deliverer[0].id
  policy    = data.aws_iam_policy_document.jmap_deliverer_eventbridge_access[0].json
}

# ===========================
# Lambda Function
# ===========================

# Archive the JMAP deliverer Lambda function code
data "archive_file" "jmap_deliverer_zip" {
  count       = var.jmap_deployment != null ? 1 : 0
  type        = "zip"
  source_file = "${path.module}/lambda/jmap_deliverer.py"
  output_path = "${path.module}/lambda/jmap_deliverer.zip"
}

# Lambda function for JMAP delivery (triggered by SQS)
resource "aws_lambda_function" "jmap_deliverer" {
  count            = var.jmap_deployment != null ? 1 : 0
  filename         = data.archive_file.jmap_deliverer_zip[0].output_path
  function_name    = "ses-mail-jmap-deliverer-${var.environment}"
  role             = aws_iam_role.lambda_jmap_deliverer_execution[0].arn
  handler          = "jmap_deliverer.lambda_handler"
  source_code_hash = data.archive_file.jmap_deliverer_zip[0].output_base64sha256
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128

  # Attach shared layer for dependencies (boto3, aws_xray_sdk, aws-lambda-powertools)
  layers = [aws_lambda_layer_version.shared.arn]

  # Enable X-Ray tracing for distributed tracing
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      JMAP_API_URL_PARAMETER = "/jmap-service-core/${var.jmap_deployment}/api-gateway-invoke-url"
      EMAIL_BUCKET           = aws_s3_bucket.email_storage.id
      ENVIRONMENT            = var.environment
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_jmap_deliverer_basic_execution[0],
    aws_iam_role_policy.lambda_jmap_deliverer_s3_access[0],
    aws_iam_role_policy.lambda_jmap_deliverer_ssm_access[0],
    aws_iam_role_policy.lambda_jmap_deliverer_api_gateway_access[0],
    aws_iam_role_policy_attachment.lambda_jmap_deliverer_xray_access[0]
  ]
}

# CloudWatch Log Group for JMAP deliverer Lambda function
resource "aws_cloudwatch_log_group" "lambda_jmap_deliverer_logs" {
  count             = var.jmap_deployment != null ? 1 : 0
  name              = "/aws/lambda/ses-mail-jmap-deliverer-${var.environment}"
  retention_in_days = 30
}

# Event source mapping for JMAP deliverer lambda
resource "aws_lambda_event_source_mapping" "jmap_deliverer" {
  count            = var.jmap_deployment != null ? 1 : 0
  event_source_arn = aws_sqs_queue.jmap_deliverer[0].arn
  function_name    = aws_lambda_function.jmap_deliverer[0].arn

  # Process one message at a time to ensure proper error handling
  batch_size = 1

  # Maximum concurrent lambda invocations
  scaling_config {
    maximum_concurrency = 10
  }

  # Enable function response types for partial batch failures
  function_response_types = ["ReportBatchItemFailures"]

  depends_on = [
    aws_iam_role_policy_attachment.lambda_jmap_deliverer_basic_execution[0],
    aws_sqs_queue.jmap_deliverer[0]
  ]
}

# ===========================
# IAM Role and Policies
# ===========================

# IAM role for JMAP deliverer Lambda function
resource "aws_iam_role" "lambda_jmap_deliverer_execution" {
  count              = var.jmap_deployment != null ? 1 : 0
  name               = "ses-mail-lambda-jmap-deliverer-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name        = "ses-mail-lambda-jmap-deliverer-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# Attach AWS managed policy for basic Lambda execution (CloudWatch Logs)
resource "aws_iam_role_policy_attachment" "lambda_jmap_deliverer_basic_execution" {
  count      = var.jmap_deployment != null ? 1 : 0
  role       = aws_iam_role.lambda_jmap_deliverer_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Attach X-Ray write access for distributed tracing
resource "aws_iam_role_policy_attachment" "lambda_jmap_deliverer_xray_access" {
  count      = var.jmap_deployment != null ? 1 : 0
  role       = aws_iam_role.lambda_jmap_deliverer_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# IAM policy document for JMAP deliverer Lambda S3 access
data "aws_iam_policy_document" "lambda_jmap_deliverer_s3_access" {
  count = var.jmap_deployment != null ? 1 : 0

  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject"
    ]
    resources = [
      "${aws_s3_bucket.email_storage.arn}/*"
    ]
  }
}

# IAM policy for JMAP deliverer Lambda to access S3
resource "aws_iam_role_policy" "lambda_jmap_deliverer_s3_access" {
  count  = var.jmap_deployment != null ? 1 : 0
  name   = "lambda-jmap-deliverer-s3-access-${var.environment}"
  role   = aws_iam_role.lambda_jmap_deliverer_execution[0].id
  policy = data.aws_iam_policy_document.lambda_jmap_deliverer_s3_access[0].json
}

# IAM policy document for JMAP deliverer Lambda SSM access
data "aws_iam_policy_document" "lambda_jmap_deliverer_ssm_access" {
  count = var.jmap_deployment != null ? 1 : 0

  statement {
    effect = "Allow"
    actions = [
      "ssm:GetParameter"
    ]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/jmap-service-core/${var.jmap_deployment}/*"
    ]
  }
}

# IAM policy for JMAP deliverer Lambda to access SSM Parameter Store
resource "aws_iam_role_policy" "lambda_jmap_deliverer_ssm_access" {
  count  = var.jmap_deployment != null ? 1 : 0
  name   = "lambda-jmap-deliverer-ssm-access-${var.environment}"
  role   = aws_iam_role.lambda_jmap_deliverer_execution[0].id
  policy = data.aws_iam_policy_document.lambda_jmap_deliverer_ssm_access[0].json
}

# IAM policy document for JMAP deliverer Lambda API Gateway access (SigV4 signing)
data "aws_iam_policy_document" "lambda_jmap_deliverer_api_gateway_access" {
  count = var.jmap_deployment != null ? 1 : 0

  statement {
    effect = "Allow"
    actions = [
      "execute-api:Invoke"
    ]
    resources = [
      "${local.jmap_api_arn}/*/POST/jmap-iam/*"
    ]
  }
}

# IAM policy for JMAP deliverer Lambda to invoke API Gateway
resource "aws_iam_role_policy" "lambda_jmap_deliverer_api_gateway_access" {
  count  = var.jmap_deployment != null ? 1 : 0
  name   = "lambda-jmap-deliverer-api-gateway-access-${var.environment}"
  role   = aws_iam_role.lambda_jmap_deliverer_execution[0].id
  policy = data.aws_iam_policy_document.lambda_jmap_deliverer_api_gateway_access[0].json
}

# IAM policy document for JMAP deliverer Lambda SQS access
data "aws_iam_policy_document" "lambda_jmap_deliverer_sqs_access" {
  count = var.jmap_deployment != null ? 1 : 0

  statement {
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes"
    ]
    resources = [
      aws_sqs_queue.jmap_deliverer[0].arn
    ]
  }
}

# IAM policy for JMAP deliverer Lambda to access SQS
resource "aws_iam_role_policy" "lambda_jmap_deliverer_sqs_access" {
  count  = var.jmap_deployment != null ? 1 : 0
  name   = "lambda-jmap-deliverer-sqs-access-${var.environment}"
  role   = aws_iam_role.lambda_jmap_deliverer_execution[0].id
  policy = data.aws_iam_policy_document.lambda_jmap_deliverer_sqs_access[0].json
}

# IAM policy document for JMAP deliverer Lambda CloudWatch metrics access
data "aws_iam_policy_document" "lambda_jmap_deliverer_cloudwatch_metrics" {
  count = var.jmap_deployment != null ? 1 : 0

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

# IAM policy for JMAP deliverer Lambda to publish CloudWatch metrics
resource "aws_iam_role_policy" "lambda_jmap_deliverer_cloudwatch_metrics" {
  count  = var.jmap_deployment != null ? 1 : 0
  name   = "lambda-jmap-deliverer-cloudwatch-metrics-${var.environment}"
  role   = aws_iam_role.lambda_jmap_deliverer_execution[0].id
  policy = data.aws_iam_policy_document.lambda_jmap_deliverer_cloudwatch_metrics[0].json
}

# ===========================
# CloudWatch Alarms
# ===========================

# CloudWatch alarm for JMAP deliverer DLQ messages
resource "aws_cloudwatch_metric_alarm" "jmap_deliverer_dlq_alarm" {
  count               = var.jmap_deployment != null ? 1 : 0
  alarm_name          = "ses-mail-jmap-deliverer-dlq-messages-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Average"
  threshold           = 0
  alarm_description   = "Alert when messages appear in JMAP deliverer DLQ"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.jmap_deliverer_dlq[0].name
  }

  tags = {
    Name        = "ses-mail-jmap-deliverer-dlq-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# CloudWatch alarm for JMAP deliverer queue age (messages waiting too long)
resource "aws_cloudwatch_metric_alarm" "jmap_deliverer_queue_age_alarm" {
  count               = var.jmap_deployment != null ? 1 : 0
  alarm_name          = "ses-mail-jmap-deliverer-queue-age-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 300 # 5 minutes
  alarm_description   = "Alert when messages in JMAP deliverer queue are older than 5 minutes"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.jmap_deliverer[0].name
  }

  tags = {
    Name        = "ses-mail-jmap-deliverer-queue-age-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# CloudWatch Alarm for JMAP deliverer Lambda errors
resource "aws_cloudwatch_metric_alarm" "lambda_jmap_deliverer_errors" {
  count               = var.jmap_deployment != null ? 1 : 0
  alarm_name          = "ses-mail-lambda-jmap-deliverer-errors-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when JMAP deliverer Lambda function has errors (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.jmap_deliverer[0].function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
}

# CloudWatch Alarm for JMAP deliverer failures (custom metric)
resource "aws_cloudwatch_metric_alarm" "jmap_deliverer_failures" {
  count               = var.jmap_deployment != null ? 1 : 0
  alarm_name          = "ses-mail-jmap-deliverer-failures-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "JmapDeliverFailure"
  namespace           = "SESMail/${var.environment}"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when JMAP deliverer fails to deliver messages (${var.environment}). Catches failures in JMAP API calls or email upload issues."
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
}

# ===========================
# Update EventBridge IAM Policy for SQS
# ===========================

# Update the existing EventBridge SQS policy to include JMAP queue
# This is done via a separate policy to keep it conditional
data "aws_iam_policy_document" "eventbridge_jmap_sqs_access" {
  count = var.jmap_deployment != null ? 1 : 0

  statement {
    effect = "Allow"
    actions = [
      "sqs:SendMessage"
    ]
    resources = [
      aws_sqs_queue.jmap_deliverer[0].arn
    ]
  }
}

# IAM policy for EventBridge to access JMAP SQS queue
resource "aws_iam_role_policy" "eventbridge_jmap_sqs_access" {
  count  = var.jmap_deployment != null ? 1 : 0
  name   = "eventbridge-jmap-sqs-access-${var.environment}"
  role   = aws_iam_role.eventbridge_sqs.id
  policy = data.aws_iam_policy_document.eventbridge_jmap_sqs_access[0].json
}
