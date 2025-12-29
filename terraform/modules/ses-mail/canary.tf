# ===========================
# CloudWatch Synthetics Canary for Email Monitoring
# ===========================

# Local variables for canary resources
locals {
  canary_artifacts_bucket_name = "ses-mail-canary-artifacts-${data.aws_caller_identity.current.account_id}-${var.environment}"
  canary_name                  = "ses-mail-email-monitor-${var.environment}"
}

# S3 bucket for canary artifacts (execution results, errors)
resource "aws_s3_bucket" "canary_artifacts" {
  bucket = local.canary_artifacts_bucket_name

  tags = {
    Name        = local.canary_artifacts_bucket_name
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "CloudWatch Synthetics canary artifacts"
  }
}

# Enable versioning for canary artifacts
resource "aws_s3_bucket_versioning" "canary_artifacts" {
  bucket = aws_s3_bucket.canary_artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Server-side encryption for canary artifacts
resource "aws_s3_bucket_server_side_encryption_configuration" "canary_artifacts" {
  bucket = aws_s3_bucket.canary_artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Block public access for canary artifacts
resource "aws_s3_bucket_public_access_block" "canary_artifacts" {
  bucket = aws_s3_bucket.canary_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle policy for canary artifacts (1-day expiration)
resource "aws_s3_bucket_lifecycle_configuration" "canary_artifacts" {
  bucket = aws_s3_bucket.canary_artifacts.id

  rule {
    id     = "delete-old-artifacts"
    status = "Enabled"

    expiration {
      days = 1
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }
}

# IAM role for CloudWatch Synthetics canary
resource "aws_iam_role" "canary_execution" {
  name = "ses-mail-canary-execution-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name        = "ses-mail-canary-execution-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# IAM policy for canary execution
resource "aws_iam_role_policy" "canary_execution" {
  name = "canary-execution-policy"
  role = aws_iam_role.canary_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetBucketLocation"
        ]
        Resource = [
          aws_s3_bucket.canary_artifacts.arn,
          "${aws_s3_bucket.canary_artifacts.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:CreateLogGroup"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/cwsyn-*"
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/ses-mail/${var.environment}/integration-test-token"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query"
        ]
        Resource = aws_dynamodb_table.email_routing.arn
      },
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "SESMail/${var.environment}"
          }
        }
      },
      {
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Resource = "*"
      }
    ]
  })
}

# Attach CloudWatch Synthetics basic execution policy
resource "aws_iam_role_policy_attachment" "canary_execution_basic" {
  role       = aws_iam_role.canary_execution.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchSyntheticsFullAccess"
}

# Canary script artifact (zip file with Python code)
data "archive_file" "canary_script" {
  type        = "zip"
  source_dir  = "${path.module}/canary"
  output_path = "${path.module}/.terraform/canary_email_monitor.zip"
}

# Trigger resource to force canary replacement when ZIP content changes
resource "terraform_data" "canary_script_trigger" {
  triggers_replace = {
    archive_hash = data.archive_file.canary_script.output_base64sha256
  }
}

# CloudWatch Synthetics canary
resource "aws_synthetics_canary" "email_monitor" {
  name                 = local.canary_name
  artifact_s3_location = "s3://${aws_s3_bucket.canary_artifacts.id}/"
  execution_role_arn   = aws_iam_role.canary_execution.arn
  handler              = "canary_email_monitor.handler"
  zip_file             = data.archive_file.canary_script.output_path
  runtime_version      = "syn-python-selenium-8.0"
  start_canary         = true
  delete_lambda        = true

  lifecycle {
    replace_triggered_by = [terraform_data.canary_script_trigger]
  }

  schedule {
    expression = "rate(1 hour)"
  }

  run_config {
    timeout_in_seconds = 300 # 5 minutes
    memory_in_mb       = 1024
    active_tracing     = false # Python runtime doesn't support X-Ray active tracing

    environment_variables = {
      ENVIRONMENT             = var.environment
      DOMAIN                  = var.domain[0]
      DYNAMODB_TABLE_NAME     = aws_dynamodb_table.email_routing.name
      INTEGRATION_TOKEN_PARAM = "/ses-mail/${var.environment}/integration-test-token"
      CODE_HASH               = data.archive_file.canary_script.output_base64sha256
    }
  }

  success_retention_period = 7  # Days to retain successful run data
  failure_retention_period = 14 # Days to retain failed run data

  tags = {
    Name        = local.canary_name
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "End-to-end email system monitoring"
  }

  depends_on = [
    aws_iam_role_policy.canary_execution,
    aws_iam_role_policy_attachment.canary_execution_basic
  ]
}

# ===========================
# Canary Monitor Queue Infrastructure
# ===========================

# Dead letter queue for canary monitor
resource "aws_sqs_queue" "canary_monitor_dlq" {
  name = "ses-mail-canary-monitor-dlq-${var.environment}"

  message_retention_seconds = 1209600 # 14 days

  tags = {
    Name        = "ses-mail-canary-monitor-dlq-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Dead letter queue for canary monitor handler"
  }
}

# Canary monitor queue (receives messages from EventBridge)
resource "aws_sqs_queue" "canary_monitor" {
  name = "ses-mail-canary-monitor-${var.environment}"

  message_retention_seconds  = 345600 # 4 days
  visibility_timeout_seconds = 30     # 10s timeout * 3 = 30s

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.canary_monitor_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Name        = "ses-mail-canary-monitor-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Handler queue for canary monitoring"
  }
}

# CloudWatch alarm for canary monitor DLQ
resource "aws_cloudwatch_metric_alarm" "canary_monitor_dlq_alarm" {
  alarm_name          = "ses-mail-canary-monitor-dlq-messages-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Average"
  threshold           = 0
  alarm_description   = "Alert when messages appear in canary monitor DLQ"
  alarm_actions       = [var.alarm_sns_topic_arn]
  ok_actions          = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.canary_monitor_dlq.name
  }

  tags = {
    Name        = "ses-mail-canary-monitor-dlq-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# ===========================
# EventBridge Rule for Canary Monitor
# ===========================

# EventBridge rule for routing canary emails
resource "aws_cloudwatch_event_rule" "canary_monitor" {
  name           = "ses-mail-route-to-canary-monitor-${var.environment}"
  description    = "Route canary test emails to canary monitor queue"
  event_bus_name = aws_cloudwatch_event_bus.email_routing.name

  event_pattern = jsonencode({
    source      = ["ses.email.router"]
    detail-type = ["Email Routing Decision"]
    detail = {
      actions = {
        canary-monitor = {
          count = [{ numeric = [">", 0] }]
        }
      }
    }
  })

  tags = {
    Name        = "ses-mail-route-to-canary-monitor-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Route canary-monitor actions to canary monitor queue"
  }
}

# Target for canary monitor rule (sends to SQS queue)
resource "aws_cloudwatch_event_target" "canary_monitor" {
  rule           = aws_cloudwatch_event_rule.canary_monitor.name
  event_bus_name = aws_cloudwatch_event_bus.email_routing.name
  target_id      = "canary-monitor-queue"
  arn            = aws_sqs_queue.canary_monitor.arn
  role_arn       = aws_iam_role.eventbridge_sqs.arn

  dead_letter_config {
    arn = aws_sqs_queue.canary_monitor_dlq.arn
  }

  retry_policy {
    maximum_event_age_in_seconds = 3600 # 1 hour
    maximum_retry_attempts       = 2
  }
}

# SQS queue policy to allow EventBridge
data "aws_iam_policy_document" "canary_monitor_eventbridge_access" {
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
      aws_sqs_queue.canary_monitor.arn
    ]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.canary_monitor.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "canary_monitor_eventbridge" {
  queue_url = aws_sqs_queue.canary_monitor.id
  policy    = data.aws_iam_policy_document.canary_monitor_eventbridge_access.json
}

# ===========================
# Canary Monitor Lambda Handler
# ===========================

# IAM role for canary monitor Lambda
resource "aws_iam_role" "lambda_canary_monitor_execution" {
  name = "ses-mail-lambda-canary-monitor-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name        = "ses-mail-lambda-canary-monitor-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# Attach basic execution policy
resource "aws_iam_role_policy_attachment" "lambda_canary_monitor_basic_execution" {
  role       = aws_iam_role.lambda_canary_monitor_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# DynamoDB access for canary monitor
resource "aws_iam_role_policy" "lambda_canary_monitor_dynamodb_access" {
  name = "dynamodb-access"
  role = aws_iam_role.lambda_canary_monitor_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem"
        ]
        Resource = aws_dynamodb_table.email_routing.arn
      }
    ]
  })
}

# S3 access for canary monitor (delete emails)
resource "aws_iam_role_policy" "lambda_canary_monitor_s3_access" {
  name = "s3-access"
  role = aws_iam_role.lambda_canary_monitor_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:DeleteObject",
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.email_storage.arn}/emails/*"
      }
    ]
  })
}

# CloudWatch metrics access
resource "aws_iam_role_policy" "lambda_canary_monitor_cloudwatch_access" {
  name = "cloudwatch-access"
  role = aws_iam_role.lambda_canary_monitor_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "SESMail/${var.environment}"
          }
        }
      }
    ]
  })
}

# X-Ray tracing access
resource "aws_iam_role_policy_attachment" "lambda_canary_monitor_xray_access" {
  role       = aws_iam_role.lambda_canary_monitor_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# SQS access for canary monitor
resource "aws_iam_role_policy" "lambda_canary_monitor_sqs_access" {
  name = "sqs-access"
  role = aws_iam_role.lambda_canary_monitor_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.canary_monitor.arn
      }
    ]
  })
}

# Archive the canary monitor Lambda function (single file)
data "archive_file" "canary_monitor_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/canary_monitor.py"
  output_path = "${path.module}/lambda/canary_monitor.zip"
}

# Lambda function for canary monitoring
resource "aws_lambda_function" "canary_monitor" {
  filename         = data.archive_file.canary_monitor_zip.output_path
  function_name    = "ses-mail-canary-monitor-${var.environment}"
  role             = aws_iam_role.lambda_canary_monitor_execution.arn
  handler          = "canary_monitor.lambda_handler"
  source_code_hash = data.archive_file.canary_monitor_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 10
  memory_size      = 128

  # Shared dependencies layer
  layers = [aws_lambda_layer_version.shared_deps.arn]

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      DYNAMODB_TABLE_NAME = aws_dynamodb_table.email_routing.name
      EMAIL_BUCKET        = aws_s3_bucket.email_storage.id
      ENVIRONMENT         = var.environment
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_canary_monitor_basic_execution,
    aws_iam_role_policy.lambda_canary_monitor_dynamodb_access,
    aws_iam_role_policy.lambda_canary_monitor_s3_access
  ]

  tags = {
    Name        = "ses-mail-canary-monitor-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# CloudWatch Log Group for canary monitor Lambda
resource "aws_cloudwatch_log_group" "lambda_canary_monitor_logs" {
  name              = "/aws/lambda/${aws_lambda_function.canary_monitor.function_name}"
  retention_in_days = 30

  tags = {
    Name        = "ses-mail-canary-monitor-logs-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# Event source mapping for canary monitor
resource "aws_lambda_event_source_mapping" "canary_monitor" {
  event_source_arn = aws_sqs_queue.canary_monitor.arn
  function_name    = aws_lambda_function.canary_monitor.arn

  batch_size = 1

  scaling_config {
    maximum_concurrency = 2
  }

  function_response_types = ["ReportBatchItemFailures"]

  depends_on = [
    aws_iam_role_policy_attachment.lambda_canary_monitor_basic_execution,
    aws_sqs_queue.canary_monitor
  ]
}

# ===========================
# CloudWatch Alarms for Canary
# ===========================

# Alarm for canary failures (2 failures in 3-hour window)
resource "aws_cloudwatch_metric_alarm" "canary_failures" {
  alarm_name          = "ses-mail-canary-failures-${var.environment}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 3 # 3 hours
  metric_name         = "Failed"
  namespace           = "CloudWatchSynthetics"
  period              = 3600 # 1 hour
  statistic           = "Sum"
  threshold           = 2 # 2 failures
  alarm_description   = "Alert when email canary fails 2 or more times in 3-hour window (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    CanaryName = aws_synthetics_canary.email_monitor.name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "ses-mail-canary-failures-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# Alarm for canary monitor Lambda errors
resource "aws_cloudwatch_metric_alarm" "lambda_canary_monitor_errors" {
  alarm_name          = "ses-mail-lambda-canary-monitor-errors-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when canary monitor Lambda function has errors (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.canary_monitor.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "ses-mail-lambda-canary-monitor-errors-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# ===========================
# DynamoDB Routing Rule for Canary
# ===========================

# Routing rule for canary emails
resource "aws_dynamodb_table_item" "canary_routing_rule" {
  table_name = aws_dynamodb_table.email_routing.name
  hash_key   = aws_dynamodb_table.email_routing.hash_key
  range_key  = aws_dynamodb_table.email_routing.range_key

  item = jsonencode({
    PK          = { S = "ROUTE#canary@${var.domain[0]}" }
    SK          = { S = "RULE#v1" }
    entity_type = { S = "ROUTE" }
    recipient   = { S = "canary@${var.domain[0]}" }
    action      = { S = "canary-monitor" }
    target      = { S = "" }
    enabled     = { BOOL = true }
    created_at  = { S = "2025-12-29T00:00:00Z" }
    updated_at  = { S = "2025-12-29T00:00:00Z" }
    description = { S = "Route canary test emails to monitoring handler" }
  })

  lifecycle {
    ignore_changes = [item] # Prevent drift from runtime updates
  }
}
