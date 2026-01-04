# Operations Guide

This guide covers day-to-day operational tasks for managing the SES Mail system. Use the quick reference table below to find common commands quickly.

## Table of Contents

- [Quick Start for Day-to-Day Operations](#quick-start-for-day-to-day-operations)
- [Quick Command Reference](#quick-command-reference)
- [Email Routing Management](#email-routing-management)
- [OAuth Token Management](#oauth-token-management)
- [SMTP Credential Management](#smtp-credential-management)
- [Outbound Email Monitoring](#outbound-email-monitoring)
- [Monitoring, Troubleshooting, and Recovery](#monitoring-troubleshooting-and-recovery) - See [MONITORING.md](MONITORING.md) and [RECOVERY.md](RECOVERY.md)
- [Backup and Recovery](#backup-and-recovery)
- [Best Practices](#best-practices)
- [Escalation Paths](#escalation-paths)

## Quick Start for Day-to-Day Operations

**Most common tasks:**

1. **Refresh OAuth token** (every 7 days in testing mode)
   ```bash
   AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test
   ```

2. **Add routing rule** (when adding new email addresses)
   - See [Email Routing Management](#adding-routing-rules) for detailed commands
   - Quick example: Forward `new@example.com` to Gmail:
   ```bash
   AWS_PROFILE=ses-mail aws dynamodb put-item \
     --table-name ses-mail-email-routing-test \
     --item '{"PK": {"S": "ROUTE#new@example.com"}, "SK": {"S": "RULE#v1"}, ...}'
   ```

3. **Check logs** (when debugging email issues)
   ```bash
   # Router logs
   AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow

   # Gmail forwarder logs
   AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-gmail-forwarder-test --follow
   ```

4. **Monitor dashboard** (weekly health check)
   ```bash
   # Get dashboard URL
   cd terraform/environments/test && terraform output dashboard_url
   ```

**In an emergency:**

1. Check [MONITORING.md - Common Failure Scenarios](MONITORING.md#common-failure-scenarios) for your issue
2. Follow [MONITORING.md - Incident Response Procedures](MONITORING.md#incident-response-procedures) appropriate to severity
3. Review [Escalation Paths](#escalation-paths) if you need additional support

**First time here?** Start with [SETUP.md](SETUP.md) for initial deployment, then return here for day-to-day operations.

## Quick Command Reference

| Task | Command |
|------|---------|
| **Deploy Changes** | `AWS_PROFILE=ses-mail make apply ENV=test` |
| **Show Plan** | `AWS_PROFILE=ses-mail make show-plan ENV=test` |
| **Validate Config** | `AWS_PROFILE=ses-mail make validate ENV=test` |
| **Refresh OAuth Token** | `AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test` |
| **Add Routing Rule** | See [Email Routing Management](#adding-routing-rules) |
| **View Router Logs** | `AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow` |
| **View Gmail Forwarder Logs** | `AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-gmail-forwarder-test --follow` |
| **Check Queue Depth** | `AWS_PROFILE=ses-mail aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names ApproximateNumberOfMessages` |
| **Get Terraform Outputs** | `AWS_PROFILE=ses-mail make outputs ENV=test` |
| **View CloudWatch Dashboard** | `cd terraform/environments/test && terraform output dashboard_url` |
| **Check Token Expiration** | `AWS_PROFILE=ses-mail aws cloudwatch get-metric-statistics --namespace "SESMail/test" --metric-name TokenSecondsUntilExpiration ...` |
| **Run Integration Tests** | `AWS_PROFILE=ses-mail python3 scripts/integration_test.py --env test` |

## Email Routing Management

### Understanding Routing Rules

The system uses DynamoDB to store email routing rules with hierarchical address matching. The router lambda performs lookups in this order (first match wins):

1. **Exact match**: `ROUTE#user+tag@example.com`
2. **Normalized match**: `ROUTE#user@example.com` (removes +tag for Gmail plus addressing)
3. **Domain wildcard**: `ROUTE#*@example.com`
4. **Global wildcard**: `ROUTE#*` (default catch-all)

### DynamoDB Table Structure

**Table Name**: `ses-mail-email-routing-{environment}`

**Key Schema:**
- **PK (Primary Key)**: `ROUTE#<email-pattern>`
- **SK (Sort Key)**: `RULE#v1`

**Attributes:**
- `entity_type`: Always `"ROUTE"`
- `recipient`: Email pattern (denormalized from PK)
- `action`: `"forward-to-gmail"` or `"bounce"`
- `target`: Gmail address (for forward-to-gmail) or empty string (for bounce)
- `enabled`: Boolean (`true` or `false`)
- `created_at`: ISO 8601 timestamp
- `updated_at`: ISO 8601 timestamp
- `description`: Human-readable description

### Adding Routing Rules

#### Forward Specific Address to Gmail

```bash
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-mail-email-routing-test \
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

#### Forward All Emails for a Domain

```bash
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-mail-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#*@example.com"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "*@example.com"},
    "action": {"S": "forward-to-gmail"},
    "target": {"S": "catch-all@gmail.com"},
    "enabled": {"BOOL": true},
    "created_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "updated_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "description": {"S": "Forward all domain emails to Gmail"}
  }'
```

#### Bounce Unmatched Emails (Default Rule)

```bash
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-mail-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#*"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "*"},
    "action": {"S": "bounce"},
    "target": {"S": ""},
    "enabled": {"BOOL": true},
    "created_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "updated_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "description": {"S": "Default: bounce all unmatched emails"}
  }'
```

### Viewing Routing Rules

```bash
# Get specific routing rule
AWS_PROFILE=ses-mail aws dynamodb get-item \
  --table-name ses-mail-email-routing-test \
  --key '{"PK": {"S": "ROUTE#support@example.com"}, "SK": {"S": "RULE#v1"}}'

# Scan all routing rules
AWS_PROFILE=ses-mail aws dynamodb scan \
  --table-name ses-mail-email-routing-test \
  --filter-expression "entity_type = :et" \
  --expression-attribute-values '{":et": {"S": "ROUTE"}}'
```

### Updating Routing Rules

To update a rule, use `put-item` with the same PK and SK but different attribute values:

```bash
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-mail-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#support@example.com"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "support@example.com"},
    "action": {"S": "forward-to-gmail"},
    "target": {"S": "new-email@gmail.com"},
    "enabled": {"BOOL": true},
    "created_at": {"S": "2025-01-18T10:00:00Z"},
    "updated_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "description": {"S": "Updated Gmail target"}
  }'
```

### Disabling Routing Rules

To temporarily disable a rule without deleting it:

```bash
AWS_PROFILE=ses-mail aws dynamodb update-item \
  --table-name ses-mail-email-routing-test \
  --key '{"PK": {"S": "ROUTE#support@example.com"}, "SK": {"S": "RULE#v1"}}' \
  --update-expression "SET enabled = :val, updated_at = :ts" \
  --expression-attribute-values '{
    ":val": {"BOOL": false},
    ":ts": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}
  }'
```

### Deleting Routing Rules

```bash
AWS_PROFILE=ses-mail aws dynamodb delete-item \
  --table-name ses-mail-email-routing-test \
  --key '{"PK": {"S": "ROUTE#support@example.com"}, "SK": {"S": "RULE#v1"}}'
```

### Testing Routing Rules

After adding a routing rule, test it by sending an email to the configured address:

```bash
# Send test email via any email client to the address
# Or use AWS SES (from a verified sender):
AWS_PROFILE=ses-mail aws ses send-email \
  --from verified-sender@example.com \
  --destination "ToAddresses=support@example.com" \
  --message "Subject={Data='Test Routing'},Body={Text={Data='Testing email routing'}}"
```

Then check the logs to verify routing:

```bash
# Check router logs for routing decision
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow

# Check Gmail forwarder logs for delivery
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-gmail-forwarder-test --follow
```

## OAuth Token Management

### Token Overview

The system uses Google OAuth for Gmail API access with two types of credentials:

1. **OAuth Client Credentials** (from Google Cloud Console)
   - Stored in SSM: `/ses-mail/{env}/gmail-forwarder/oauth/client-credentials`
   - Never expires unless revoked
   - Used to generate access tokens

2. **OAuth Refresh Token** (generated by OAuth flow)
   - Stored in SSM: `/ses-mail/{env}/gmail-forwarder/oauth/refresh-token`
   - Expires after 7 days in Google OAuth testing mode
   - Used by Lambda to generate short-lived access tokens

### Refreshing OAuth Tokens

When your refresh token expires (7 days in testing mode), run the refresh script:

```bash
# For test environment
AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test

# For production environment
AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env prod
```

**What the script does:**

1. Retrieves OAuth client credentials from SSM
2. Opens browser to Google consent screen
3. Runs local web server on port 8080 for OAuth callback
4. Exchanges authorization code for new refresh token
5. Stores new token in SSM with expiration metadata
6. Publishes CloudWatch metric for expiration monitoring
7. Automatically triggers Step Function to process queued messages

**Interactive flow:**

- Browser opens automatically to Google OAuth consent screen
- Review permissions and click "Allow"
- Return to terminal after authorization
- Script confirms successful token refresh

### Monitoring Token Expiration

The system automatically monitors token expiration every 5 minutes using:

- **EventBridge Rule**: Triggers Step Function every 5 minutes
- **Step Function**: Calculates seconds until expiration and publishes CloudWatch metric
- **CloudWatch Metric**: `TokenSecondsUntilExpiration` in namespace `SESMail/{environment}`
- **CloudWatch Alarms**: Two-tier alerting (24-hour warning, 6-hour critical)

**Check current token expiration:**

```bash
# Get latest token expiration metric
AWS_PROFILE=ses-mail aws cloudwatch get-metric-statistics \
  --namespace "SESMail/test" \
  --metric-name TokenSecondsUntilExpiration \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Minimum \
  --query 'Datapoints[0].Minimum'

# Check alarm statuses
AWS_PROFILE=ses-mail aws cloudwatch describe-alarms \
  --alarm-name-prefix "ses-mail-gmail-token" \
  --query 'MetricAlarms[*].[AlarmName,StateValue,StateReason]' \
  --output table
```

### Subscribing to Token Expiration Alerts

Subscribe to SNS topic for proactive notifications:

```bash
# Subscribe email to token alerts
AWS_PROFILE=ses-mail aws sns subscribe \
  --topic-arn arn:aws:sns:ap-southeast-2:{account-id}:ses-mail-gmail-forwarder-token-alerts-test \
  --protocol email \
  --notification-endpoint admin@example.com

# Confirm subscription in your email inbox
```

**Alert thresholds:**

- **Warning (24 hours)**: Early notice to plan token refresh
- **Critical (6 hours)**: Urgent reminder - refresh immediately

### Troubleshooting OAuth Issues

#### Error: "OAuth client credentials not yet configured"

Upload `client_secret.json` to SSM:

```bash
AWS_PROFILE=ses-mail aws ssm put-parameter \
  --name "/ses-mail/test/gmail-forwarder/oauth/client-credentials" \
  --value "$(cat client_secret.json)" \
  --type SecureString \
  --overwrite
```

#### Error: "Port 8080 is already in use"

Check and free port 8080:

```bash
# Check what's using port 8080
lsof -i :8080

# Kill the process or use a different port
```

#### Error: "OAuth flow succeeded but did not return a refresh token"

This happens if you've already authorized the app. To fix:

1. Visit <https://myaccount.google.com/permissions>
2. Find "ses-mail" application
3. Click "Remove access"
4. Re-run the refresh script

#### Error: "OAuth redirect URI mismatch"

Add `http://localhost:8080/callback` to redirect URIs in Google Cloud Console:

1. Go to: **Google Cloud Console** → **APIs & Services** → **Credentials**
2. Click on your OAuth client ID
3. Add `http://localhost:8080/callback` to "Authorized redirect URIs"
4. Save and retry

### Production OAuth Mode

To eliminate the 7-day token expiration:

1. **Publish OAuth App**: Submit your app for Google verification
2. **Move to Production**: Change app status from "Testing" to "Production"
3. **Benefit**: Refresh tokens don't expire unless explicitly revoked

Note: Google's review process can take several weeks.

## SMTP Credential Management

### Overview

The system provides automated SMTP credential management for applications that need to send email directly via AWS SES SMTP endpoint.

**Features:**

- Automated IAM user creation via DynamoDB Streams
- KMS-encrypted credential storage
- Per-user email sending restrictions
- Automatic IAM cleanup on deletion

### Getting SMTP Endpoint Configuration

```bash
# For test environment
AWS_PROFILE=ses-mail make outputs ENV=test | grep smtp_endpoint

# Or directly from Terraform
cd terraform/environments/test
terraform output smtp_endpoint
terraform output smtp_port
```

**Example output:**
```
smtp_endpoint = "email-smtp.ap-southeast-2.amazonaws.com"
smtp_port = 587  # or 465 for TLS, 25 for plaintext
```

### Creating SMTP Credentials

SMTP credentials are created automatically when you insert a record into DynamoDB:

```bash
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-mail-email-routing-test \
  --item '{
    "PK": {"S": "SMTP_USER#app-sender"},
    "SK": {"S": "CREDENTIALS#v1"},
    "entity_type": {"S": "SMTP_CREDENTIALS"},
    "username": {"S": "app-sender"},
    "allowed_senders": {"L": [
      {"S": "app@example.com"},
      {"S": "notifications@example.com"}
    ]},
    "status": {"S": "pending"},
    "created_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "description": {"S": "SMTP credentials for application"}
  }'
```

**What happens:**

1. DynamoDB Streams triggers credential manager Lambda
2. Lambda creates IAM user: `ses-smtp-user-app-sender-{timestamp}`
3. Lambda generates IAM access keys
4. Lambda converts secret key to SES SMTP password (AWS Version 4 algorithm)
5. Lambda encrypts credentials with KMS key
6. Lambda stores encrypted credentials in DynamoDB with `status="active"`

**Processing time:** Usually completes within 1-2 seconds.

### Retrieving SMTP Credentials

```bash
# Get SMTP credential record
AWS_PROFILE=ses-mail aws dynamodb get-item \
  --table-name ses-mail-email-routing-test \
  --key '{"PK": {"S": "SMTP_USER#app-sender"}, "SK": {"S": "CREDENTIALS#v1"}}' \
  --query 'Item.{Username:username.S,Status:status.S,IAMUser:iam_user_arn.S,EncryptedPassword:encrypted_credentials.S}'
```

**Response includes:**
- `username`: SMTP username (same as IAM access key ID)
- `status`: `active`, `pending`, or `disabled`
- `iam_user_arn`: ARN of the created IAM user
- `encrypted_credentials`: KMS-encrypted blob containing access key ID and SMTP password

### Decrypting SMTP Credentials

To use the credentials, decrypt them with KMS:

```bash
# Step 1: Get encrypted credentials from DynamoDB
ENCRYPTED=$(AWS_PROFILE=ses-mail aws dynamodb get-item \
  --table-name ses-mail-email-routing-test \
  --key '{"PK": {"S": "SMTP_USER#app-sender"}, "SK": {"S": "CREDENTIALS#v1"}}' \
  --query 'Item.encrypted_credentials.S' \
  --output text)

# Step 2: Decrypt using KMS
AWS_PROFILE=ses-mail aws kms decrypt \
  --ciphertext-blob fileb://<(echo "$ENCRYPTED" | base64 -d) \
  --query 'Plaintext' \
  --output text | base64 -d
```

**Output:**
```json
{
  "access_key_id": "AKIAEXAMPLE123",
  "smtp_password": "BMEXAMPLEVkJvhJP..."
}
```

### Configuring Email Clients

Use the decrypted credentials to configure SMTP:

**Settings:**

- **SMTP Server**: (from `terraform output smtp_endpoint`)
- **Port**: 587 (STARTTLS) or 465 (TLS)
- **Username**: `access_key_id` from decrypted credentials
- **Password**: `smtp_password` from decrypted credentials
- **Authentication**: LOGIN or PLAIN
- **Encryption**: STARTTLS (port 587) or TLS (port 465)

**Example (Python):**

```python
import smtplib
from email.mime.text import MIMEText

msg = MIMEText("Hello from SES SMTP!")
msg['Subject'] = "Test Email"
msg['From'] = "app@example.com"
msg['To'] = "recipient@example.com"

smtp = smtplib.SMTP("email-smtp.ap-southeast-2.amazonaws.com", 587)
smtp.starttls()
smtp.login("AKIAEXAMPLE123", "BMEXAMPLEVkJvhJP...")
smtp.send_message(msg)
smtp.quit()
```

### Monitoring SMTP Credential Usage

```bash
# Check CloudWatch metrics for credential operations
AWS_PROFILE=ses-mail aws cloudwatch get-metric-statistics \
  --namespace "SESMail/test" \
  --metric-name SMTPCredentialCreated \
  --start-time $(date -u -v-1d +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Sum

# View credential manager Lambda logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-credential-manager-test --follow
```

### Disabling SMTP Credentials

To temporarily disable credentials without deleting:

```bash
AWS_PROFILE=ses-mail aws dynamodb update-item \
  --table-name ses-mail-email-routing-test \
  --key '{"PK": {"S": "SMTP_USER#app-sender"}, "SK": {"S": "CREDENTIALS#v1"}}' \
  --update-expression "SET status = :status, updated_at = :ts" \
  --expression-attribute-values '{
    ":status": {"S": "disabled"},
    ":ts": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}
  }'
```

Note: This updates the status in DynamoDB but doesn't deactivate IAM access keys. To fully disable access, delete the credentials.

### Deleting SMTP Credentials

When you delete an SMTP credential record, the system automatically cleans up all IAM resources:

```bash
AWS_PROFILE=ses-mail aws dynamodb delete-item \
  --table-name ses-mail-email-routing-test \
  --key '{"PK": {"S": "SMTP_USER#app-sender"}, "SK": {"S": "CREDENTIALS#v1"}}'
```

**Automatic cleanup:**

1. DynamoDB Streams detects REMOVE event
2. Credential manager Lambda triggers
3. Lambda deletes all IAM access keys for the user
4. Lambda deletes all inline IAM policies
5. Lambda deletes the IAM user
6. CloudWatch metrics track deletion success

**Processing time:** Usually completes within 1-2 seconds.

### DNS SPF Record Configuration

Add SPF record to authorize AWS SES to send emails for your domain:

```bash
# Get SPF record recommendation from Terraform
cd terraform/environments/test
terraform output spf_record
```

**Example output:**
```
"v=spf1 include:amazonses.com ~all"
```

**Add to DNS:**

- **Name**: `YOUR_DOMAIN` (or blank/@ if zone is YOUR_DOMAIN)
- **Type**: TXT
- **Value**: `v=spf1 include:amazonses.com ~all`

**SPF policies:**

- `~all` - Softfail (recommended for testing)
- `-all` - Fail/reject (recommended for production after confirming SPF works)

## Outbound Email Monitoring

The system automatically tracks all outbound emails sent via SES SMTP using a Configuration Set associated with your domains. Metrics are published to CloudWatch for monitoring delivery success, bounces, and complaints.

### Configuration Set

All emails sent from verified domains automatically use the Configuration Set:
- **Configuration Set Name**: `ses-mail-outbound-{environment}`
- **Association**: Automatically configured at domain level (no SMTP client changes needed)
- **Events Tracked**: Send, Delivery, Bounce, Reject, Complaint

**Verify Configuration Set is associated:**
```bash
AWS_PROFILE=ses-mail aws sesv2 get-email-identity \
  --email-identity YOUR_DOMAIN \
  --region ap-southeast-2 \
  --query 'ConfigurationSetName'
```

### CloudWatch Metrics

Outbound email metrics are published to the `SESMail/{environment}` namespace:

| Metric | Description |
|--------|-------------|
| `OutboundSend` | Total emails sent |
| `OutboundDelivery` | Successfully delivered emails |
| `OutboundBounce` | Total bounces (hard + soft) |
| `OutboundBounceHard` | Permanent bounces (bad address) |
| `OutboundBounceSoft` | Temporary bounces (mailbox full, server down) |
| `OutboundComplaint` | Spam complaints |
| `OutboundReject` | Rejected by SES (invalid sender, etc.) |

**View metrics:**
```bash
# Check send volume (last hour)
AWS_PROFILE=ses-mail aws cloudwatch get-metric-statistics \
  --namespace "SESMail/test" \
  --metric-name OutboundSend \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 3600 \
  --statistics Sum \
  --region ap-southeast-2
```

### Dashboard Widgets

The CloudWatch dashboard includes 4 outbound email widgets:
1. **Outbound Email Volume** - Line graph showing sends, deliveries, bounces, complaints, rejects
2. **Outbound Delivery & Error Rates** - Percentage rates with warning/critical annotations
3. **Outbound Bounce Types** - Hard vs soft bounces (stacked area)
4. **AWS SES Reputation Metrics** - Native SES bounce/complaint rates from Configuration Set

**Access dashboard:**
```bash
# Get dashboard URL
AWS_PROFILE=ses-mail make outputs ENV=test | grep cloudwatch_dashboard_url
```

### CloudWatch Alarms

Two alarms monitor sender reputation:

**High Bounce Rate Alarm:**
- **Threshold**: 5% (industry standard warning level)
- **Evaluation**: 2 consecutive 5-minute periods
- **Action**: SNS notification to alarm topic
- **Impact**: Sustained >10% bounce rate can result in SES sending suspension

**High Complaint Rate Alarm:**
- **Threshold**: 0.1% (AWS SES account health threshold)
- **Evaluation**: 2 consecutive 5-minute periods
- **Action**: SNS notification to alarm topic
- **Severity**: CRITICAL - AWS may suspend account above 0.1%

**Check alarm status:**
```bash
AWS_PROFILE=ses-mail aws cloudwatch describe-alarms \
  --region ap-southeast-2 \
  --alarm-names \
    ses-mail-outbound-high-bounce-rate-test \
    ses-mail-outbound-high-complaint-rate-test
```

### Testing Outbound Metrics

Use the AWS SES Mailbox Simulator to test metrics without affecting sender reputation:

```bash
# Test successful delivery
AWS_PROFILE=ses-mail aws ses send-email \
  --from sender@YOUR_DOMAIN \
  --destination ToAddresses=success@simulator.amazonses.com \
  --message Subject={Data="Test"},Body={Text={Data="Metrics test"}} \
  --region ap-southeast-2

# Test hard bounce (bad address)
AWS_PROFILE=ses-mail aws ses send-email \
  --from sender@YOUR_DOMAIN \
  --destination ToAddresses=bounce@simulator.amazonses.com \
  --message Subject={Data="Bounce Test"},Body={Text={Data="Test"}} \
  --region ap-southeast-2

# Test spam complaint
AWS_PROFILE=ses-mail aws ses send-email \
  --from sender@YOUR_DOMAIN \
  --destination ToAddresses=complaint@simulator.amazonses.com \
  --message Subject={Data="Complaint Test"},Body={Text={Data="Test"}} \
  --region ap-southeast-2
```

**Verify metrics are published (wait 60 seconds after sending):**
```bash
# Check Lambda logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-outbound-metrics-test \
  --region ap-southeast-2 \
  --since 5m \
  --follow

# Check CloudWatch metric
AWS_PROFILE=ses-mail aws cloudwatch get-metric-statistics \
  --namespace "SESMail/test" \
  --metric-name OutboundSend \
  --start-time $(date -u -v-10M +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Sum \
  --region ap-southeast-2
```

### Troubleshooting Outbound Metrics

**Issue: Metrics not appearing after sending email**

1. **Verify Configuration Set association:**
   ```bash
   AWS_PROFILE=ses-mail aws sesv2 get-email-identity \
     --email-identity YOUR_DOMAIN \
     --region ap-southeast-2
   ```
   Should show `"ConfigurationSetName": "ses-mail-outbound-test"`

2. **Check SNS topic subscriptions:**
   ```bash
   AWS_PROFILE=ses-mail aws sns list-subscriptions \
     --region ap-southeast-2 \
     --query 'Subscriptions[?contains(TopicArn, `outbound`)]'
   ```
   All 4 topics should be subscribed to Lambda function

3. **Check Lambda logs for errors:**
   ```bash
   AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-outbound-metrics-test \
     --region ap-southeast-2 \
     --since 1h
   ```

4. **Verify SES event destinations:**
   ```bash
   AWS_PROFILE=ses-mail aws ses describe-configuration-set \
     --configuration-set-name ses-mail-outbound-test \
     --region ap-southeast-2
   ```

**Issue: Alarm triggering frequently**

High bounce/complaint rates indicate deliverability problems:
- **Review bounce types**: Check `OutboundBounceHard` vs `OutboundBounceSoft` to determine if addresses are invalid (hard) or servers are temporarily unavailable (soft)
- **Check recipient addresses**: Hard bounces usually mean the email address is invalid or doesn't exist - verify addresses before sending
- **Review email content**: Complaints may indicate your messages are being flagged as spam by recipient mail servers
- **Verify SPF/DKIM/DMARC**: Misconfigured email authentication can cause bounces

## Monitoring, Troubleshooting, and Recovery

**For monitoring and troubleshooting**, see **[MONITORING.md](MONITORING.md)**:
- CloudWatch Dashboard access
- Viewing logs and X-Ray traces
- Common issues and solutions
- Failure scenarios and resolutions
- Incident response procedures

**For retry and recovery**, see **[RECOVERY.md](RECOVERY.md)**:
- Dead Letter Queue (DLQ) management
- Retry queue infrastructure
- Systems Manager automation runbooks
- Automated redrive procedures

## Backup and Recovery

### Backing Up Routing Rules

**Export all routing rules from DynamoDB:**

```bash
# Export to JSON file
AWS_PROFILE=ses-mail aws dynamodb scan \
  --table-name ses-mail-email-routing-test \
  --filter-expression "entity_type = :type" \
  --expression-attribute-values '{":type": {"S": "ROUTE"}}' \
  > routing_rules_backup_$(date +%Y%m%d).json

# Verify backup
cat routing_rules_backup_*.json | jq '.Items | length'
```

**Restore from backup:**

```bash
# Review backup file first
cat routing_rules_backup_20250119.json | jq '.Items[] | {PK: .PK.S, SK: .SK.S, recipient: .recipient.S}'

# Restore individual items
cat routing_rules_backup_20250119.json | jq -c '.Items[]' | while read item; do
  AWS_PROFILE=ses-mail aws dynamodb put-item \
    --table-name ses-mail-email-routing-test \
    --item "$item"
done
```

**Backup schedule recommendation**: Weekly exports, keep for 30 days

### Exporting OAuth Credentials

**Export OAuth credentials from SSM:**

```bash
# Export client credentials
AWS_PROFILE=ses-mail aws ssm get-parameter \
  --name "/ses-mail/test/gmail-forwarder/oauth/client-credentials" \
  --with-decryption \
  --query 'Parameter.Value' \
  --output text > client_secret_backup.json

# Export refresh token metadata (not the token itself)
AWS_PROFILE=ses-mail aws ssm get-parameter \
  --name "/ses-mail/test/gmail-forwarder/oauth/refresh-token" \
  --query 'Parameter.{Name:Name,Type:Type,LastModifiedDate:LastModifiedDate}' \
  > oauth_token_metadata.json
```

**IMPORTANT**: Store backup files securely (encrypted, access-controlled). Never commit to git.

**Restore OAuth credentials:**

```bash
# Restore client credentials
AWS_PROFILE=ses-mail aws ssm put-parameter \
  --name "/ses-mail/test/gmail-forwarder/oauth/client-credentials" \
  --value "$(cat client_secret_backup.json)" \
  --type SecureString \
  --overwrite

# For refresh token, run the OAuth flow again
AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test
```

### Infrastructure as Code (Terraform State)

**Terraform state is automatically backed up** in S3 with versioning enabled.

**View state history:**

```bash
# List state versions
aws s3api list-object-versions \
  --bucket terraform-state-{account-id} \
  --prefix ses-mail/test.tfstate

# Download specific version
aws s3api get-object \
  --bucket terraform-state-{account-id} \
  --key ses-mail/test.tfstate \
  --version-id <version-id> \
  terraform.tfstate.backup
```

**Restore previous state** (CAUTION):

```bash
# Download backup version
aws s3api get-object \
  --bucket terraform-state-{account-id} \
  --key ses-mail/test.tfstate \
  --version-id <previous-version-id> \
  terraform.tfstate.restore

# Review what will change
terraform plan

# Apply if acceptable (or use the backup to selectively restore resources)
```

**State backup is automatic** - no manual action required. S3 versioning retains all state history.

### Disaster Recovery Checklist

**Before disaster:**
- [ ] Weekly DynamoDB routing rules export
- [ ] OAuth credentials backed up securely
- [ ] Terraform code in version control (git)
- [ ] CloudWatch dashboard screenshots saved
- [ ] Documentation reviewed and up-to-date

**During disaster:**
1. Assess scope: What's affected? (routing rules, OAuth, infrastructure, all?)
2. Check backups: When was last known-good state?
3. Review recent changes: What changed before the disaster?
4. Restore from backup: Use most recent good backup
5. Verify restoration: Run integration tests
6. Monitor: Watch logs and metrics for 24 hours

**After recovery:**
1. Document what happened (incident report)
2. Update runbooks if needed
3. Improve backup procedures if gaps found
4. Consider automation for frequently restored items

### Testing Backups

**Recommended quarterly:**

```bash
# 1. Export production routing rules
AWS_PROFILE=ses-mail aws dynamodb scan \
  --table-name ses-mail-email-routing-prod \
  --filter-expression "entity_type = :type" \
  --expression-attribute-values '{":type": {"S": "ROUTE"}}' \
  > test_restore.json

# 2. Create test table
AWS_PROFILE=ses-mail aws dynamodb create-table \
  --table-name ses-mail-email-routing-restore-test \
  --attribute-definitions AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
  --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST

# 3. Restore to test table
cat test_restore.json | jq -c '.Items[]' | while read item; do
  AWS_PROFILE=ses-mail aws dynamodb put-item \
    --table-name ses-mail-email-routing-restore-test \
    --item "$item"
done

# 4. Verify restoration
AWS_PROFILE=ses-mail aws dynamodb scan \
  --table-name ses-mail-email-routing-restore-test \
  --filter-expression "entity_type = :type" \
  --expression-attribute-values '{":type": {"S": "ROUTE"}}' \
  | jq '.Items | length'

# 5. Delete test table
AWS_PROFILE=ses-mail aws dynamodb delete-table \
  --table-name ses-mail-email-routing-restore-test
```

## Best Practices

### Operational Best Practices

1. **Subscribe to alerts:** Always subscribe to SNS topics for token expiration and critical alarms
2. **Monitor dashboards regularly:** Check CloudWatch dashboard weekly for anomalies
3. **Test routing rules:** Always test new routing rules with test emails before relying on them
4. **Keep OAuth tokens fresh:** Refresh tokens proactively when warning alarms fire (24 hours)
5. **Review logs periodically:** Check Lambda logs for errors or warnings
6. **Backup routing rules:** Export DynamoDB routing rules periodically for backup
7. **Use descriptive names:** Add clear descriptions to all routing rules and SMTP credentials
8. **Clean up unused resources:** Delete unused routing rules and SMTP credentials

### Security Best Practices

1. **Rotate SMTP credentials:** Periodically rotate SMTP credentials for applications
2. **Use least privilege:** SMTP credentials restrict sending to specific "From" addresses
3. **Monitor unauthorized access:** Watch for failed authentication attempts in logs
4. **Encrypt sensitive data:** All credentials are KMS-encrypted at rest
5. **Delete local tokens:** Never keep OAuth tokens in local files after uploading to SSM
6. **Use production OAuth:** Move to production OAuth mode to eliminate 7-day expiration
7. **Review IAM policies:** Periodically audit IAM policies for Lambda functions
8. **Enable MFA:** Use MFA for AWS accounts with SES access

### Performance Best Practices

1. **Use domain wildcards:** Reduce DynamoDB lookups with domain-level routing rules
2. **Monitor queue depths:** Keep an eye on SQS queue depths to detect processing delays
3. **Review Lambda timeouts:** Increase Lambda timeouts if seeing timeout errors
4. **Enable X-Ray sampling:** Use 1% sampling to balance observability and cost
5. **Archive old logs:** Use CloudWatch log retention policies to manage costs
6. **Optimize routing rules:** Order routing rules from most specific to most general

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
- **OAuth Token**: Token refresh requires manual process (see [OAuth Token Management](#oauth-token-management))
- **DNS/Route53**: Domain verification and email routing
