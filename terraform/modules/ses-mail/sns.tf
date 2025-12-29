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

# IAM policy document for SNS topic to allow SES to publish
data "aws_iam_policy_document" "sns_email_processing_policy" {
  statement {
    sid    = "AllowSESToPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.email_processing.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# SNS topic policy to allow SES to publish messages
resource "aws_sns_topic_policy" "email_processing" {
  arn = aws_sns_topic.email_processing.arn

  policy = data.aws_iam_policy_document.sns_email_processing_policy.json
}
