# ===========================
# Step Function for Retry Processing
# ===========================

# IAM policy document for Step Functions assume role (shared)
data "aws_iam_policy_document" "stepfunctions_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

# IAM policy document for retry processor SQS access
data "aws_iam_policy_document" "stepfunctions_retry_processor_sqs_access" {
  statement {
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes"
    ]
    resources = [aws_sqs_queue.gmail_forwarder_retry.arn]
  }
}

# IAM policy document for retry processor Lambda invoke
data "aws_iam_policy_document" "stepfunctions_retry_processor_lambda_invoke" {
  statement {
    effect = "Allow"
    actions = [
      "lambda:InvokeFunction"
    ]
    resources = [aws_lambda_function.gmail_forwarder.arn]
  }
}

# IAM policy document for retry processor CloudWatch Logs
data "aws_iam_policy_document" "stepfunctions_retry_processor_cloudwatch_logs" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups"
    ]
    resources = ["*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["${aws_cloudwatch_log_group.stepfunction_retry_processor_logs.arn}:*"]
  }
}

# IAM policy document for retry processor CloudWatch metrics
data "aws_iam_policy_document" "stepfunctions_retry_processor_cloudwatch_metrics" {
  statement {
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricData"
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["SESMail/${var.environment}"]
    }
  }
}

# IAM policy document for token monitor SSM access
data "aws_iam_policy_document" "stepfunctions_token_monitor_ssm_access" {
  statement {
    effect = "Allow"
    actions = [
      "ssm:GetParameter"
    ]
    resources = [aws_ssm_parameter.gmail_oauth_refresh_token.arn]
  }
}

# IAM policy document for token monitor CloudWatch metrics
data "aws_iam_policy_document" "stepfunctions_token_monitor_cloudwatch_metrics" {
  statement {
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricData"
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["SESMail/${var.environment}"]
    }
  }
}

# IAM policy document for token monitor CloudWatch Logs
data "aws_iam_policy_document" "stepfunctions_token_monitor_cloudwatch_logs" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups"
    ]
    resources = ["*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["${aws_cloudwatch_log_group.stepfunction_token_monitor_logs.arn}:*"]
  }
}

# IAM role for Step Function execution
resource "aws_iam_role" "stepfunction_retry_processor" {
  name = "ses-mail-stepfunction-retry-processor-${var.environment}"

  assume_role_policy = data.aws_iam_policy_document.stepfunctions_assume_role.json

  tags = {
    Name        = "ses-mail-stepfunction-retry-processor-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Step Function role for Gmail forwarder retry processing"
  }
}

# IAM policy for Step Function to read from SQS retry queue
resource "aws_iam_role_policy" "stepfunction_sqs_access" {
  name = "sqs-access"
  role = aws_iam_role.stepfunction_retry_processor.id

  policy = data.aws_iam_policy_document.stepfunctions_retry_processor_sqs_access.json
}

# IAM policy for Step Function to invoke Gmail Forwarder Lambda
resource "aws_iam_role_policy" "stepfunction_lambda_invoke" {
  name = "lambda-invoke"
  role = aws_iam_role.stepfunction_retry_processor.id

  policy = data.aws_iam_policy_document.stepfunctions_retry_processor_lambda_invoke.json
}

# IAM policy for Step Function CloudWatch Logs
resource "aws_iam_role_policy" "stepfunction_cloudwatch_logs" {
  name = "cloudwatch-logs"
  role = aws_iam_role.stepfunction_retry_processor.id

  policy = data.aws_iam_policy_document.stepfunctions_retry_processor_cloudwatch_logs.json
}

# IAM policy for Step Function X-Ray tracing
resource "aws_iam_role_policy_attachment" "stepfunction_xray_access" {
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
  role       = aws_iam_role.stepfunction_retry_processor.name
}

# IAM policy for Step Function to publish CloudWatch metrics
resource "aws_iam_role_policy" "stepfunction_cloudwatch_metrics" {
  name = "cloudwatch-metrics"
  role = aws_iam_role.stepfunction_retry_processor.id

  policy = data.aws_iam_policy_document.stepfunctions_retry_processor_cloudwatch_metrics.json
}

# CloudWatch Log Group for Step Function
resource "aws_cloudwatch_log_group" "stepfunction_retry_processor_logs" {
  name              = "/aws/states/ses-mail-gmail-forwarder-retry-processor-${var.environment}"
  retention_in_days = 30

  tags = {
    Name        = "stepfunction-retry-processor-logs-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}

# Step Function state machine for retry processing
resource "aws_sfn_state_machine" "retry_processor" {
  name     = "ses-mail-gmail-forwarder-retry-processor-${var.environment}"
  role_arn = aws_iam_role.stepfunction_retry_processor.arn

  # Enable X-Ray tracing for distributed tracing
  tracing_configuration {
    enabled = true
  }

  # Enable CloudWatch Logs
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.stepfunction_retry_processor_logs.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode(yamldecode(templatefile(
    "${path.module}/stepfunctions/retry-processor.yaml",
    {
      queue_url   = aws_sqs_queue.gmail_forwarder_retry.url
      lambda_arn  = aws_lambda_function.gmail_forwarder.arn
      environment = var.environment
    }
  )))

  tags = {
    Name        = "ses-mail-gmail-forwarder-retry-processor-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Process Gmail forwarder retry queue after token refresh"
  }

  depends_on = [
    aws_iam_role_policy.stepfunction_sqs_access,
    aws_iam_role_policy.stepfunction_lambda_invoke,
    aws_iam_role_policy.stepfunction_cloudwatch_logs,
    aws_iam_role_policy.stepfunction_cloudwatch_metrics,
    aws_cloudwatch_log_group.stepfunction_retry_processor_logs
  ]
}

# CloudWatch alarm for Step Function execution failures
resource "aws_cloudwatch_metric_alarm" "stepfunction_retry_processor_failed" {
  alarm_name          = "ses-mail-stepfunction-retry-processor-failed-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when Step Function retry processor execution fails"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.retry_processor.arn
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "ses-mail-stepfunction-retry-processor-failed-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Alert on Step Function retry processor failures"
  }
}

# CloudWatch alarm for Step Function execution timeouts
resource "aws_cloudwatch_metric_alarm" "stepfunction_retry_processor_timeout" {
  alarm_name          = "ses-mail-stepfunction-retry-processor-timeout-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionsTimedOut"
  namespace           = "AWS/States"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when Step Function retry processor execution times out"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.retry_processor.arn
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "ses-mail-stepfunction-retry-processor-timeout-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Alert on Step Function retry processor timeouts"
  }
}

# CloudWatch alarm for Step Function throttled executions
resource "aws_cloudwatch_metric_alarm" "stepfunction_retry_processor_throttled" {
  alarm_name          = "ses-mail-stepfunction-retry-processor-throttled-${var.environment}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ExecutionThrottled"
  namespace           = "AWS/States"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Alert when Step Function retry processor execution is throttled"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.retry_processor.arn
  }

  alarm_actions = [var.alarm_sns_topic_arn]
  ok_actions    = [var.alarm_sns_topic_arn]

  tags = {
    Name        = "ses-mail-stepfunction-retry-processor-throttled-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Alert on Step Function retry processor throttling"
  }
}

# ===========================
# Token Expiration Monitoring Resources
# ===========================

# IAM role for token monitor Step Function
resource "aws_iam_role" "stepfunction_token_monitor" {
  name = "ses-mail-stepfunction-token-monitor-${var.environment}"

  assume_role_policy = data.aws_iam_policy_document.stepfunctions_assume_role.json

  tags = {
    Name        = "ses-mail-stepfunction-token-monitor-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Step Function role for Gmail token expiration monitoring"
  }
}

# IAM policy for Step Function to read SSM parameter
resource "aws_iam_role_policy" "stepfunction_token_monitor_ssm" {
  name = "ssm-access"
  role = aws_iam_role.stepfunction_token_monitor.id

  policy = data.aws_iam_policy_document.stepfunctions_token_monitor_ssm_access.json
}

# IAM policy for Step Function to publish CloudWatch metrics
resource "aws_iam_role_policy" "stepfunction_token_monitor_cloudwatch" {
  name = "cloudwatch-metrics"
  role = aws_iam_role.stepfunction_token_monitor.id

  policy = data.aws_iam_policy_document.stepfunctions_token_monitor_cloudwatch_metrics.json
}

# IAM policy for Step Function CloudWatch Logs
resource "aws_iam_role_policy" "stepfunction_token_monitor_logs" {
  name = "cloudwatch-logs"
  role = aws_iam_role.stepfunction_token_monitor.id

  policy = data.aws_iam_policy_document.stepfunctions_token_monitor_cloudwatch_logs.json
}

# CloudWatch Log Group for token monitor Step Function
resource "aws_cloudwatch_log_group" "stepfunction_token_monitor_logs" {
  name              = "/aws/states/ses-mail-gmail-token-monitor-${var.environment}"
  retention_in_days = 7

  tags = {
    Name        = "stepfunction-token-monitor-logs-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
  }
}


# Step Function state machine for token expiration monitoring
resource "aws_sfn_state_machine" "token_monitor" {
  name     = "ses-mail-gmail-token-monitor-${var.environment}"
  role_arn = aws_iam_role.stepfunction_token_monitor.arn

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.stepfunction_token_monitor_logs.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  definition = jsonencode(yamldecode(templatefile(
    "${path.module}/stepfunctions/token-monitor.yaml",
    {
      parameter_name = aws_ssm_parameter.gmail_oauth_refresh_token.name
      environment    = var.environment
    }
  )))

  tags = {
    Name        = "ses-mail-gmail-token-monitor-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Monitor Gmail OAuth token expiration every 5 minutes"
  }

  depends_on = [
    aws_iam_role_policy.stepfunction_token_monitor_ssm,
    aws_iam_role_policy.stepfunction_token_monitor_cloudwatch,
    aws_iam_role_policy.stepfunction_token_monitor_logs,
    aws_cloudwatch_log_group.stepfunction_token_monitor_logs
  ]
}

