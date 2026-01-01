# Monitoring and Troubleshooting Guide

This guide covers monitoring, troubleshooting, and incident response for the SES Mail system.

## Table of Contents

- [CloudWatch Dashboard](#cloudwatch-dashboard)
- [Viewing Logs](#viewing-logs)
- [CloudWatch Logs Insights Queries](#cloudwatch-logs-insights-queries)
- [X-Ray Distributed Tracing](#x-ray-distributed-tracing)
- [Checking Queue Depth](#checking-queue-depth)
- [Common Issues and Solutions](#common-issues-and-solutions)
- [Common Failure Scenarios](#common-failure-scenarios)
- [Incident Response Procedures](#incident-response-procedures)
- [CloudWatch Logs Insights Saved Queries](#cloudwatch-logs-insights-saved-queries)
- [Alarm Response Times](#alarm-response-times)

## CloudWatch Dashboard

Access the pre-configured dashboard:

```bash
# Get dashboard URL
cd terraform/environments/test
terraform output dashboard_url

# Or navigate in AWS Console:
# CloudWatch → Dashboards → ses-mail-dashboard-test
```

**Dashboard includes:**

- Email processing metrics (accepted, spam, virus)
- Lambda execution metrics (invocations, errors, duration)
- Queue metrics (messages, age, DLQ depth)
- Token expiration countdown
- SMTP credential operations

## Viewing Logs

```bash
# Router enrichment logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow

# Gmail forwarder logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-gmail-forwarder-test --follow

# Bouncer logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-bouncer-test --follow

# Credential manager logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-credential-manager-test --follow

# EventBridge Pipes logs
AWS_PROFILE=ses-mail aws logs tail /aws/pipes/ses-email-router-test --follow
```

## CloudWatch Logs Insights Queries

Use CloudWatch Logs Insights for advanced log analysis:

```bash
# Navigate to: CloudWatch → Logs → Logs Insights
# Select log group and use saved queries or custom queries
```

**Example query - Failed email processing:**

```
fields @timestamp, @message
| filter level = "ERROR"
| sort @timestamp desc
| limit 20
```

**Example query - Gmail forwarding successes:**

```
fields @timestamp, messageId, destination
| filter message = "Successfully imported message to Gmail"
| sort @timestamp desc
```

## X-Ray Distributed Tracing

View end-to-end traces for email processing:

```bash
# Navigate to: AWS Console → X-Ray → Traces
# Filter by service: ses-mail-router-enrichment-test

# Or view service map:
# X-Ray → Service map → Select time range
```

**Trace shows:**

- SNS → SQS → EventBridge Pipes → Router Lambda → Event Bus → Handler Queue → Gmail Forwarder
- Timing breakdown for each component
- Any errors or exceptions

## Checking Queue Depth

```bash
# Get queue URL from Terraform
cd terraform/environments/test
terraform output -json | jq -r '.sqs_queues.value'

# Check specific queue depth
AWS_PROFILE=ses-mail aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-gmail-forwarder-test \
  --attribute-names ApproximateNumberOfMessages,ApproximateNumberOfMessagesNotVisible

# Check DLQ depth
AWS_PROFILE=ses-mail aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-gmail-forwarder-dlq-test \
  --attribute-names ApproximateNumberOfMessages
```

## Common Issues and Solutions

### Email not forwarded to Gmail

**Symptoms:** Email received by SES but doesn't appear in Gmail

**Debugging steps:**

1. **Check routing rule exists:**
   ```bash
   AWS_PROFILE=ses-mail aws dynamodb get-item \
     --table-name ses-mail-email-routing-test \
     --key '{"PK": {"S": "ROUTE#your-email@example.com"}, "SK": {"S": "RULE#v1"}}'
   ```

2. **Check router logs for routing decision:**
   ```bash
   AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow
   ```

3. **Check Gmail forwarder logs for errors:**
   ```bash
   AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-gmail-forwarder-test --follow
   ```

4. **Check for OAuth token expiration:**
   ```bash
   AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test
   ```

5. **Check queue depth (messages may be stuck):**
   ```bash
   AWS_PROFILE=ses-mail aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names All
   ```

### Lambda function errors

**Symptoms:** CloudWatch alarms firing for Lambda errors

**Debugging:**

1. **Check Lambda logs:**
   ```bash
   AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-{function}-test --follow
   ```

2. **Check Lambda metrics:**
   ```bash
   AWS_PROFILE=ses-mail aws cloudwatch get-metric-statistics \
     --namespace "AWS/Lambda" \
     --metric-name Errors \
     --dimensions Name=FunctionName,Value=ses-mail-router-enrichment-test \
     --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 300 \
     --statistics Sum
   ```

3. **Check X-Ray traces for detailed error:**
   Navigate to X-Ray console and filter by error status

### Messages in DLQ

**Symptoms:** Dead letter queue has messages

**Investigation:**

1. **Check DLQ depth:**
   ```bash
   AWS_PROFILE=ses-mail aws sqs get-queue-attributes \
     --queue-url <dlq-url> \
     --attribute-names ApproximateNumberOfMessages
   ```

2. **Receive and inspect message:**
   ```bash
   AWS_PROFILE=ses-mail aws sqs receive-message \
     --queue-url <dlq-url> \
     --max-number-of-messages 1 \
     --attribute-names All \
     --message-attribute-names All
   ```

3. **Check for common issues:**
   - OAuth token expired
   - Gmail API rate limiting
   - Malformed message
   - DynamoDB unavailable

4. **Process DLQ messages:**
   See [RECOVERY.md](RECOVERY.md) for DLQ management procedures

## Common Failure Scenarios

### Gmail API Token Expired

**Symptoms:**
- CloudWatch alarm: `ses-mail-lambda-gmail-forwarder-errors-{environment}`
- DLQ: `ses-mail-gmail-forwarder-dlq-{environment}` has messages
- Logs show: "Token expired" or "invalid_grant"

**Resolution:**
1. Refresh Gmail OAuth token (see [OPERATIONS.md#oauth-token-management](OPERATIONS.md#oauth-token-management))
   ```bash
   AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test
   ```
2. Wait 2-3 minutes for parameter to propagate
3. Use DLQ redrive runbook to reprocess failed messages (see [RECOVERY.md](RECOVERY.md))

### Router Enrichment DynamoDB Errors

**Symptoms:**
- CloudWatch alarm: `ses-mail-lambda-router-errors-{environment}`
- DLQ: `ses-mail-email-input-dlq-{environment}` has messages
- Logs show: "DynamoDB timeout" or "ProvisionedThroughputExceededException"

**Resolution:**
1. Check DynamoDB table status:
   ```bash
   AWS_PROFILE=ses-mail aws dynamodb describe-table \
     --table-name ses-mail-email-routing-test
   ```
2. If table is throttling, wait for capacity to recover (PAY_PER_REQUEST should auto-scale)
3. If table doesn't exist, redeploy infrastructure
4. Use DLQ redrive runbook to reprocess messages

### Bouncer SES Sending Failures

**Symptoms:**
- CloudWatch alarm: `ses-mail-lambda-bouncer-errors-{environment}`
- DLQ: `ses-mail-bouncer-dlq-{environment}` has messages
- Logs show: "MessageRejected" or "Account in sandbox mode"

**Resolution:**
1. Check if SES is in sandbox mode (requires verified recipients):
   ```bash
   AWS_PROFILE=ses-mail aws ses get-account-sending-enabled
   ```
2. For production, request SES sending limit increase
3. For sandbox issues, verify sender email address
4. Review bounce message content for compliance with SES policies
5. Use DLQ redrive runbook after fixing

### EventBridge Pipes Failures

**Symptoms:**
- CloudWatch alarm: `eventbridge-pipes-failures-{environment}`
- DLQ: `ses-mail-email-input-dlq-{environment}` has messages
- EventBridge Pipes logs show enrichment errors

**Resolution:**
1. Check EventBridge Pipes status:
   ```bash
   AWS_PROFILE=ses-mail aws pipes list-pipes --region ap-southeast-2
   ```
2. Check pipe logs:
   ```bash
   AWS_PROFILE=ses-mail aws logs tail \
     /aws/vendedlogs/pipes/test/ses-email-router --follow
   ```
3. If router lambda is failing, check router lambda logs
4. If pipes is stopped, restart it via console or CLI
5. Use DLQ redrive runbook after resolution

### High Queue Age Alarms

**Symptoms:**
- CloudWatch alarm: `ses-{queue}-queue-age-{environment}`
- Messages sitting in queue for >5 minutes
- Dashboard shows increasing queue depth

**Resolution:**
1. Check if downstream lambda is throttled:
   ```bash
   # Check lambda concurrent executions
   AWS_PROFILE=ses-mail aws cloudwatch get-metric-statistics \
     --namespace AWS/Lambda \
     --metric-name ConcurrentExecutions \
     --dimensions Name=FunctionName,Value=ses-mail-gmail-forwarder-test \
     --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S) \
     --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
     --period 300 \
     --statistics Maximum
   ```
2. Check if lambda is experiencing errors (see lambda error scenarios above)
3. If lambda is healthy but slow, consider increasing concurrency limit
4. Monitor queue depth - should decrease as lambda catches up

## Incident Response Procedures

### P1: Email Processing Completely Down

**Definition:** No emails being processed for >15 minutes, multiple alarms firing

**Response:**
1. Run queue health check automation:
   ```bash
   AWS_PROFILE=ses-mail aws ssm start-automation-execution \
     --document-name "SESMail-Queue-HealthCheck-test"
   ```
2. Check CloudWatch dashboard for system overview
3. Review X-Ray service map for failed components
4. Check AWS Service Health Dashboard for regional issues
5. If infrastructure issue, consider rollback to previous Terraform version
6. Update status page and notify stakeholders

### P2: Partial Email Processing Failure

**Definition:** Some emails failing (DLQ has messages), but system mostly working

**Response:**
1. Identify which queue/lambda is affected from alarms
2. Follow specific failure scenario procedures above
3. Check if issue is isolated to specific email addresses or routing rules
4. Review recent deployments or configuration changes
5. If systematic issue, prepare fix and deploy
6. If transient issue, use DLQ redrive after verification

### P3: Performance Degradation

**Definition:** Emails processing slowly (high queue age) but eventually succeeding

**Response:**
1. Check CloudWatch dashboard "Lambda Duration" widget
2. Review X-Ray traces for slow operations
3. Check for AWS API throttling in lambda logs
4. Monitor queue depths - if decreasing, let system catch up
5. If persistent, investigate resource limits or external dependencies
6. Consider scaling adjustments if needed

## CloudWatch Logs Insights Saved Queries

Navigate to CloudWatch Logs Insights and select from saved queries:

1. **ses-mail/{environment}/router-enrichment-errors** - Router lambda errors with message details
2. **ses-mail/{environment}/gmail-forwarder-failures** - Gmail forwarding failures
3. **ses-mail/{environment}/bouncer-failures** - Bounce sending failures
4. **ses-mail/{environment}/routing-decision-analysis** - Routing decision statistics by action and rule
5. **ses-mail/{environment}/email-end-to-end-trace** - Trace email through entire pipeline
6. **ses-mail/{environment}/dlq-message-investigation** - Find failed messages and retry attempts
7. **ses-mail/{environment}/performance-analysis** - Lambda execution time statistics

## Alarm Response Times

- **DLQ Alarms**: Respond within 1 hour, investigate immediately
- **Lambda Error Alarms**: Respond within 30 minutes
- **Queue Age Alarms**: Respond within 2 hours, monitor for auto-recovery
- **High Volume Alarms**: Informational, verify expected traffic

## Further Reading

- [OPERATIONS.md](OPERATIONS.md) - Core operations (routing, OAuth, SMTP)
- [RECOVERY.md](RECOVERY.md) - DLQ management, retry workflows, and runbooks
- [ARCHITECTURE.md](ARCHITECTURE.md) - Technical deep-dive into system design
