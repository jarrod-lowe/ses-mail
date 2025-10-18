# Dead letter queue for input queue
resource "aws_sqs_queue" "input_dlq" {
  name = "ses-email-input-dlq-${var.environment}"

  # Retain messages for 14 days to allow time for investigation
  message_retention_seconds = 1209600

  tags = {
    Name        = "ses-email-input-dlq-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Dead letter queue for SNS to EventBridge Pipes input"
  }
}

# Input queue for EventBridge Pipes (receives messages from SNS)
resource "aws_sqs_queue" "input_queue" {
  name = "ses-email-input-${var.environment}"

  # Message retention: 4 days (default)
  message_retention_seconds = 345600

  # Visibility timeout: 6x lambda timeout (30 seconds * 6 = 180 seconds)
  visibility_timeout_seconds = 180

  # Enable content-based deduplication for exactly-once processing
  # Note: This requires FIFO queue, so we'll use standard queue with at-least-once delivery
  # content_based_deduplication = true

  # Dead letter queue configuration
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.input_dlq.arn
    maxReceiveCount     = 3
  })

  # X-Ray tracing for SQS is automatic when using AWS SDK
  # Trace context is propagated via message attributes by SNS and consumed by EventBridge Pipes
  # No additional configuration needed - tracing is handled by the integrated services

  tags = {
    Name        = "ses-email-input-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Input queue for EventBridge Pipes enrichment"
  }
}

# SQS queue policy to allow SNS to send messages
resource "aws_sqs_queue_policy" "input_queue" {
  queue_url = aws_sqs_queue.input_queue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSNSToSendMessage"
        Effect = "Allow"
        Principal = {
          Service = "sns.amazonaws.com"
        }
        Action   = "SQS:SendMessage"
        Resource = aws_sqs_queue.input_queue.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_sns_topic.email_processing.arn
          }
        }
      }
    ]
  })
}

# SNS topic subscription to SQS input queue
resource "aws_sns_topic_subscription" "input_queue" {
  topic_arn = aws_sns_topic.email_processing.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.input_queue.arn

  # Enable raw message delivery to preserve SES event structure
  raw_message_delivery = true
}

# CloudWatch alarm for input DLQ messages
resource "aws_cloudwatch_metric_alarm" "input_dlq_alarm" {
  alarm_name          = "ses-email-input-dlq-messages-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Average"
  threshold           = 0
  alarm_description   = "Alert when messages appear in input queue DLQ"
  alarm_actions       = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.input_dlq.name
  }

  tags = {
    Name        = "ses-email-input-dlq-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# CloudWatch alarm for input queue age (messages waiting too long)
resource "aws_cloudwatch_metric_alarm" "input_queue_age_alarm" {
  alarm_name          = "ses-email-input-queue-age-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 900 # 15 minutes
  alarm_description   = "Alert when messages in input queue are older than 15 minutes"
  alarm_actions       = [var.alarm_sns_topic_arn]

  dimensions = {
    QueueName = aws_sqs_queue.input_queue.name
  }

  tags = {
    Name        = "ses-email-input-queue-age-alarm-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}
