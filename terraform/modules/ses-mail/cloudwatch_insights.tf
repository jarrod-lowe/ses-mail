# CloudWatch Logs Insights Query Definition for Router Enrichment Errors
resource "aws_cloudwatch_query_definition" "router_errors" {
  name = "ses-mail/${var.environment}/router-enrichment-errors"

  log_group_names = [
    aws_cloudwatch_log_group.lambda_router_logs.name
  ]

  query_string = <<-QUERY
    fields @timestamp, @message, @logStream
    | filter @message like /ERROR/
    | parse @message /messageId: (?<messageId>[^\s]+)/
    | parse @message /recipient: (?<recipient>[^\s]+)/
    | parse @message /action: (?<action>[^\s]+)/
    | sort @timestamp desc
    | limit 100
  QUERY
}

# CloudWatch Logs Insights Query Definition for Gmail Forwarder Failures
resource "aws_cloudwatch_query_definition" "gmail_forwarder_failures" {
  name = "ses-mail/${var.environment}/gmail-forwarder-failures"

  log_group_names = [
    aws_cloudwatch_log_group.lambda_gmail_forwarder_logs.name
  ]

  query_string = <<-QUERY
    fields @timestamp, @message, @logStream
    | filter @message like /ERROR/ or @message like /Failed/
    | parse @message /messageId: (?<messageId>[^\s]+)/
    | parse @message /recipient: (?<recipient>[^\s]+)/
    | parse @message /target: (?<target>[^\s]+)/
    | sort @timestamp desc
    | limit 100
  QUERY
}

# CloudWatch Logs Insights Query Definition for Bouncer Failures
resource "aws_cloudwatch_query_definition" "bouncer_failures" {
  name = "ses-mail/${var.environment}/bouncer-failures"

  log_group_names = [
    aws_cloudwatch_log_group.lambda_bouncer_logs.name
  ]

  query_string = <<-QUERY
    fields @timestamp, @message, @logStream
    | filter @message like /ERROR/ or @message like /Failed/
    | parse @message /messageId: (?<messageId>[^\s]+)/
    | parse @message /source: (?<source>[^\s]+)/
    | parse @message /recipient: (?<recipient>[^\s]+)/
    | sort @timestamp desc
    | limit 100
  QUERY
}

# CloudWatch Logs Insights Query Definition for Routing Decision Analysis
resource "aws_cloudwatch_query_definition" "routing_decisions" {
  name = "ses-mail/${var.environment}/routing-decision-analysis"

  log_group_names = [
    aws_cloudwatch_log_group.lambda_router_logs.name
  ]

  query_string = <<-QUERY
    fields @timestamp, @message
    | filter @message like /Routing decision/
    | parse @message /recipient: (?<recipient>[^\s]+)/
    | parse @message /action: (?<action>[^\s]+)/
    | parse @message /matchedRule: (?<matchedRule>[^\s]+)/
    | stats count() by action, matchedRule
    | sort count desc
  QUERY
}

# CloudWatch Logs Insights Query Definition for End-to-End Email Tracing
resource "aws_cloudwatch_query_definition" "email_trace" {
  name = "ses-mail/${var.environment}/email-end-to-end-trace"

  log_group_names = [
    aws_cloudwatch_log_group.lambda_router_logs.name,
    aws_cloudwatch_log_group.lambda_gmail_forwarder_logs.name,
    aws_cloudwatch_log_group.lambda_bouncer_logs.name
  ]

  query_string = <<-QUERY
    fields @timestamp, @logStream, @message
    | filter @message like /messageId:/
    | parse @message /messageId: (?<messageId>[^\s]+)/
    | parse @message /(?<stage>Router|Gmail|Bouncer)/
    | sort @timestamp asc
    | limit 200
  QUERY
}

# CloudWatch Logs Insights Query Definition for DLQ Message Investigation
resource "aws_cloudwatch_query_definition" "dlq_investigation" {
  name = "ses-mail/${var.environment}/dlq-message-investigation"

  log_group_names = [
    aws_cloudwatch_log_group.lambda_router_logs.name,
    aws_cloudwatch_log_group.lambda_gmail_forwarder_logs.name,
    aws_cloudwatch_log_group.lambda_bouncer_logs.name
  ]

  query_string = <<-QUERY
    fields @timestamp, @message, @logStream
    | filter @message like /Failed/ or @message like /retry/ or @message like /DLQ/
    | parse @message /messageId: (?<messageId>[^\s]+)/
    | parse @message /receiptHandle: (?<receiptHandle>[^\s]+)/
    | sort @timestamp desc
    | limit 50
  QUERY
}

# CloudWatch Logs Insights Query Definition for Performance Analysis
resource "aws_cloudwatch_query_definition" "performance_analysis" {
  name = "ses-mail/${var.environment}/performance-analysis"

  log_group_names = [
    aws_cloudwatch_log_group.lambda_router_logs.name,
    aws_cloudwatch_log_group.lambda_gmail_forwarder_logs.name,
    aws_cloudwatch_log_group.lambda_bouncer_logs.name
  ]

  query_string = <<-QUERY
    fields @timestamp, @duration, @logStream
    | filter @type = "REPORT"
    | stats avg(@duration), max(@duration), min(@duration), count() by @logStream
    | sort avg(@duration) desc
  QUERY
}
