# ===========================
# Gmail Forwarder Queue Infrastructure
# ===========================

# Dead letter queue for Gmail forwarder
resource "aws_sqs_queue" "gmail_forwarder_dlq" {
  name = "ses-gmail-forwarder-dlq-${var.environment}"

  # Retain messages for 14 days to allow time for investigation
  message_retention_seconds = 1209600

  tags = {
    Name        = "ses-gmail-forwarder-dlq-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Dead letter queue for Gmail forwarder handler"
  }
}

# Gmail forwarder queue (receives messages from EventBridge Event Bus)
resource "aws_sqs_queue" "gmail_forwarder" {
  name = "ses-gmail-forwarder-${var.environment}"

  # Message retention: 4 days (default)
  message_retention_seconds = 345600

  # Visibility timeout: 30 seconds (Gmail forwarder lambda timeout is 3s, using 10x = 30s)
  visibility_timeout_seconds = 30

  # Dead letter queue configuration
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.gmail_forwarder_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Name        = "ses-gmail-forwarder-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Handler queue for Gmail forwarding actions"
  }
}

# CloudWatch alarm for Gmail forwarder DLQ messages
resource "aws_cloudwatch_metric_alarm" "gmail_forwarder_dlq_alarm" {
  alarm_name          = "ses-gmail-forwarder-dlq-messages-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Average"
  threshold           = 0
  alarm_description   = "Alert when messages appear in Gmail forwarder DLQ"
  alarm_actions       = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.gmail_forwarder_dlq.name
  }

  tags = {
    Name        = "ses-gmail-forwarder-dlq-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# CloudWatch alarm for Gmail forwarder queue age (messages waiting too long)
resource "aws_cloudwatch_metric_alarm" "gmail_forwarder_queue_age_alarm" {
  alarm_name          = "ses-gmail-forwarder-queue-age-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 300 # 5 minutes
  alarm_description   = "Alert when messages in Gmail forwarder queue are older than 5 minutes"
  alarm_actions       = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.gmail_forwarder.name
  }

  tags = {
    Name        = "ses-gmail-forwarder-queue-age-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# ===========================
# Bouncer Queue Infrastructure
# ===========================

# Dead letter queue for bouncer
resource "aws_sqs_queue" "bouncer_dlq" {
  name = "ses-bouncer-dlq-${var.environment}"

  # Retain messages for 14 days to allow time for investigation
  message_retention_seconds = 1209600

  tags = {
    Name        = "ses-bouncer-dlq-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Dead letter queue for bouncer handler"
  }
}

# Bouncer queue (receives messages from EventBridge Event Bus)
resource "aws_sqs_queue" "bouncer" {
  name = "ses-bouncer-${var.environment}"

  # Message retention: 4 days (default)
  message_retention_seconds = 345600

  # Visibility timeout: 180 seconds (bouncer lambda timeout is 30s, using 6x = 180s)
  visibility_timeout_seconds = 180

  # Dead letter queue configuration
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.bouncer_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Name        = "ses-bouncer-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Handler queue for bounce actions"
  }
}

# CloudWatch alarm for bouncer DLQ messages
resource "aws_cloudwatch_metric_alarm" "bouncer_dlq_alarm" {
  alarm_name          = "ses-bouncer-dlq-messages-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Average"
  threshold           = 0
  alarm_description   = "Alert when messages appear in bouncer DLQ"
  alarm_actions       = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.bouncer_dlq.name
  }

  tags = {
    Name        = "ses-bouncer-dlq-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# CloudWatch alarm for bouncer queue age (messages waiting too long)
resource "aws_cloudwatch_metric_alarm" "bouncer_queue_age_alarm" {
  alarm_name          = "ses-bouncer-queue-age-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 300 # 5 minutes
  alarm_description   = "Alert when messages in bouncer queue are older than 5 minutes"
  alarm_actions       = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.bouncer.name
  }

  tags = {
    Name        = "ses-bouncer-queue-age-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}
