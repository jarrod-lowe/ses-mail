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
  ok_actions          = [var.alarm_sns_topic_arn]

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
  ok_actions          = [var.alarm_sns_topic_arn]

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
# Gmail Forwarder Retry Queue Infrastructure
# ===========================

# Dead letter queue for Gmail forwarder retry
resource "aws_sqs_queue" "gmail_forwarder_retry_dlq" {
  name = "ses-mail-gmail-forwarder-retry-dlq-${var.environment}"

  # Retain messages for 14 days to allow time for investigation
  message_retention_seconds = 1209600

  tags = {
    Name        = "ses-mail-gmail-forwarder-retry-dlq-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Dead letter queue for Gmail forwarder retry processing"
  }
}

# Gmail forwarder retry queue (receives messages from Gmail forwarder lambda when token expires)
resource "aws_sqs_queue" "gmail_forwarder_retry" {
  name = "ses-mail-gmail-forwarder-retry-${var.environment}"

  # Message retention: 14 days (to allow for extended recovery scenarios)
  message_retention_seconds = 1209600

  # Visibility timeout: 15 minutes (to allow Step Function retry processing)
  visibility_timeout_seconds = 900

  # Dead letter queue configuration
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.gmail_forwarder_retry_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Name        = "ses-mail-gmail-forwarder-retry-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Retry queue for Gmail token expiration failures"
  }
}

# CloudWatch alarm for Gmail forwarder retry DLQ messages
resource "aws_cloudwatch_metric_alarm" "gmail_forwarder_retry_dlq_alarm" {
  alarm_name          = "ses-mail-gmail-forwarder-retry-dlq-messages-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Average"
  threshold           = 0
  alarm_description   = "Alert when messages appear in Gmail forwarder retry DLQ"
  alarm_actions       = [var.alarm_sns_topic_arn]
  ok_actions          = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.gmail_forwarder_retry_dlq.name
  }

  tags = {
    Name        = "ses-mail-gmail-forwarder-retry-dlq-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# CloudWatch alarm for Gmail forwarder retry queue age (messages waiting too long)
resource "aws_cloudwatch_metric_alarm" "gmail_forwarder_retry_queue_age_alarm" {
  alarm_name          = "ses-mail-gmail-forwarder-retry-queue-age-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 900 # 15 minutes
  alarm_description   = "Alert when messages in Gmail forwarder retry queue are older than 15 minutes"
  alarm_actions       = [var.alarm_sns_topic_arn]
  ok_actions          = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.gmail_forwarder_retry.name
  }

  tags = {
    Name        = "ses-mail-gmail-forwarder-retry-queue-age-alarm-${var.environment}"
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
  ok_actions          = [var.alarm_sns_topic_arn]

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
  ok_actions          = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.bouncer.name
  }

  tags = {
    Name        = "ses-bouncer-queue-age-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}
