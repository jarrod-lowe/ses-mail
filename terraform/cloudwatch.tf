# CloudWatch Log Metric Filter for accepted emails
resource "aws_cloudwatch_log_metric_filter" "emails_accepted" {
  name           = "ses-mail-emails-accepted"
  log_group_name = aws_cloudwatch_log_group.lambda_logs.name
  pattern        = "[timestamp, request_id, level = INFO*, msg = \"Processing email:\"]"

  metric_transformation {
    name      = "EmailsAccepted"
    namespace = "SESMail"
    value     = "1"
    unit      = "Count"
  }
}

# CloudWatch Log Metric Filter for spam emails
resource "aws_cloudwatch_log_metric_filter" "emails_spam" {
  name           = "ses-mail-emails-spam"
  log_group_name = aws_cloudwatch_log_group.lambda_logs.name
  pattern        = "[timestamp, request_id, level, msg = \"*Spam verdict: FAIL*\"]"

  metric_transformation {
    name      = "EmailsSpam"
    namespace = "SESMail"
    value     = "1"
    unit      = "Count"
  }
}

# CloudWatch Log Metric Filter for virus emails
resource "aws_cloudwatch_log_metric_filter" "emails_virus" {
  name           = "ses-mail-emails-virus"
  log_group_name = aws_cloudwatch_log_group.lambda_logs.name
  pattern        = "[timestamp, request_id, level, msg = \"*Virus verdict: FAIL*\"]"

  metric_transformation {
    name      = "EmailsVirus"
    namespace = "SESMail"
    value     = "1"
    unit      = "Count"
  }
}

# CloudWatch Log Metric Filter for Lambda errors
resource "aws_cloudwatch_log_metric_filter" "lambda_errors" {
  name           = "ses-mail-lambda-errors"
  log_group_name = aws_cloudwatch_log_group.lambda_logs.name
  pattern        = "[timestamp, request_id, level = ERROR*, ...]"

  metric_transformation {
    name      = "LambdaErrors"
    namespace = "SESMail"
    value     = "1"
    unit      = "Count"
  }
}

# CloudWatch Dashboard
resource "aws_cloudwatch_dashboard" "ses_mail" {
  dashboard_name = "ses-mail-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          metrics = [
            ["SESMail", "EmailsAccepted", { stat = "Sum", label = "Accepted" }],
            [".", "EmailsSpam", { stat = "Sum", label = "Spam" }],
            [".", "EmailsVirus", { stat = "Sum", label = "Virus" }]
          ]
          period = 300
          stat   = "Sum"
          region = var.aws_region
          title  = "Email Processing"
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/Lambda", "Invocations", { stat = "Sum", label = "Lambda Invocations" }],
            [".", "Errors", { stat = "Sum", label = "Lambda Errors" }],
            ["SESMail", "LambdaErrors", { stat = "Sum", label = "Application Errors" }]
          ]
          period = 300
          stat   = "Sum"
          region = var.aws_region
          title  = "Lambda Performance"
        }
      },
      {
        type = "metric"
        properties = {
          metrics = [
            ["AWS/Lambda", "Duration", { stat = "Average" }]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "Lambda Duration (ms)"
        }
      },
      {
        type = "log"
        properties = {
          query   = "SOURCE '${aws_cloudwatch_log_group.lambda_logs.name}' | fields @timestamp, @message | filter @message like /Processing email:/ | sort @timestamp desc | limit 20"
          region  = var.aws_region
          title   = "Recent Emails"
          stacked = false
        }
      }
    ]
  })
}

# CloudWatch Alarm for high email volume
resource "aws_cloudwatch_metric_alarm" "high_email_volume" {
  alarm_name          = "ses-mail-high-email-volume"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "EmailsAccepted"
  namespace           = "SESMail"
  period              = 300
  statistic           = "Sum"
  threshold           = var.alarm_email_count_threshold
  alarm_description   = "Alert when email volume exceeds threshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
}

# CloudWatch Alarm for high spam rate
resource "aws_cloudwatch_metric_alarm" "high_spam_rate" {
  alarm_name          = "ses-mail-high-spam-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = var.alarm_rejection_rate_threshold
  alarm_description   = "Alert when spam detection rate is high"
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "spam_rate"
    expression  = "(spam / accepted) * 100"
    label       = "Spam Rate (%)"
    return_data = true
  }

  metric_query {
    id = "spam"
    metric {
      metric_name = "EmailsSpam"
      namespace   = "SESMail"
      period      = 300
      stat        = "Sum"
    }
  }

  metric_query {
    id = "accepted"
    metric {
      metric_name = "EmailsAccepted"
      namespace   = "SESMail"
      period      = 300
      stat        = "Sum"
    }
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
}

# CloudWatch Alarm for Lambda errors
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "ses-mail-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "Alert when Lambda function has errors"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.email_processor.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
}
