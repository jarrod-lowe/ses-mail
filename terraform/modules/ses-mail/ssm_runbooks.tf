# IAM assume role policy document for Systems Manager Automation
data "aws_iam_policy_document" "ssm_automation_assume_role" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["ssm.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

# IAM Role for Systems Manager Automation
resource "aws_iam_role" "ssm_automation" {
  name               = "ses-mail-ssm-automation-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.ssm_automation_assume_role.json

  tags = {
    Name        = "ses-mail-ssm-automation-${var.environment}"
    Environment = var.environment
  }
}

# IAM policy document for SSM Automation to manage SQS queues
data "aws_iam_policy_document" "ssm_automation_sqs" {
  statement {
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:SendMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ChangeMessageVisibility"
    ]
    resources = [
      aws_sqs_queue.gmail_forwarder.arn,
      aws_sqs_queue.gmail_forwarder_dlq.arn,
      aws_sqs_queue.bouncer.arn,
      aws_sqs_queue.bouncer_dlq.arn
    ]
  }
}

# IAM Policy for SSM Automation to manage SQS queues
resource "aws_iam_role_policy" "ssm_automation_sqs" {
  name   = "sqs-dlq-management"
  role   = aws_iam_role.ssm_automation.id
  policy = data.aws_iam_policy_document.ssm_automation_sqs.json
}

# IAM policy document for SSM Automation to read CloudWatch Logs
data "aws_iam_policy_document" "ssm_automation_logs" {
  statement {
    effect = "Allow"
    actions = [
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
      "logs:DescribeLogStreams"
    ]
    resources = [
      "${aws_cloudwatch_log_group.lambda_router_logs.arn}:*",
      "${aws_cloudwatch_log_group.lambda_gmail_forwarder_logs.arn}:*",
      "${aws_cloudwatch_log_group.lambda_bouncer_logs.arn}:*"
    ]
  }
}

# IAM Policy for SSM Automation to read CloudWatch Logs
resource "aws_iam_role_policy" "ssm_automation_logs" {
  name   = "cloudwatch-logs-read"
  role   = aws_iam_role.ssm_automation.id
  policy = data.aws_iam_policy_document.ssm_automation_logs.json
}

# IAM policy document for SSM Automation to access X-Ray
data "aws_iam_policy_document" "ssm_automation_xray" {
  statement {
    effect = "Allow"
    actions = [
      "xray:GetTraceSummaries",
      "xray:BatchGetTraces",
      "xray:GetServiceGraph"
    ]
    resources = ["*"]
  }
}

# IAM Policy for SSM Automation to access X-Ray
resource "aws_iam_role_policy" "ssm_automation_xray" {
  name   = "xray-read"
  role   = aws_iam_role.ssm_automation.id
  policy = data.aws_iam_policy_document.ssm_automation_xray.json
}

# IAM policy document for SSM Automation to access Lambda functions
data "aws_iam_policy_document" "ssm_automation_lambda" {
  statement {
    effect = "Allow"
    actions = [
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:ListTags"
    ]
    resources = [
      aws_lambda_function.router_enrichment.arn,
      aws_lambda_function.gmail_forwarder.arn,
      aws_lambda_function.bouncer.arn
    ]
  }
}

# IAM Policy for SSM Automation to invoke Lambda functions
resource "aws_iam_role_policy" "ssm_automation_lambda" {
  name   = "lambda-invoke"
  role   = aws_iam_role.ssm_automation.id
  policy = data.aws_iam_policy_document.ssm_automation_lambda.json
}

# SSM Automation Document: Redrive DLQ Messages
resource "aws_ssm_document" "dlq_redrive" {
  name            = "ses-mail-dlq-redrive-${var.environment}"
  document_type   = "Automation"
  document_format = "YAML"

  content = yamlencode({
    schemaVersion = "0.3"
    description   = "Redrive messages from dead letter queue back to source queue with optional velocity control"
    assumeRole    = aws_iam_role.ssm_automation.arn

    parameters = {
      DLQUrl = {
        type        = "String"
        description = "Dead letter queue URL to redrive messages from"
        allowedValues = [
          aws_sqs_queue.gmail_forwarder_dlq.url,
          aws_sqs_queue.bouncer_dlq.url
        ]
      }
      SourceQueueUrl = {
        type        = "String"
        description = "Source queue URL to send messages back to"
        allowedValues = [
          aws_sqs_queue.gmail_forwarder.url,
          aws_sqs_queue.bouncer.url
        ]
      }
      MaxMessages = {
        type        = "Integer"
        description = "Maximum number of messages to redrive (default: all)"
        default     = 0
      }
      VelocityPerSecond = {
        type        = "Integer"
        description = "Maximum messages per second to redrive (0 = unlimited)"
        default     = 10
      }
    }

    mainSteps = [
      {
        name   = "GetDLQAttributes"
        action = "aws:executeAwsApi"
        inputs = {
          Service        = "sqs"
          Api            = "GetQueueAttributes"
          QueueUrl       = "{{ DLQUrl }}"
          AttributeNames = ["ApproximateNumberOfMessages"]
        }
        outputs = [
          {
            Name     = "MessageCount"
            Selector = "$.Attributes.ApproximateNumberOfMessages"
            Type     = "String"
          }
        ]
      },
      {
        name   = "CalculateMessagesToRedrive"
        action = "aws:executeScript"
        inputs = {
          Runtime = "python3.11"
          Handler = "script_handler"
          Script  = <<-PYTHON
            def script_handler(events, context):
              dlq_count = int(events['DLQMessageCount'])
              max_messages = int(events['MaxMessages'])

              if max_messages == 0:
                messages_to_redrive = dlq_count
              else:
                messages_to_redrive = min(dlq_count, max_messages)

              return {'MessagesToRedrive': messages_to_redrive}
          PYTHON
          InputPayload = {
            DLQMessageCount = "{{ GetDLQAttributes.MessageCount }}"
            MaxMessages     = "{{ MaxMessages }}"
          }
        }
        outputs = [
          {
            Name     = "MessagesToRedrive"
            Selector = "$.Payload.MessagesToRedrive"
            Type     = "Integer"
          }
        ]
      },
      {
        name   = "RedriveMessages"
        action = "aws:executeScript"
        inputs = {
          Runtime = "python3.11"
          Handler = "script_handler"
          Script  = <<-PYTHON
            import boto3
            import time

            def script_handler(events, context):
              sqs = boto3.client('sqs')
              dlq_url = events['DLQUrl']
              source_url = events['SourceQueueUrl']
              messages_to_redrive = int(events['MessagesToRedrive'])
              velocity = int(events['VelocityPerSecond'])

              redriven = 0
              failed = 0

              while redriven < messages_to_redrive:
                # Receive up to 10 messages
                batch_size = min(10, messages_to_redrive - redriven)
                response = sqs.receive_message(
                  QueueUrl=dlq_url,
                  MaxNumberOfMessages=batch_size,
                  WaitTimeSeconds=1
                )

                messages = response.get('Messages', [])
                if not messages:
                  break

                for message in messages:
                  try:
                    # Send to source queue
                    sqs.send_message(
                      QueueUrl=source_url,
                      MessageBody=message['Body'],
                      MessageAttributes=message.get('MessageAttributes', {})
                    )

                    # Delete from DLQ
                    sqs.delete_message(
                      QueueUrl=dlq_url,
                      ReceiptHandle=message['ReceiptHandle']
                    )

                    redriven += 1

                    # Velocity control
                    if velocity > 0:
                      time.sleep(1.0 / velocity)
                  except Exception as e:
                    failed += 1
                    print(f"Failed to redrive message: {str(e)}")

              return {
                'RedrivenCount': redriven,
                'FailedCount': failed
              }
          PYTHON
          InputPayload = {
            DLQUrl            = "{{ DLQUrl }}"
            SourceQueueUrl    = "{{ SourceQueueUrl }}"
            MessagesToRedrive = "{{ CalculateMessagesToRedrive.MessagesToRedrive }}"
            VelocityPerSecond = "{{ VelocityPerSecond }}"
          }
        }
        outputs = [
          {
            Name     = "RedrivenCount"
            Selector = "$.Payload.RedrivenCount"
            Type     = "Integer"
          },
          {
            Name     = "FailedCount"
            Selector = "$.Payload.FailedCount"
            Type     = "Integer"
          }
        ]
      }
    ]

    outputs = [
      "RedriveMessages.RedrivenCount",
      "RedriveMessages.FailedCount"
    ]
  })

  tags = {
    Name        = "ses-mail-dlq-redrive-${var.environment}"
    Environment = var.environment
  }
}

# SSM Automation Document: Queue Health Check
resource "aws_ssm_document" "queue_health_check" {
  name            = "ses-mail-queue-health-check-${var.environment}"
  document_type   = "Automation"
  document_format = "YAML"

  content = yamlencode({
    schemaVersion = "0.3"
    description   = "Check health of all SES mail processing queues including depths, ages, and DLQ status"
    assumeRole    = aws_iam_role.ssm_automation.arn

    parameters = {}

    mainSteps = [
      {
        name   = "CheckGmailQueue"
        action = "aws:executeAwsApi"
        inputs = {
          Service  = "sqs"
          Api      = "GetQueueAttributes"
          QueueUrl = aws_sqs_queue.gmail_forwarder.url
          AttributeNames = [
            "ApproximateNumberOfMessages",
            "ApproximateAgeOfOldestMessage"
          ]
        }
        outputs = [
          {
            Name     = "Messages"
            Selector = "$.Attributes.ApproximateNumberOfMessages"
            Type     = "String"
          },
          {
            Name     = "OldestAge"
            Selector = "$.Attributes.ApproximateAgeOfOldestMessage"
            Type     = "String"
          }
        ]
      },
      {
        name   = "CheckGmailDLQ"
        action = "aws:executeAwsApi"
        inputs = {
          Service        = "sqs"
          Api            = "GetQueueAttributes"
          QueueUrl       = aws_sqs_queue.gmail_forwarder_dlq.url
          AttributeNames = ["ApproximateNumberOfMessages"]
        }
        outputs = [
          {
            Name     = "DLQMessages"
            Selector = "$.Attributes.ApproximateNumberOfMessages"
            Type     = "String"
          }
        ]
      },
      {
        name   = "CheckBouncerQueue"
        action = "aws:executeAwsApi"
        inputs = {
          Service  = "sqs"
          Api      = "GetQueueAttributes"
          QueueUrl = aws_sqs_queue.bouncer.url
          AttributeNames = [
            "ApproximateNumberOfMessages",
            "ApproximateAgeOfOldestMessage"
          ]
        }
        outputs = [
          {
            Name     = "Messages"
            Selector = "$.Attributes.ApproximateNumberOfMessages"
            Type     = "String"
          },
          {
            Name     = "OldestAge"
            Selector = "$.Attributes.ApproximateAgeOfOldestMessage"
            Type     = "String"
          }
        ]
      },
      {
        name   = "CheckBouncerDLQ"
        action = "aws:executeAwsApi"
        inputs = {
          Service        = "sqs"
          Api            = "GetQueueAttributes"
          QueueUrl       = aws_sqs_queue.bouncer_dlq.url
          AttributeNames = ["ApproximateNumberOfMessages"]
        }
        outputs = [
          {
            Name     = "DLQMessages"
            Selector = "$.Attributes.ApproximateNumberOfMessages"
            Type     = "String"
          }
        ]
      },
      {
        name   = "GenerateHealthReport"
        action = "aws:executeScript"
        inputs = {
          Runtime = "python3.11"
          Handler = "script_handler"
          Script  = <<-PYTHON
            def script_handler(events, context):
              report = {
                'GmailQueue': {
                  'Messages': events['GmailMessages'],
                  'OldestAge': events['GmailOldestAge'],
                  'DLQMessages': events['GmailDLQMessages']
                },
                'BouncerQueue': {
                  'Messages': events['BouncerMessages'],
                  'OldestAge': events['BouncerOldestAge'],
                  'DLQMessages': events['BouncerDLQMessages']
                }
              }

              # Check for issues
              issues = []
              if int(events['GmailDLQMessages']) > 0:
                issues.append('Gmail DLQ has messages')
              if int(events['BouncerDLQMessages']) > 0:
                issues.append('Bouncer DLQ has messages')
              if int(events['GmailOldestAge']) > 300:
                issues.append('Gmail queue has old messages (>5min)')
              if int(events['BouncerOldestAge']) > 300:
                issues.append('Bouncer queue has old messages (>5min)')

              report['Issues'] = issues
              report['HealthStatus'] = 'HEALTHY' if not issues else 'UNHEALTHY'

              return report
          PYTHON
          InputPayload = {
            GmailMessages      = "{{ CheckGmailQueue.Messages }}"
            GmailOldestAge     = "{{ CheckGmailQueue.OldestAge }}"
            GmailDLQMessages   = "{{ CheckGmailDLQ.DLQMessages }}"
            BouncerMessages    = "{{ CheckBouncerQueue.Messages }}"
            BouncerOldestAge   = "{{ CheckBouncerQueue.OldestAge }}"
            BouncerDLQMessages = "{{ CheckBouncerDLQ.DLQMessages }}"
          }
        }
        outputs = [
          {
            Name     = "HealthReport"
            Selector = "$.Payload"
            Type     = "StringMap"
          }
        ]
      }
    ]

    outputs = [
      "GenerateHealthReport.HealthReport"
    ]
  })

  tags = {
    Name        = "ses-mail-queue-health-check-${var.environment}"
    Environment = var.environment
  }
}
