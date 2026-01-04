# SNS topics for outbound email event notifications from SES Configuration Set

# SNS topic for outbound email send events
resource "aws_sns_topic" "outbound_send" {
  name = "ses-mail-outbound-send-${var.environment}"

  # Enable X-Ray Active tracing to initiate distributed traces
  tracing_config = "Active"

  tags = {
    Name        = "ses-mail-outbound-send-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Outbound email send event notifications"
  }
}

# SNS topic for outbound email delivery events
resource "aws_sns_topic" "outbound_delivery" {
  name = "ses-mail-outbound-delivery-${var.environment}"

  tracing_config = "Active"

  tags = {
    Name        = "ses-mail-outbound-delivery-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Outbound email delivery event notifications"
  }
}

# SNS topic for outbound email bounce events
resource "aws_sns_topic" "outbound_bounce" {
  name = "ses-mail-outbound-bounce-${var.environment}"

  tracing_config = "Active"

  tags = {
    Name        = "ses-mail-outbound-bounce-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Outbound email bounce event notifications"
  }
}

# SNS topic for outbound email complaint events
resource "aws_sns_topic" "outbound_complaint" {
  name = "ses-mail-outbound-complaint-${var.environment}"

  tracing_config = "Active"

  tags = {
    Name        = "ses-mail-outbound-complaint-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Outbound email complaint event notifications"
  }
}

# IAM policy document for outbound send SNS topic
data "aws_iam_policy_document" "sns_outbound_send_policy" {
  statement {
    sid    = "AllowSESToPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.outbound_send.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# IAM policy document for outbound delivery SNS topic
data "aws_iam_policy_document" "sns_outbound_delivery_policy" {
  statement {
    sid    = "AllowSESToPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.outbound_delivery.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# IAM policy document for outbound bounce SNS topic
data "aws_iam_policy_document" "sns_outbound_bounce_policy" {
  statement {
    sid    = "AllowSESToPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.outbound_bounce.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# IAM policy document for outbound complaint SNS topic
data "aws_iam_policy_document" "sns_outbound_complaint_policy" {
  statement {
    sid    = "AllowSESToPublish"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.outbound_complaint.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# SNS topic policies to allow SES to publish messages
resource "aws_sns_topic_policy" "outbound_send" {
  arn    = aws_sns_topic.outbound_send.arn
  policy = data.aws_iam_policy_document.sns_outbound_send_policy.json
}

resource "aws_sns_topic_policy" "outbound_delivery" {
  arn    = aws_sns_topic.outbound_delivery.arn
  policy = data.aws_iam_policy_document.sns_outbound_delivery_policy.json
}

resource "aws_sns_topic_policy" "outbound_bounce" {
  arn    = aws_sns_topic.outbound_bounce.arn
  policy = data.aws_iam_policy_document.sns_outbound_bounce_policy.json
}

resource "aws_sns_topic_policy" "outbound_complaint" {
  arn    = aws_sns_topic.outbound_complaint.arn
  policy = data.aws_iam_policy_document.sns_outbound_complaint_policy.json
}

# SNS topic subscriptions to Lambda function (will be created after Lambda exists)
resource "aws_sns_topic_subscription" "send_to_lambda" {
  topic_arn = aws_sns_topic.outbound_send.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.outbound_metrics_publisher.arn
}

resource "aws_sns_topic_subscription" "delivery_to_lambda" {
  topic_arn = aws_sns_topic.outbound_delivery.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.outbound_metrics_publisher.arn
}

resource "aws_sns_topic_subscription" "bounce_to_lambda" {
  topic_arn = aws_sns_topic.outbound_bounce.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.outbound_metrics_publisher.arn
}

resource "aws_sns_topic_subscription" "complaint_to_lambda" {
  topic_arn = aws_sns_topic.outbound_complaint.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.outbound_metrics_publisher.arn
}
