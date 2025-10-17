# SNS topic for email processing pipeline with X-Ray tracing
resource "aws_sns_topic" "email_processing" {
  name = "ses-email-processing-${var.environment}"

  # Enable X-Ray Active tracing to initiate distributed traces
  tracing_config = "Active"

  tags = {
    Name        = "ses-email-processing-${var.environment}"
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
