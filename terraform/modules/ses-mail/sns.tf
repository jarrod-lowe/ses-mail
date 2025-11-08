# SNS topic for email processing pipeline with X-Ray tracing
resource "aws_sns_topic" "email_processing" {
  name = "ses-mail-email-processing-${var.environment}"

  # Enable X-Ray Active tracing to initiate distributed traces
  tracing_config = "Active"

  tags = {
    Name        = "ses-mail-email-processing-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Email processing pipeline message broker"
  }
}

# SNS topic policy to allow SES to publish messages
resource "aws_sns_topic_policy" "email_processing" {
  arn = aws_sns_topic.email_processing.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSESToPublish"
        Effect = "Allow"
        Principal = {
          Service = "ses.amazonaws.com"
        }
        Action   = "SNS:Publish"
        Resource = aws_sns_topic.email_processing.arn
        Condition = {
          StringEquals = {
            "AWS:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })
}

# SNS topic for Gmail OAuth token alerts and retry processing notifications
resource "aws_sns_topic" "gmail_token_alerts" {
  name = "ses-mail-gmail-forwarder-token-alerts-${var.environment}"

  tags = {
    Name        = "ses-mail-gmail-forwarder-token-alerts-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Gmail OAuth token expiration and retry processing alerts"
  }
}
