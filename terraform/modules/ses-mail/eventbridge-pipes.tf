# ===========================
# EventBridge Pipes for Email Router Enrichment
# ===========================
#
# EventBridge Pipes connects the SQS input queue to the EventBridge Event Bus,
# enriching messages with the router lambda function that adds routing decisions.
#
# Flow: SQS input queue → Router Lambda enrichment → EventBridge Event Bus

# ===========================
# IAM Role for EventBridge Pipes
# ===========================

# IAM policy document for EventBridge Pipes assume role
data "aws_iam_policy_document" "eventbridge_pipes_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["pipes.amazonaws.com"]
    }
  }
}

# IAM role for EventBridge Pipes execution
resource "aws_iam_role" "eventbridge_pipes_execution" {
  name               = "ses-mail-pipes-execution-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.eventbridge_pipes_assume_role.json

  tags = {
    Name        = "ses-mail-pipes-execution-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Execution role for EventBridge Pipes to enrich SES messages"
  }
}

# ===========================
# IAM Policies for EventBridge Pipes
# ===========================

# IAM policy document for Pipes to read from SQS input queue
data "aws_iam_policy_document" "eventbridge_pipes_sqs_source" {
  statement {
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes"
    ]
    resources = [
      aws_sqs_queue.input_queue.arn
    ]
  }
}

# IAM policy for Pipes to access SQS source queue
resource "aws_iam_role_policy" "eventbridge_pipes_sqs_source" {
  name   = "pipes-sqs-source-${var.environment}"
  role   = aws_iam_role.eventbridge_pipes_execution.id
  policy = data.aws_iam_policy_document.eventbridge_pipes_sqs_source.json
}

# IAM policy document for Pipes to invoke router lambda for enrichment
data "aws_iam_policy_document" "eventbridge_pipes_lambda_enrichment" {
  statement {
    effect = "Allow"
    actions = [
      "lambda:InvokeFunction"
    ]
    resources = [
      aws_lambda_function.router_enrichment.arn
    ]
  }
}

# IAM policy for Pipes to invoke enrichment lambda
resource "aws_iam_role_policy" "eventbridge_pipes_lambda_enrichment" {
  name   = "pipes-lambda-enrichment-${var.environment}"
  role   = aws_iam_role.eventbridge_pipes_execution.id
  policy = data.aws_iam_policy_document.eventbridge_pipes_lambda_enrichment.json
}

# IAM policy document for Pipes to send events to EventBridge Event Bus
data "aws_iam_policy_document" "eventbridge_pipes_event_bus_target" {
  statement {
    effect = "Allow"
    actions = [
      "events:PutEvents"
    ]
    resources = [
      aws_cloudwatch_event_bus.email_routing.arn
    ]
  }
}

# IAM policy for Pipes to send to Event Bus target
resource "aws_iam_role_policy" "eventbridge_pipes_event_bus_target" {
  name   = "pipes-event-bus-target-${var.environment}"
  role   = aws_iam_role.eventbridge_pipes_execution.id
  policy = data.aws_iam_policy_document.eventbridge_pipes_event_bus_target.json
}

# Attach AWS managed policy for X-Ray write access (for distributed tracing)
resource "aws_iam_role_policy_attachment" "eventbridge_pipes_xray_access" {
  role       = aws_iam_role.eventbridge_pipes_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# IAM policy document for Pipes to write CloudWatch Logs
data "aws_iam_policy_document" "eventbridge_pipes_logs" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = [
      "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/vendedlogs/pipes/*"
    ]
  }
}

# IAM policy for Pipes CloudWatch Logs
resource "aws_iam_role_policy" "eventbridge_pipes_logs" {
  name   = "pipes-cloudwatch-logs-${var.environment}"
  role   = aws_iam_role.eventbridge_pipes_execution.id
  policy = data.aws_iam_policy_document.eventbridge_pipes_logs.json
}

# ===========================
# EventBridge Pipe Resource
# ===========================

# EventBridge Pipe connecting SQS input queue to Event Bus via router enrichment
resource "aws_pipes_pipe" "email_router" {
  name     = "ses-email-router-${var.environment}"
  role_arn = aws_iam_role.eventbridge_pipes_execution.arn

  # Source: SQS input queue (receives messages from SNS)
  source = aws_sqs_queue.input_queue.arn

  # Target: EventBridge Event Bus (routes enriched messages to handlers)
  target = aws_cloudwatch_event_bus.email_routing.arn

  # Enrichment: Router lambda function (adds routing decisions)
  enrichment = aws_lambda_function.router_enrichment.arn

  description = "Enriches SES email messages with routing decisions and sends to Event Bus for handler dispatch"

  # ===========================
  # Source Configuration (SQS)
  # ===========================
  source_parameters {
    sqs_queue_parameters {
      batch_size                         = 1
      maximum_batching_window_in_seconds = 0
    }

    # Filter criteria (optional - currently accept all messages)
    # filter_criteria {}
  }

  # ===========================
  # Enrichment Configuration (Lambda)
  # ===========================
  enrichment_parameters {
    # Input transformation: Pass the entire SQS message to the enrichment lambda
    # For SQS sources, the message body is available as the root object
    input_template = "$.body"
  }

  # ===========================
  # Target Configuration (EventBridge Event Bus)
  # ===========================
  target_parameters {
    eventbridge_event_bus_parameters {
      # Event source identifier
      source = "ses.email.router"

      # Event detail type
      detail_type = "Email Routing Decision"
    }

    # The enrichment response is automatically used as the detail field
    # No input_template needed for EventBridge Event Bus target
  }

  # ===========================
  # Logging Configuration
  # ===========================
  log_configuration {
    level = "INFO"

    cloudwatch_logs_log_destination {
      log_group_arn = aws_cloudwatch_log_group.eventbridge_pipes_logs.arn
    }

    include_execution_data = ["ALL"]
  }

  tags = {
    Name        = "ses-email-router-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Enrich SES messages with routing decisions and dispatch to Event Bus"
  }

  depends_on = [
    aws_iam_role_policy.eventbridge_pipes_sqs_source,
    aws_iam_role_policy.eventbridge_pipes_lambda_enrichment,
    aws_iam_role_policy.eventbridge_pipes_event_bus_target,
    aws_iam_role_policy.eventbridge_pipes_logs,
    aws_iam_role_policy_attachment.eventbridge_pipes_xray_access
  ]
}

# ===========================
# CloudWatch Logs for EventBridge Pipes
# ===========================

# CloudWatch Log Group for EventBridge Pipes execution logs
resource "aws_cloudwatch_log_group" "eventbridge_pipes_logs" {
  name              = "/aws/vendedlogs/pipes/${var.environment}/ses-email-router"
  retention_in_days = 30

  tags = {
    Name        = "eventbridge-pipes-logs-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Execution logs for EventBridge Pipes email router"
  }
}

# ===========================
# CloudWatch Alarms for EventBridge Pipes
# ===========================

# CloudWatch metric filter for Pipes execution failures
resource "aws_cloudwatch_log_metric_filter" "eventbridge_pipes_failures" {
  name           = "eventbridge-pipes-failures-${var.environment}"
  log_group_name = aws_cloudwatch_log_group.eventbridge_pipes_logs.name
  pattern        = "{ $.level = \"ERROR\" }"

  metric_transformation {
    name      = "EventBridgePipesFailures"
    namespace = "SESMail/${var.environment}"
    value     = "1"
    unit      = "Count"
  }
}

# CloudWatch alarm for EventBridge Pipes failures
resource "aws_cloudwatch_metric_alarm" "eventbridge_pipes_failures" {
  alarm_name          = "eventbridge-pipes-failures-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EventBridgePipesFailures"
  namespace           = "SESMail/${var.environment}"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when EventBridge Pipes fails to process messages"
  alarm_actions       = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "eventbridge-pipes-failures-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}
