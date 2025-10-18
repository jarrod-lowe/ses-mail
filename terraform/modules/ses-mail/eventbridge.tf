# ===========================
# EventBridge Event Bus
# ===========================

# Custom Event Bus for email routing
resource "aws_cloudwatch_event_bus" "email_routing" {
  name = "ses-email-routing-${var.environment}"

  tags = {
    Name        = "ses-email-routing-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Event bus for routing enriched email messages to handlers"
  }
}

# ===========================
# IAM Role for EventBridge Rules
# ===========================

# IAM policy document for EventBridge assume role
data "aws_iam_policy_document" "eventbridge_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

# IAM role for EventBridge rules to send messages to SQS
resource "aws_iam_role" "eventbridge_sqs" {
  name               = "ses-mail-eventbridge-sqs-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.eventbridge_assume_role.json

  tags = {
    Name        = "ses-mail-eventbridge-sqs-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# IAM policy document for EventBridge to send messages to SQS queues
data "aws_iam_policy_document" "eventbridge_sqs_access" {
  statement {
    effect = "Allow"
    actions = [
      "sqs:SendMessage"
    ]
    resources = [
      aws_sqs_queue.gmail_forwarder.arn,
      aws_sqs_queue.bouncer.arn
    ]
  }
}

# IAM policy for EventBridge to access SQS
resource "aws_iam_role_policy" "eventbridge_sqs_access" {
  name   = "eventbridge-sqs-access-${var.environment}"
  role   = aws_iam_role.eventbridge_sqs.id
  policy = data.aws_iam_policy_document.eventbridge_sqs_access.json
}

# ===========================
# EventBridge Rules and Targets
# ===========================

# EventBridge rule for routing to Gmail forwarder
resource "aws_cloudwatch_event_rule" "gmail_forwarder" {
  name           = "route-to-gmail-${var.environment}"
  description    = "Route emails with forward-to-gmail action to Gmail forwarder queue"
  event_bus_name = aws_cloudwatch_event_bus.email_routing.name

  # Match events from router enrichment with forward-to-gmail action
  event_pattern = jsonencode({
    source = ["ses.email.router"]
    detail = {
      routingDecisions = {
        action = ["forward-to-gmail"]
      }
    }
  })

  tags = {
    Name        = "route-to-gmail-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Route forward-to-gmail actions to Gmail forwarder queue"
  }
}

# Target for Gmail forwarder rule (sends to SQS queue)
resource "aws_cloudwatch_event_target" "gmail_forwarder" {
  rule           = aws_cloudwatch_event_rule.gmail_forwarder.name
  event_bus_name = aws_cloudwatch_event_bus.email_routing.name
  target_id      = "gmail-forwarder-queue"
  arn            = aws_sqs_queue.gmail_forwarder.arn
  role_arn       = aws_iam_role.eventbridge_sqs.arn

  # Dead letter queue configuration for failed event deliveries
  dead_letter_config {
    arn = aws_sqs_queue.gmail_forwarder_dlq.arn
  }

  # Retry policy for transient failures
  retry_policy {
    maximum_event_age_in_seconds = 3600 # 1 hour
    maximum_retry_attempts       = 2
  }
}

# EventBridge rule for routing to bouncer
resource "aws_cloudwatch_event_rule" "bouncer" {
  name           = "route-to-bouncer-${var.environment}"
  description    = "Route emails with bounce action to bouncer queue"
  event_bus_name = aws_cloudwatch_event_bus.email_routing.name

  # Match events from router enrichment with bounce action
  event_pattern = jsonencode({
    source = ["ses.email.router"]
    detail = {
      routingDecisions = {
        action = ["bounce"]
      }
    }
  })

  tags = {
    Name        = "route-to-bouncer-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Route bounce actions to bouncer queue"
  }
}

# Target for bouncer rule (sends to SQS queue)
resource "aws_cloudwatch_event_target" "bouncer" {
  rule           = aws_cloudwatch_event_rule.bouncer.name
  event_bus_name = aws_cloudwatch_event_bus.email_routing.name
  target_id      = "bouncer-queue"
  arn            = aws_sqs_queue.bouncer.arn
  role_arn       = aws_iam_role.eventbridge_sqs.arn

  # Dead letter queue configuration for failed event deliveries
  dead_letter_config {
    arn = aws_sqs_queue.bouncer_dlq.arn
  }

  # Retry policy for transient failures
  retry_policy {
    maximum_event_age_in_seconds = 3600 # 1 hour
    maximum_retry_attempts       = 2
  }
}

# ===========================
# SQS Queue Policies for EventBridge
# ===========================

# IAM policy document for Gmail forwarder queue to allow EventBridge
data "aws_iam_policy_document" "gmail_forwarder_eventbridge_access" {
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
      aws_sqs_queue.gmail_forwarder.arn
    ]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.gmail_forwarder.arn]
    }
  }
}

# SQS queue policy to allow EventBridge to send messages to Gmail forwarder queue
resource "aws_sqs_queue_policy" "gmail_forwarder_eventbridge" {
  queue_url = aws_sqs_queue.gmail_forwarder.id
  policy    = data.aws_iam_policy_document.gmail_forwarder_eventbridge_access.json
}

# IAM policy document for bouncer queue to allow EventBridge
data "aws_iam_policy_document" "bouncer_eventbridge_access" {
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
      aws_sqs_queue.bouncer.arn
    ]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.bouncer.arn]
    }
  }
}

# SQS queue policy to allow EventBridge to send messages to bouncer queue
resource "aws_sqs_queue_policy" "bouncer_eventbridge" {
  queue_url = aws_sqs_queue.bouncer.id
  policy    = data.aws_iam_policy_document.bouncer_eventbridge_access.json
}

# ===========================
# CloudWatch Alarms for EventBridge
# ===========================

# CloudWatch log group for EventBridge Event Bus
# Note: EventBridge doesn't automatically create log groups, so we create it manually
resource "aws_cloudwatch_log_group" "eventbridge_logs" {
  name              = "/aws/events/${aws_cloudwatch_event_bus.email_routing.name}"
  retention_in_days = 30

  tags = {
    Name        = "eventbridge-logs-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# CloudWatch metric filter for EventBridge rule failures (Gmail forwarder)
resource "aws_cloudwatch_log_metric_filter" "eventbridge_gmail_failures" {
  depends_on     = [aws_cloudwatch_log_group.eventbridge_logs]
  name           = "eventbridge-gmail-failures-${var.environment}"
  log_group_name = "/aws/events/${aws_cloudwatch_event_bus.email_routing.name}"
  pattern        = "[time, request_id, event_id, rule_name=${aws_cloudwatch_event_rule.gmail_forwarder.name}, ...]"

  metric_transformation {
    name      = "EventBridgeGmailRuleFailures"
    namespace = "SESMail/${var.environment}"
    value     = "1"
    unit      = "Count"
  }
}

# CloudWatch alarm for EventBridge Gmail forwarder rule failures
resource "aws_cloudwatch_metric_alarm" "eventbridge_gmail_failures" {
  alarm_name          = "eventbridge-gmail-failures-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EventBridgeGmailRuleFailures"
  namespace           = "SESMail/${var.environment}"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when EventBridge Gmail forwarder rule fails to deliver events"
  alarm_actions       = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "eventbridge-gmail-failures-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# CloudWatch metric filter for EventBridge rule failures (Bouncer)
resource "aws_cloudwatch_log_metric_filter" "eventbridge_bouncer_failures" {
  depends_on     = [aws_cloudwatch_log_group.eventbridge_logs]
  name           = "eventbridge-bouncer-failures-${var.environment}"
  log_group_name = "/aws/events/${aws_cloudwatch_event_bus.email_routing.name}"
  pattern        = "[time, request_id, event_id, rule_name=${aws_cloudwatch_event_rule.bouncer.name}, ...]"

  metric_transformation {
    name      = "EventBridgeBouncerRuleFailures"
    namespace = "SESMail/${var.environment}"
    value     = "1"
    unit      = "Count"
  }
}

# CloudWatch alarm for EventBridge bouncer rule failures
resource "aws_cloudwatch_metric_alarm" "eventbridge_bouncer_failures" {
  alarm_name          = "eventbridge-bouncer-failures-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EventBridgeBouncerRuleFailures"
  namespace           = "SESMail/${var.environment}"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when EventBridge bouncer rule fails to deliver events"
  alarm_actions       = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "eventbridge-bouncer-failures-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}
