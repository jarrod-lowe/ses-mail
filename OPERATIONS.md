# SES Mail Operations Runbook

This document provides operational procedures for managing and troubleshooting the SES Mail system.

## Table of Contents

- [Dead Letter Queue (DLQ) Management](#dead-letter-queue-dlq-management)
- [Common Failure Scenarios](#common-failure-scenarios)
- [Incident Response Procedures](#incident-response-procedures)
- [Systems Manager Automation Runbooks](#systems-manager-automation-runbooks)
- [Monitoring and Alerting](#monitoring-and-alerting)
- [Managing Routing Rules](#managing-routing-rules)
- [Escalation Paths](#escalation-paths)

## Dead Letter Queue (DLQ) Management

### Overview

The system has three dead letter queues (DLQs) that receive messages after failed processing attempts:

- `ses-email-input-dlq-{environment}` - Messages that failed EventBridge Pipes enrichment
- `ses-gmail-forwarder-dlq-{environment}` - Messages that failed Gmail forwarding
- `ses-bouncer-dlq-{environment}` - Messages that failed bounce sending

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
  --queue-url https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-gmail-forwarder-dlq-test \
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

Use the Systems Manager automation runbook to redrive messages:

```bash
# Via AWS Console:
# 1. Navigate to Systems Manager → Automation
# 2. Click "Execute automation"
# 3. Select document: SESMail-DLQ-Redrive-{environment}
# 4. Configure parameters:
#    - DLQUrl: Select the DLQ to redrive from
#    - SourceQueueUrl: Select corresponding source queue
#    - MaxMessages: 0 (all messages) or specific count
#    - VelocityPerSecond: 10 (default) or adjust for rate limiting

# Via AWS CLI:
AWS_PROFILE=ses-mail aws ssm start-automation-execution \
  --document-name "SESMail-DLQ-Redrive-test" \
  --parameters \
    "DLQUrl=https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-gmail-forwarder-dlq-test,\
     SourceQueueUrl=https://sqs.ap-southeast-2.amazonaws.com/{account-id}/ses-gmail-forwarder-test,\
     MaxMessages=0,\
     VelocityPerSecond=10"

# Check execution status:
AWS_PROFILE=ses-mail aws ssm describe-automation-executions \
  --filters "Key=DocumentName,Values=SESMail-DLQ-Redrive-test"
```

**Important**: Only redrive messages after:

1. Investigating the root cause
2. Confirming the issue is resolved (code deployed, token refreshed, service restored)
3. Verifying messages won't immediately fail again

## Common Failure Scenarios

### Gmail API Token Expired

**Symptoms**:

- CloudWatch alarm: `ses-mail-lambda-gmail-forwarder-errors-{environment}`
- DLQ: `ses-gmail-forwarder-dlq-{environment}` has messages
- Logs show: "Token expired" or "invalid_grant"

**Resolution**:

1. Generate new Gmail OAuth token locally (see README.md)
2. Update SSM parameter:

   ```bash
   AWS_PROFILE=ses-mail aws ssm put-parameter \
     --name "/ses-mail/test/gmail-token" \
     --value "$(cat token.json)" \
     --type SecureString \
     --overwrite
   ```

3. Wait 2-3 minutes for parameter to propagate
4. Use DLQ redrive runbook to reprocess failed messages

### Router Enrichment DynamoDB Errors

**Symptoms**:

- CloudWatch alarm: `ses-mail-lambda-router-errors-{environment}`
- DLQ: `ses-email-input-dlq-{environment}` has messages
- Logs show: "DynamoDB timeout" or "ProvisionedThroughputExceededException"

**Resolution**:

1. Check DynamoDB table status:

   ```bash
   AWS_PROFILE=ses-mail aws dynamodb describe-table \
     --table-name ses-email-routing-test
   ```

2. If table is throttling, wait for capacity to recover (PAY_PER_REQUEST should auto-scale)
3. If table doesn't exist, redeploy infrastructure
4. Use DLQ redrive runbook to reprocess messages

### Bouncer SES Sending Failures

**Symptoms**:

- CloudWatch alarm: `ses-mail-lambda-bouncer-errors-{environment}`
- DLQ: `ses-bouncer-dlq-{environment}` has messages
- Logs show: "MessageRejected" or "Account in sandbox mode"

**Resolution**:

1. Check if SES is in sandbox mode (requires verified recipients):

   ```bash
   AWS_PROFILE=ses-mail aws ses get-account-sending-enabled
   ```

2. For production, request SES sending limit increase
3. For sandbox issues, verify sender email address
4. Review bounce message content for compliance with SES policies
5. Use DLQ redrive runbook after fixing

### EventBridge Pipes Failures

**Symptoms**:

- CloudWatch alarm: `eventbridge-pipes-failures-{environment}`
- DLQ: `ses-email-input-dlq-{environment}` has messages
- EventBridge Pipes logs show enrichment errors

**Resolution**:

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

**Symptoms**:

- CloudWatch alarm: `ses-{queue}-queue-age-{environment}`
- Messages sitting in queue for >5 minutes
- Dashboard shows increasing queue depth

**Resolution**:

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

**Definition**: No emails being processed for >15 minutes, multiple alarms firing

**Response**:

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

**Definition**: Some emails failing (DLQ has messages), but system mostly working

**Response**:

1. Identify which queue/lambda is affected from alarms
2. Follow specific failure scenario procedures above
3. Check if issue is isolated to specific email addresses or routing rules
4. Review recent deployments or configuration changes
5. If systematic issue, prepare fix and deploy
6. If transient issue, use DLQ redrive after verification

### P3: Performance Degradation

**Definition**: Emails processing slowly (high queue age) but eventually succeeding

**Response**:

1. Check CloudWatch dashboard "Lambda Duration" widget
2. Review X-Ray traces for slow operations
3. Check for AWS API throttling in lambda logs
4. Monitor queue depths - if decreasing, let system catch up
5. If persistent, investigate resource limits or external dependencies
6. Consider scaling adjustments if needed

## Systems Manager Automation Runbooks

### Available Runbooks

#### SESMail-DLQ-Redrive-{environment}

**Purpose**: Redrive messages from DLQ back to source queue with velocity control

**When to Use**:

- After fixing transient errors (token refresh, service restoration)
- After deploying code fixes for application bugs
- When DLQ messages are verified safe to reprocess

**Parameters**:

- `DLQUrl`: Dead letter queue URL to redrive from
- `SourceQueueUrl`: Source queue URL to send messages back to
- `MaxMessages`: Maximum number of messages to redrive (0 = all)
- `VelocityPerSecond`: Rate limiting (messages per second)

**Outputs**:

- `RedrivenCount`: Number of messages successfully redriven
- `FailedCount`: Number of messages that failed during redrive

#### SESMail-Queue-HealthCheck-{environment}

**Purpose**: Check health of all queues and DLQs

**When to Use**:

- During incident response to quickly assess system state
- As part of regular health monitoring
- Before and after maintenance windows

**Outputs**:

- `HealthReport`: JSON with queue depths, ages, DLQ counts, and detected issues
- `HealthStatus`: HEALTHY or UNHEALTHY

### Running Runbooks via CLI

```bash
# Start execution
EXECUTION_ID=$(AWS_PROFILE=ses-mail aws ssm start-automation-execution \
  --document-name "SESMail-Queue-HealthCheck-test" \
  --query 'AutomationExecutionId' \
  --output text)

# Check status
AWS_PROFILE=ses-mail aws ssm get-automation-execution \
  --automation-execution-id "$EXECUTION_ID"

# View outputs
AWS_PROFILE=ses-mail aws ssm get-automation-execution \
  --automation-execution-id "$EXECUTION_ID" \
  --query 'AutomationExecution.Outputs'
```

## Monitoring and Alerting

### CloudWatch Dashboard

Access the dashboard:

- Console: CloudWatch → Dashboards → `ses-mail-dashboard-{environment}`
- URL: `https://console.aws.amazon.com/cloudwatch/home?region=ap-southeast-2#dashboards:name=ses-mail-dashboard-{environment}`

**Key Widgets**:

1. Email Processing Overview - Monitor email volume and spam/virus detection
2. Handler Success/Failure Rates - Track routing, forwarding, and bounce operations
3. Lambda Function Errors - Identify which lambda is failing
4. Queue Depths - Monitor message backlog
5. DLQ Message Counts - Should always be 0
6. Lambda Duration - Identify performance issues

### CloudWatch Logs Insights Saved Queries

Navigate to CloudWatch Logs Insights and select from saved queries:

1. **ses-mail/{environment}/router-enrichment-errors** - Router lambda errors with message details
2. **ses-mail/{environment}/gmail-forwarder-failures** - Gmail forwarding failures
3. **ses-mail/{environment}/bouncer-failures** - Bounce sending failures
4. **ses-mail/{environment}/routing-decision-analysis** - Routing decision statistics by action and rule
5. **ses-mail/{environment}/email-end-to-end-trace** - Trace email through entire pipeline
6. **ses-mail/{environment}/dlq-message-investigation** - Find failed messages and retry attempts
7. **ses-mail/{environment}/performance-analysis** - Lambda execution time statistics

### Alarm Response Times

- **DLQ Alarms**: Respond within 1 hour, investigate immediately
- **Lambda Error Alarms**: Respond within 30 minutes
- **Queue Age Alarms**: Respond within 2 hours, monitor for auto-recovery
- **High Volume Alarms**: Informational, verify expected traffic

## Managing Routing Rules

### Adding a New Routing Rule

```bash
# Example: Forward support@example.com to Gmail
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#support@example.com"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "support@example.com"},
    "action": {"S": "forward-to-gmail"},
    "target": {"S": "your-email@gmail.com"},
    "enabled": {"BOOL": true},
    "created_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "updated_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "description": {"S": "Forward support emails to Gmail"}
  }'
```

### Updating an Existing Routing Rule

```bash
# Update the target email address
AWS_PROFILE=ses-mail aws dynamodb update-item \
  --table-name ses-email-routing-test \
  --key '{"PK": {"S": "ROUTE#support@example.com"}, "SK": {"S": "RULE#v1"}}' \
  --update-expression "SET target = :target, updated_at = :updated" \
  --expression-attribute-values '{
    ":target": {"S": "new-email@gmail.com"},
    ":updated": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}
  }'
```

### Disabling a Routing Rule

```bash
# Disable without deleting
AWS_PROFILE=ses-mail aws dynamodb update-item \
  --table-name ses-email-routing-test \
  --key '{"PK": {"S": "ROUTE#support@example.com"}, "SK": {"S": "RULE#v1"}}' \
  --update-expression "SET enabled = :enabled, updated_at = :updated" \
  --expression-attribute-values '{
    ":enabled": {"BOOL": false},
    ":updated": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}
  }'
```

### Testing a Routing Rule

After adding or modifying a routing rule:

1. Send a test email to the address
2. Check CloudWatch Logs Insights with "routing-decision-analysis" query
3. Verify the correct routing decision was made
4. Check destination (Gmail inbox or bounce received)
5. Review X-Ray trace for end-to-end processing

### Common Routing Rule Patterns

**Exact Address Match**:

```bash
PK: "ROUTE#user@example.com"
action: "forward-to-gmail"
target: "destination@gmail.com"
```

**Domain Wildcard** (all addresses at domain):

```bash
PK: "ROUTE#*@example.com"
action: "forward-to-gmail"
target: "catchall@gmail.com"
```

**Global Wildcard** (default for unmatched):

```bash
PK: "ROUTE#*"
action: "bounce"
target: ""
```

**Note**: Router performs hierarchical lookup: exact match → normalized (removes +tag) → domain wildcard → global wildcard. First match wins.

### Viewing All Routing Rules

```bash
# Scan all routing rules
AWS_PROFILE=ses-mail aws dynamodb scan \
  --table-name ses-email-routing-test \
  --filter-expression "entity_type = :type" \
  --expression-attribute-values '{":type": {"S": "ROUTE"}}' \
  --projection-expression "recipient,action,target,enabled,description"
```

## Escalation Paths

### Level 1: Automated Recovery

- DLQ redrive runbooks (transient errors)

- Queue health monitoring
- Standard operational procedures

### Level 2: On-Call Engineer

- Application bugs requiring code fixes

- Configuration issues requiring Terraform changes
- DLQ messages requiring manual investigation

### Level 3: AWS Support

- AWS service outages or degradation

- Quota increases (SES sending limits, Lambda concurrency)
- Infrastructure-level issues

### External Dependencies

- **Gmail API**: Google Cloud Console → APIs & Services

- **OAuth Token**: Token refresh requires manual process (see README.md)
- **DNS/Route53**: Domain verification and email routing

## Best Practices

1. **Monitor DLQs Daily**: Check dashboard each morning for overnight issues
2. **Review Routing Rules Monthly**: Ensure rules are current and necessary
3. **Test After Changes**: Always send test email after modifying routing rules
4. **Document Incidents**: Record root cause and resolution for future reference
5. **Keep Runbooks Updated**: Update this document when procedures change
6. **Regular Health Checks**: Run queue health check automation weekly
7. **X-Ray Trace Reviews**: Periodically review traces to identify optimization opportunities

## Support Contacts

- **System Owner**: [Your team/contact]
- **AWS Account**: [Account ID and account owner]
- **Gmail API Project**: [Google Cloud project name]
- **On-Call**: [PagerDuty/on-call system]
