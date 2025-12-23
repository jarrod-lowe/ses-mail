# ===========================
# Step Function for Retry Processing
# ===========================

# IAM role for Step Function execution
resource "aws_iam_role" "stepfunction_retry_processor" {
  name = "ses-mail-stepfunction-retry-processor-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

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

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.gmail_forwarder_retry.arn
      }
    ]
  })
}

# IAM policy for Step Function to invoke Gmail Forwarder Lambda
resource "aws_iam_role_policy" "stepfunction_lambda_invoke" {
  name = "lambda-invoke"
  role = aws_iam_role.stepfunction_retry_processor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = aws_lambda_function.gmail_forwarder.arn
      }
    ]
  })
}

# IAM policy for Step Function CloudWatch Logs
resource "aws_iam_role_policy" "stepfunction_cloudwatch_logs" {
  name = "cloudwatch-logs"
  role = aws_iam_role.stepfunction_retry_processor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.stepfunction_retry_processor_logs.arn}:*"
      }
    ]
  })
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

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "SESMail/${var.environment}"
          }
        }
      }
    ]
  })
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

  definition = jsonencode({
    Comment = "Process retry queue messages and invoke Gmail Forwarder Lambda"
    StartAt = "ReadMessagesFromQueue"
    States = {
      # Read messages from the retry queue using AWS SDK integration
      ReadMessagesFromQueue = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:sqs:receiveMessage"
        Parameters = {
          QueueUrl            = aws_sqs_queue.gmail_forwarder_retry.url
          MaxNumberOfMessages = 10
          WaitTimeSeconds     = 20
        }
        ResultPath = "$.QueueMessages"
        Next       = "CheckIfMessagesExist"
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "HandleQueueReadError"
            ResultPath  = "$.Error"
          }
        ]
      }

      # Check if there are any messages to process
      CheckIfMessagesExist = {
        Type = "Choice"
        Choices = [
          {
            Variable  = "$.QueueMessages.Messages[0]"
            IsPresent = true
            Next      = "ProcessMessages"
          }
        ]
        Default = "NoMessagesToProcess"
      }

      # No messages found - complete successfully
      NoMessagesToProcess = {
        Type = "Succeed"
      }

      # Process each message using a Map state
      ProcessMessages = {
        Type           = "Map"
        ItemsPath      = "$.QueueMessages.Messages"
        MaxConcurrency = 1
        ResultPath     = "$.ProcessingResults"
        Iterator = {
          StartAt = "ParseMessageBody"
          States = {
            # Wrap message in SQS Records array format for Lambda
            ParseMessageBody = {
              Type = "Pass"
              Parameters = {
                "body.$"          = "$.Body"
                "receiptHandle.$" = "$.ReceiptHandle"
                "messageId.$"     = "$.MessageId"
              }
              Next = "InvokeGmailForwarder"
            }

            # Invoke Gmail Forwarder Lambda with SQS-formatted event
            InvokeGmailForwarder = {
              Type     = "Task"
              Resource = "arn:aws:states:::lambda:invoke"
              Parameters = {
                "FunctionName" = aws_lambda_function.gmail_forwarder.arn
                Payload = {
                  "Records.$" = "States.Array($)"
                }
              }
              TimeoutSeconds = 60
              Retry = [
                {
                  ErrorEquals = [
                    "Lambda.ServiceException",
                    "Lambda.AWSLambdaException",
                    "Lambda.SdkClientException",
                    "Lambda.TooManyRequestsException"
                  ]
                  IntervalSeconds = 30
                  MaxAttempts     = 3
                  BackoffRate     = 2.0
                }
              ]
              Catch = [
                {
                  ErrorEquals = ["States.ALL"]
                  Next        = "InvocationFailed"
                  ResultPath  = "$.LambdaError"
                }
              ]
              ResultPath = "$.LambdaResult"
              Next       = "DeleteMessageFromQueue"
            }

            # Delete message from queue after successful processing
            DeleteMessageFromQueue = {
              Type     = "Task"
              Resource = "arn:aws:states:::aws-sdk:sqs:deleteMessage"
              Parameters = {
                "QueueUrl.$"      = "States.Format('${aws_sqs_queue.gmail_forwarder_retry.url}')"
                "ReceiptHandle.$" = "$.receiptHandle"
              }
              ResultPath = "$.DeleteResult"
              Next       = "MessageProcessedSuccessfully"
            }

            # Message processed successfully
            MessageProcessedSuccessfully = {
              Type = "Succeed"
            }

            # Lambda invocation failed after retries
            InvocationFailed = {
              Type = "Pass"
              Parameters = {
                "Status"          = "Failed"
                "MessageId.$"     = "$.MessageId"
                "Error.$"         = "$.LambdaError"
                "ReceiptHandle.$" = "$.ReceiptHandle"
              }
              Next = "DeleteFailedMessage"
            }

            # Delete failed message from queue (will go to DLQ via redrive policy)
            DeleteFailedMessage = {
              Type     = "Task"
              Resource = "arn:aws:states:::aws-sdk:sqs:deleteMessage"
              Parameters = {
                "QueueUrl.$"      = "States.Format('${aws_sqs_queue.gmail_forwarder_retry.url}')"
                "ReceiptHandle.$" = "$.ReceiptHandle"
              }
              Catch = [
                {
                  ErrorEquals = ["States.ALL"]
                  Next        = "MessageProcessingComplete"
                }
              ]
              Next = "MessageProcessingComplete"
            }

            # Final state for failed message processing
            MessageProcessingComplete = {
              Type = "Succeed"
            }
          }
        }
        Next = "CheckForMoreMessages"
      }

      # Check if there might be more messages to process
      CheckForMoreMessages = {
        Type = "Choice"
        Choices = [
          {
            Variable  = "$.QueueMessages.Messages[10]"
            IsPresent = false
            Next      = "AllMessagesProcessed"
          }
        ]
        Default = "ReadMessagesFromQueue"
      }

      # All messages processed - publish completion metrics
      AllMessagesProcessed = {
        Type = "Pass"
        Parameters = {
          "Status"                = "Completed"
          "ProcessingResults.$"   = "$.ProcessingResults"
          "CompletionTimestamp.$" = "$$.State.EnteredTime"
        }
        Next = "PublishCompletionMetrics"
      }

      # Publish metrics about retry processing completion
      PublishCompletionMetrics = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:cloudwatch:putMetricData"
        Parameters = {
          Namespace = "SESMail/${var.environment}"
          MetricData = [
            {
              MetricName = "RetryProcessingCompleted"
              Value      = 1
              Unit       = "Count"
            }
          ]
        }
        ResultPath = "$.MetricsResult"
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "MetricsPublishFailed"
            ResultPath  = "$.MetricsError"
          }
        ]
        Next = "RetryProcessingComplete"
      }

      # Metrics publish failed - log and continue
      MetricsPublishFailed = {
        Type = "Pass"
        Parameters = {
          "Status"         = "MetricsPublishFailed"
          "Error.$"        = "$.MetricsError"
          "OriginalData.$" = "$"
        }
        Next = "RetryProcessingComplete"
      }

      # Final success state
      RetryProcessingComplete = {
        Type = "Succeed"
      }

      # Handle errors reading from queue
      HandleQueueReadError = {
        Type = "Pass"
        Parameters = {
          "Status"  = "QueueReadError"
          "Error.$" = "$.Error"
        }
        Next = "QueueReadFailed"
      }

      # Queue read failed - terminal state
      QueueReadFailed = {
        Type  = "Fail"
        Error = "QueueReadError"
        Cause = "Failed to read messages from retry queue"
      }
    }
  })

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

  alarm_actions = [aws_sns_topic.gmail_token_alerts.arn]
  ok_actions    = [aws_sns_topic.gmail_token_alerts.arn]

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

  alarm_actions = [aws_sns_topic.gmail_token_alerts.arn]
  ok_actions    = [aws_sns_topic.gmail_token_alerts.arn]

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

  alarm_actions = [aws_sns_topic.gmail_token_alerts.arn]
  ok_actions    = [aws_sns_topic.gmail_token_alerts.arn]

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

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

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

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter"
        ]
        Resource = aws_ssm_parameter.gmail_oauth_refresh_token.arn
      }
    ]
  })
}

# IAM policy for Step Function to publish CloudWatch metrics
resource "aws_iam_role_policy" "stepfunction_token_monitor_cloudwatch" {
  name = "cloudwatch-metrics"
  role = aws_iam_role.stepfunction_token_monitor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "SESMail/${var.environment}"
          }
        }
      }
    ]
  })
}

# IAM policy for Step Function CloudWatch Logs
resource "aws_iam_role_policy" "stepfunction_token_monitor_logs" {
  name = "cloudwatch-logs"
  role = aws_iam_role.stepfunction_token_monitor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.stepfunction_token_monitor_logs.arn}:*"
      }
    ]
  })
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

  definition = jsonencode({
    Comment       = "Monitor Gmail OAuth token expiration every 5 minutes"
    QueryLanguage = "JSONata"
    StartAt       = "GetTokenParameter"
    States = {
      # Get SSM parameter containing token metadata
      GetTokenParameter = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:ssm:getParameter"
        Arguments = {
          Name           = aws_ssm_parameter.gmail_oauth_refresh_token.name
          WithDecryption = true
        }
        Next = "CalculateExpiration"
        Catch = [
          {
            ErrorEquals = ["Ssm.ParameterNotFound"]
            Next        = "HandleMissingParameter"
          },
          {
            ErrorEquals = ["States.ALL"]
            Next        = "HandleParameterError"
          }
        ]
      }

      # Parse token JSON and calculate seconds until expiration using JSONata
      CalculateExpiration = {
        Type = "Pass"
        Output = {
          seconds_until_expiration = "{% $parse($states.input.Parameter.Value).expires_at_epoch - ($millis() / 1000) %}"
        }
        Next = "PublishExpirationMetric"
      }

      # Publish metric to CloudWatch
      PublishExpirationMetric = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:cloudwatch:putMetricData"
        Arguments = {
          Namespace = "SESMail/${var.environment}"
          MetricData = [
            {
              MetricName = "TokenSecondsUntilExpiration"
              Value      = "{% $states.input.seconds_until_expiration %}"
              Unit       = "Seconds"
            }
          ]
        }
        Next = "MonitoringComplete"
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "HandleMetricPublishError"
          }
        ]
      }

      # Success state
      MonitoringComplete = {
        Type = "Succeed"
      }

      # Error handling: Missing parameter
      HandleMissingParameter = {
        Type = "Pass"
        Output = {
          ErrorType    = "ParameterNotFound"
          ErrorMessage = "SSM parameter does not exist - run refresh_oauth_token.py"
        }
        Next = "PublishErrorMetric"
      }

      # Error handling: Parameter read error
      HandleParameterError = {
        Type = "Pass"
        Output = {
          ErrorType    = "ParameterReadError"
          ErrorMessage = "Failed to read SSM parameter"
        }
        Next = "PublishErrorMetric"
      }

      # Error handling: Metric publish error
      HandleMetricPublishError = {
        Type = "Pass"
        Output = {
          ErrorType    = "MetricPublishError"
          ErrorMessage = "Failed to publish metric to CloudWatch"
        }
        Next = "PublishErrorMetric"
      }

      # Publish error metric for monitoring system health
      PublishErrorMetric = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:cloudwatch:putMetricData"
        Arguments = {
          Namespace = "SESMail/${var.environment}"
          MetricData = [
            {
              MetricName = "TokenMonitoringErrors"
              Value      = 1
              Unit       = "Count"
            }
          ]
        }
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "MonitoringFailed"
          }
        ]
        Next = "MonitoringFailed"
      }

      # Terminal failure state
      MonitoringFailed = {
        Type  = "Fail"
        Error = "TokenMonitoringFailed"
        Cause = "Token monitoring workflow failed - check CloudWatch logs"
      }
    }
  })

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

