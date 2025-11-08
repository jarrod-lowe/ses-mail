# CloudWatch Dashboard
resource "aws_cloudwatch_dashboard" "ses_mail" {
  dashboard_name = "ses-mail-dashboard-${var.environment}"

  dashboard_body = jsonencode({
    widgets = [
      # Email Processing Overview
      {
        type   = "metric"
        width  = 12
        height = 6
        x      = 0
        y      = 0
        properties = {
          metrics = [
            ["SESMail/${var.environment}", "EmailsAccepted", { stat = "Sum", label = "Accepted" }],
            [".", "EmailsSpam", { stat = "Sum", label = "Spam" }],
            [".", "EmailsVirus", { stat = "Sum", label = "Virus" }]
          ]
          period = 300
          stat   = "Sum"
          region = var.aws_region
          title  = "Email Processing Overview"
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # Handler Success/Failure Rates
      {
        type   = "metric"
        width  = 12
        height = 6
        x      = 12
        y      = 0
        properties = {
          metrics = [
            ["SESMail/${var.environment}", "RouterEnrichmentSuccess", { stat = "Sum", label = "Router Success", color = "#2ca02c" }],
            [".", "RouterEnrichmentFailure", { stat = "Sum", label = "Router Failure", color = "#d62728" }],
            [".", "GmailForwardSuccess", { stat = "Sum", label = "Gmail Success", color = "#1f77b4" }],
            [".", "GmailForwardFailure", { stat = "Sum", label = "Gmail Failure", color = "#ff7f0e" }],
            [".", "BounceSendSuccess", { stat = "Sum", label = "Bounce Success", color = "#9467bd" }],
            [".", "BounceSendFailure", { stat = "Sum", label = "Bounce Failure", color = "#8c564b" }]
          ]
          period = 300
          stat   = "Sum"
          region = var.aws_region
          title  = "Handler Success/Failure Rates"
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # Lambda Function Errors
      {
        type   = "metric"
        width  = 12
        height = 6
        x      = 0
        y      = 6
        properties = {
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.router_enrichment.function_name, { stat = "Sum", label = "Router" }],
            ["...", aws_lambda_function.gmail_forwarder.function_name, { stat = "Sum", label = "Gmail Forwarder" }],
            ["...", aws_lambda_function.bouncer.function_name, { stat = "Sum", label = "Bouncer" }]
          ]
          period = 300
          stat   = "Sum"
          region = var.aws_region
          title  = "Lambda Function Errors"
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # Lambda Function Invocations
      {
        type   = "metric"
        width  = 12
        height = 6
        x      = 12
        y      = 6
        properties = {
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.router_enrichment.function_name, { stat = "Sum", label = "Router" }],
            ["...", aws_lambda_function.gmail_forwarder.function_name, { stat = "Sum", label = "Gmail Forwarder" }],
            ["...", aws_lambda_function.bouncer.function_name, { stat = "Sum", label = "Bouncer" }]
          ]
          period = 300
          stat   = "Sum"
          region = var.aws_region
          title  = "Lambda Function Invocations"
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # SQS Queue Depths
      {
        type   = "metric"
        width  = 12
        height = 6
        x      = 0
        y      = 12
        properties = {
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.gmail_forwarder.name, { stat = "Average", label = "Gmail Queue" }],
            ["...", aws_sqs_queue.bouncer.name, { stat = "Average", label = "Bouncer Queue" }]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "SQS Queue Depths"
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # DLQ Message Counts
      {
        type   = "metric"
        width  = 12
        height = 6
        x      = 12
        y      = 12
        properties = {
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.gmail_forwarder_dlq.name, { stat = "Average", label = "Gmail DLQ" }],
            ["...", aws_sqs_queue.bouncer_dlq.name, { stat = "Average", label = "Bouncer DLQ" }]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "Dead Letter Queue Messages"
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # Lambda Duration
      {
        type   = "metric"
        width  = 12
        height = 6
        x      = 0
        y      = 18
        properties = {
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", aws_lambda_function.router_enrichment.function_name, { stat = "Average", label = "Router (avg)" }],
            ["...", aws_lambda_function.gmail_forwarder.function_name, { stat = "Average", label = "Gmail (avg)" }],
            ["...", aws_lambda_function.bouncer.function_name, { stat = "Average", label = "Bouncer (avg)" }]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "Lambda Duration (ms)"
          yAxis = {
            left = {
              min = 0
            }
          }
        }
      },
      # Retry Queue Metrics
      {
        type   = "metric"
        width  = 12
        height = 6
        x      = 0
        y      = 24
        properties = {
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.gmail_forwarder_retry.name, { stat = "Average", label = "Retry Queue Depth", color = "#ff7f0e" }],
            [".", "ApproximateAgeOfOldestMessage", ".", ".", { stat = "Maximum", label = "Retry Queue Age (s)", yAxis = "right", color = "#d62728" }],
            [".", "ApproximateNumberOfMessagesVisible", ".", aws_sqs_queue.gmail_forwarder_retry_dlq.name, { stat = "Average", label = "Retry DLQ", color = "#e377c2" }]
          ]
          period = 300
          stat   = "Average"
          region = var.aws_region
          title  = "Retry Queue Metrics (Token Expiration)"
          yAxis = {
            left = {
              min       = 0
              label     = "Message Count"
              showUnits = false
            }
            right = {
              min       = 0
              label     = "Age (seconds)"
              showUnits = false
            }
          }
        }
      },
      # Step Function Execution Metrics
      {
        type   = "metric"
        width  = 12
        height = 6
        x      = 12
        y      = 24
        properties = {
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.retry_processor.arn, { stat = "Sum", label = "Succeeded", color = "#2ca02c" }],
            [".", "ExecutionsFailed", ".", ".", { stat = "Sum", label = "Failed", color = "#d62728" }],
            [".", "ExecutionsTimedOut", ".", ".", { stat = "Sum", label = "Timed Out", color = "#ff7f0e" }],
            [".", "ExecutionThrottled", ".", ".", { stat = "Sum", label = "Throttled", color = "#9467bd" }],
            [".", "ExecutionTime", ".", ".", { stat = "Average", label = "Duration (ms)", yAxis = "right", color = "#1f77b4" }]
          ]
          period = 300
          stat   = "Sum"
          region = var.aws_region
          title  = "Step Function Retry Processor"
          yAxis = {
            left = {
              min       = 0
              label     = "Execution Count"
              showUnits = false
            }
            right = {
              min       = 0
              label     = "Duration (ms)"
              showUnits = false
            }
          }
        }
      }
    ]
  })
}

# CloudWatch Alarm for high email volume
resource "aws_cloudwatch_metric_alarm" "high_email_volume" {
  alarm_name          = "ses-mail-high-email-volume-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "EmailsAccepted"
  namespace           = "SESMail/${var.environment}"
  period              = 300
  statistic           = "Sum"
  threshold           = var.alarm_email_count_threshold
  alarm_description   = "Alert when email volume exceeds threshold (${var.environment})"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
}

# CloudWatch Alarm for high spam rate
resource "aws_cloudwatch_metric_alarm" "high_spam_rate" {
  alarm_name          = "ses-mail-high-spam-rate-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = var.alarm_rejection_rate_threshold
  alarm_description   = "Alert when spam detection rate is high (${var.environment})"
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
      namespace   = "SESMail/${var.environment}"
      period      = 300
      stat        = "Sum"
    }
  }

  metric_query {
    id = "accepted"
    metric {
      metric_name = "EmailsAccepted"
      namespace   = "SESMail/${var.environment}"
      period      = 300
      stat        = "Sum"
    }
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
}

# CloudWatch Alarm for router enrichment Lambda errors
resource "aws_cloudwatch_metric_alarm" "lambda_router_errors" {
  alarm_name          = "ses-mail-lambda-router-errors-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when router enrichment Lambda function has errors (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.router_enrichment.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
}

# CloudWatch Alarm for Gmail forwarder Lambda errors
resource "aws_cloudwatch_metric_alarm" "lambda_gmail_forwarder_errors" {
  alarm_name          = "ses-mail-lambda-gmail-forwarder-errors-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when Gmail forwarder Lambda function has errors (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.gmail_forwarder.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
}

# CloudWatch Alarm for bouncer Lambda errors
resource "aws_cloudwatch_metric_alarm" "lambda_bouncer_errors" {
  alarm_name          = "ses-mail-lambda-bouncer-errors-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when bouncer Lambda function has errors (${var.environment})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.bouncer.function_name
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]
}

# CloudWatch Alarm for Gmail OAuth refresh token expiration
resource "aws_cloudwatch_metric_alarm" "gmail_token_expiring" {
  alarm_name          = "ses-mail-gmail-forwarder-token-expiring-${var.environment}"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "TokenHoursUntilExpiration"
  namespace           = "SESMail/${var.environment}"
  period              = 3600 # 1 hour
  statistic           = "Minimum"
  threshold           = 24 # Alert when < 24 hours remaining
  alarm_description   = "Gmail OAuth refresh token expires in less than 24 hours (${var.environment})"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.gmail_token_alerts.arn]
  ok_actions    = [aws_sns_topic.gmail_token_alerts.arn]
}
