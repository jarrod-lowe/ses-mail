# Recovery and Retry Guide

This guide covers retry mechanisms, dead letter queue (DLQ) management, and automated recovery procedures for the SES Mail system.

## Table of Contents

- [Dead Letter Queue (DLQ) Management](#dead-letter-queue-dlq-management)
- [Retry Queue Infrastructure](#retry-queue-infrastructure)
- [Automatic Retry Queueing](#automatic-retry-queueing)
- [Manual Retry Processing](#manual-retry-processing)
- [Monitoring Retry Processing](#monitoring-retry-processing)
- [Checking Retry Queue Status](#checking-retry-queue-status)
- [Purging Retry Queues](#purging-retry-queues)
- [Systems Manager Automation Runbooks](#systems-manager-automation-runbooks)

## Dead Letter Queue (DLQ) Management

### Overview

The system has three dead letter queues (DLQs) that receive messages after failed processing attempts:

- `ses-mail-email-input-dlq-{environment}` - Messages that failed EventBridge Pipes enrichment
- `ses-mail-gmail-forwarder-dlq-{environment}` - Messages that failed Gmail forwarding
- `ses-mail-bouncer-dlq-{environment}` - Messages that failed bounce sending

### When to Investigate DLQ Messages

**Automated Redrive (Safe)**:

- Single message in DLQ after transient error (network timeout, rate limiting)
- Multiple messages with same timestamp (likely AWS service issue)
- Gmail API token expiration (check logs for "token expired" errors)

**Manual Investigation Required**:

- Messages repeatedly appearing in DLQ after redrive
- DLQ alarms triggering multiple times for same message
- Error messages indicating data corruption or malformed payloads
- Security-related failures (invalid credentials, permission denied)

### Investigating DLQ Messages

#### Step 1: Check CloudWatch Logs

Use the saved CloudWatch Logs Insights query "dlq-message-investigation":

```bash
# Navigate to CloudWatch Logs Insights
# Select saved query: ses-mail/{environment}/dlq-message-investigation
# Set time range to cover when alarm triggered
# Run query
```

Look for:

- Error messages with messageId matching DLQ messages
- Stack traces indicating code bugs
- AWS API errors (throttling, permissions, service issues)

#### Step 2: Inspect DLQ Message Content

```bash
# Receive a message from the DLQ (without deleting)
AWS_PROFILE=ses-mail aws sqs receive-message \
  --queue-url https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-mail-gmail-forwarder-dlq-test \
  --max-number-of-messages 1 \
  --visibility-timeout 300

# Save the message body to file for analysis
# Extract messageId, recipient, and routing decision
# Check if email still exists in S3 bucket
```

#### Step 3: Check X-Ray Traces

```bash
# Go to X-Ray Console → Traces
# Filter by time range when message failed
# Search for trace containing messageId
# Review trace timeline for bottlenecks or errors
```

#### Step 4: Decide on Remediation

**Redrive to Source Queue** (automated):

- Transient errors (network, throttling, temporary service issues)
- Token refresh issues (after updating Gmail token)
- One-time AWS service disruptions

**Fix Code and Redeploy**:

- Application bugs (null pointer, data validation errors)
- Missing error handling for edge cases
- Incorrect business logic

**Manual Processing**:

- Data corruption in S3 email file
- Permanent external service failure
- Invalid routing rules in DynamoDB

### Automated DLQ Redrive

See [Systems Manager Automation Runbooks](#systems-manager-automation-runbooks) section for detailed instructions on using the SESMail-DLQ-Redrive runbook.

## Retry Queue Infrastructure

The system includes dedicated retry queues for handling Gmail token expiration failures:

**Retry Queue:** `ses-mail-gmail-forwarder-retry-{environment}`

- Visibility timeout: 15 minutes
- Message retention: 14 days
- Max receive count: 3 (then moves to retry DLQ)

**Retry DLQ:** `ses-mail-gmail-forwarder-retry-dlq-{environment}`

- Message retention: 14 days

## Automatic Retry Queueing

The Gmail forwarder Lambda automatically detects OAuth token expiration and queues failed messages for retry:

**Error detection:**

- `RefreshError` from Google Auth library
- HTTP 401/403 from Gmail API
- Error messages containing: `invalid_grant`, `token has been expired`, `token expired`, `invalid credentials`, `credentials have expired`, `unauthorized`, `authentication failed`

**Automatic queueing:**

1. Message is queued to retry queue with metadata
2. Original message removed from processing queue
3. Event logged in CloudWatch

## Manual Retry Processing

After refreshing the OAuth token, manually trigger retry processing:

```bash
# Start Step Function to process retry queue
AWS_PROFILE=ses-mail aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:ap-southeast-2:{account-id}:stateMachine:ses-mail-gmail-forwarder-retry-processor-test \
  --input '{}'
```

**What happens:**

1. Step Function reads messages from retry queue (batches of 10)
2. Invokes Gmail Forwarder Lambda with original SES events
3. Implements exponential backoff (30s, 60s, 120s intervals)
4. Deletes successfully processed messages
5. Moves permanently failed messages to DLQ
6. Continues until retry queue is empty

## Monitoring Retry Processing

```bash
# List recent Step Function executions
AWS_PROFILE=ses-mail aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:ap-southeast-2:{account-id}:stateMachine:ses-mail-gmail-forwarder-retry-processor-test \
  --max-results 5

# Get execution details
AWS_PROFILE=ses-mail aws stepfunctions describe-execution \
  --execution-arn <execution-arn>

# Get execution history
AWS_PROFILE=ses-mail aws stepfunctions get-execution-history \
  --execution-arn <execution-arn>
```

## Checking Retry Queue Status

```bash
# Check retry queue depth
AWS_PROFILE=ses-mail aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-mail-gmail-forwarder-retry-test \
  --attribute-names ApproximateNumberOfMessages,ApproximateAgeOfOldestMessage

# Check retry DLQ depth
AWS_PROFILE=ses-mail aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-mail-gmail-forwarder-retry-dlq-test \
  --attribute-names ApproximateNumberOfMessages
```

## Purging Retry Queues

To clear retry queues after resolving issues:

```bash
# Purge retry queue (caution: deletes all messages)
AWS_PROFILE=ses-mail aws sqs purge-queue \
  --queue-url https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-mail-gmail-forwarder-retry-test

# Purge retry DLQ
AWS_PROFILE=ses-mail aws sqs purge-queue \
  --queue-url https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-mail-gmail-forwarder-retry-dlq-test
```

## Systems Manager Automation Runbooks

The system provides automation runbooks for common operational tasks.

### Available Runbooks

#### SESMail-DLQ-Redrive-{environment}

**Purpose:** Redrive messages from DLQ back to source queue with velocity control

**When to Use:**

- After fixing transient errors (token refresh, service restoration)
- After deploying code fixes for application bugs
- When DLQ messages are verified safe to reprocess

**Parameters:**

- `DLQUrl`: Dead letter queue URL to redrive from
- `SourceQueueUrl`: Source queue URL to send messages back to
- `MaxMessages`: Maximum number of messages to redrive (0 = all)
- `VelocityPerSecond`: Rate limiting (messages per second)

**Outputs:**

- `RedrivenCount`: Number of messages successfully redriven
- `FailedCount`: Number of messages that failed during redrive

#### SESMail-Queue-HealthCheck-{environment}

**Purpose:** Check health of all queues and DLQs

**When to Use:**

- During incident response to quickly assess system state
- As part of regular health monitoring
- Before and after maintenance windows

**Outputs:**

- `HealthReport`: JSON with queue depths, ages, DLQ counts, and detected issues
- `HealthStatus`: HEALTHY or UNHEALTHY

### Running Runbooks via Console

```bash
# Navigate to: AWS Console → Systems Manager → Automation
# Click "Execute automation"
# Select document: SESMail-DLQ-Redrive-{environment}
# Configure parameters and execute
```

### Running Runbooks via CLI

```bash
# Start DLQ redrive execution
AWS_PROFILE=ses-mail aws ssm start-automation-execution \
  --document-name "SESMail-DLQ-Redrive-test" \
  --parameters \
    "DLQUrl=https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-mail-gmail-forwarder-dlq-test,\
     SourceQueueUrl=https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-mail-gmail-forwarder-test,\
     MaxMessages=0,\
     VelocityPerSecond=10"

# Start queue health check
EXECUTION_ID=$(AWS_PROFILE=ses-mail aws ssm start-automation-execution \
  --document-name "SESMail-Queue-HealthCheck-test" \
  --query 'AutomationExecutionId' \
  --output text)

# Check execution status
AWS_PROFILE=ses-mail aws ssm get-automation-execution \
  --automation-execution-id "$EXECUTION_ID"

# View outputs
AWS_PROFILE=ses-mail aws ssm get-automation-execution \
  --automation-execution-id "$EXECUTION_ID" \
  --query 'AutomationExecution.Outputs'

# List recent executions
AWS_PROFILE=ses-mail aws ssm describe-automation-executions \
  --filters "Key=DocumentName,Values=SESMail-DLQ-Redrive-test"
```

**Important:** Only redrive messages after:

1. Investigating the root cause
2. Confirming the issue is resolved (code deployed, token refreshed, service restored)
3. Verifying messages won't immediately fail again

## Further Reading

- [OPERATIONS.md](OPERATIONS.md) - Core operations (routing, OAuth, SMTP)
- [MONITORING.md](MONITORING.md) - Monitoring, logs, and troubleshooting
- [ARCHITECTURE.md](ARCHITECTURE.md) - Technical deep-dive into system design
