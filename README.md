# SES Mail System

## Initial Google Cloud Setup

- Go to <https://console.cloud.google.com>
- Click the current project (top left next to "Google Cloud")
- Click "New Project"
- Name it "ses-mail"
- Leave the organisation as "No organisation"
- Create
- ☰ -> APIs and services -> Library
- Search for "Gmail API"
- Select "Gmail API"
- Click "Enable"
- Select "OAuth consent screen" in the left-side menu
- It will say "Google auth platform not configured yet" - Click "Get started"
- App name: ses-mail
- User support email: select your email address
- Next
- Select External
- Next
- Add your email address for the contact address
- Next
- Agree to the policy
- Continue
- Create
- Audience -> Add users
- Add your gmail address
- Save
- Clients -> Create client
- Select "Create OAuth client"
- Application type: Desktop app
- Name: ses-mail
- Create
- Select "Download JSON" - and save it for later
- OK
- From the left menu -> Data Access -> Add or remove scopes
- Add "Gmail API .../auth/gmail.insert"
- Update
- Save
- Put the client secret file in this directory, named "client_secret.json"
- `python3 -m venv .venv`
- Either source the file mentioned, or let VSCode handle it and create a new terminal
- `pip3 install -r requirements.txt`
- `./scripts/create_refresh_token.py`
- In the browser window that pops up, select your account
- Continue
- Continue
- Close the tab
- You can use the scripts in `scripts/` to test the token
- After setting up the infrastructure, set the token parameter to the value of token.json
- Delete the `client_secret.json` and `token.json` files from the local filesystem

## Terraform Infrastructure Setup

Once you have created your Gmail OAuth token (above), you can deploy the AWS infrastructure:

### Directory Structure

The infrastructure is organized into environments and a reusable module:

```plain
terraform/
├── environments/
│   ├── test/          # Test environment configuration
│   └── prod/          # Production environment configuration
└── modules/
    └── ses-mail/      # Reusable SES mail module
```

### Quick Start

1. The infrastructure uses a Terraform state bucket in S3. The Makefile will automatically create `terraform-state-{account-id}` in your AWS account on first run.

1. Review and customize the configuration for your environment:

   ```bash
   # For test environment
   vi terraform/environments/test/terraform.tfvars

   # For production environment
   vi terraform/environments/prod/terraform.tfvars

   # Update domain(s) and SNS topic ARN as needed
   # domain should be a list: ["mail.example.com", "mail2.example.com"]
   ```

1. Deploy the infrastructure (first pass):

   ```bash
   # For test environment
   make init ENV=test   # Initialize Terraform and create state bucket
   make plan ENV=test   # Package Lambda and create a plan file
   make apply ENV=test  # Apply the plan (creates resources and outputs DNS records)

   # For production environment
   make init ENV=prod   # Initialize Terraform and create state bucket
   make plan ENV=prod   # Package Lambda and create a plan file
   make apply ENV=prod  # Apply the plan (creates resources and outputs DNS records)
   ```

   **Note:** The `make plan` target automatically packages the Lambda function with its dependencies. If MTA-STS is enabled, the first apply will create the ACM certificate but CloudFront creation will fail. This is expected - continue to step 4.

1. Configure DNS records in Route53:

   First, get the DNS records from Terraform:

   ```bash
   # For test environment
   cd terraform/environments/test
   terraform output dns_configuration_summary

   # For production environment
   cd terraform/environments/prod
   terraform output dns_configuration_summary
   ```

   The output will be grouped by domain. For each domain, you'll need to add the following records to your Route53 hosted zone:

   **Domain Verification (TXT record)**

   - Name: `_amazonses.YOUR_DOMAIN`
   - Type: TXT
   - Value: The verification token from the output
   - TTL: 1800 (or default)

   **Email Receiving (MX record)**

   - Name: `YOUR_DOMAIN` (or leave blank if zone is YOUR_DOMAIN)
   - Type: MX
   - Value: `10 inbound-smtp.ap-southeast-2.amazonaws.com` (adjust region as needed)
   - TTL: 1800 (or default)

   **DKIM Authentication (3 CNAME records per domain)**

   - For each of the 3 DKIM tokens in the output:
     - Name: `{token}._domainkey.YOUR_DOMAIN`
     - Type: CNAME
     - Value: `{token}.dkim.amazonses.com`
     - TTL: 1800 (or default)

   **DMARC Policy (TXT record per domain)**

   - Name: `_dmarc.YOUR_DOMAIN`
   - Type: TXT
   - Value: `v=DMARC1; p=reject; rua=mailto:dmarc@YOUR_DOMAIN` (if prefix configured)
   - TTL: 1800 (or default)
   - Purpose: Prevents others from spoofing your domain

   **MTA-STS (if enabled - records per domain)**

   - Name: `_mta-sts.YOUR_DOMAIN`
   - Type: TXT
   - Value: From terraform output (contains policy ID)
   - TTL: 1800 (or default)

   - Name: `mta-sts.YOUR_DOMAIN`
   - Type: CNAME
   - Value: CloudFront distribution URL from output
   - TTL: 1800 (or default)

   - ACM validation CNAME records (from terraform output, one set per domain)

   **TLS Reporting (if email configured - per domain)**

   - Name: `_smtp._tls.YOUR_DOMAIN`
   - Type: TXT
   - Value: `v=TLSRPTv1; rua=mailto:tlsrpt@YOUR_DOMAIN`
   - TTL: 1800 (or default)

   **Via AWS Console:**
   1. Go to Route53 → Hosted zones
   2. Select your hosted zone
   3. Click "Create record"
   4. Add each record as shown above

   **Via AWS CLI:**

   ```bash
   # Get the records in JSON format
   cd terraform/environments/test  # or prod
   terraform output -json dns_configuration_summary > /tmp/dns-records.json

   # Then manually create records or use change-resource-record-sets
   # See terraform/modules/ses-mail/README.md for detailed CLI examples
   ```

   Wait 5-15 minutes for DNS propagation, then verify:

   ```bash
   aws ses get-identity-verification-attributes --identities mail.example.com mail2.example.com
   ```

1. Complete MTA-STS setup (if enabled):

   After adding the DNS records (including ACM validation records for each domain), wait for all ACM certificates to validate:

   ```bash
   # Check certificate status (should show ISSUED for all)
   aws acm list-certificates --region us-east-1
   ```

   Once all certificates are validated (usually 5-30 minutes), run terraform again to create CloudFront distributions:

   ```bash
   make plan ENV=test  # or ENV=prod
   make apply ENV=test  # or ENV=prod
   ```

1. Upload your Gmail token to SSM Parameter Store:

   ```bash
   # For test environment
   aws ssm put-parameter \
     --name "/ses-mail/test/gmail-token" \
     --value "$(cat token.json)" \
     --type SecureString \
     --overwrite

   # For production environment
   aws ssm put-parameter \
     --name "/ses-mail/prod/gmail-token" \
     --value "$(cat token.json)" \
     --type SecureString \
     --overwrite
   ```

1. Enable GLE

The auto-enablement of the GLE does not work yet. Run it manually with:

```bash
aws resource-groups update-account-settings \
--group-lifecycle-events-desired-status ACTIVE
```

1. Enable Transaction Search

Go to the XRay console, and enable transaction search, with 1% ingestion.

### Workflow

All commands now require an `ENV` parameter to specify which environment (test or prod):

- **make package ENV=test**: Packages the Lambda function with dependencies (automatically run by make plan)
- **make plan ENV=test**: Creates a plan file showing what changes will be made
- **make apply ENV=test**: Applies the plan file (depends on plan, so will create it if missing)
- **make plan-destroy ENV=test**: Creates a destroy plan
- **make destroy ENV=test**: Applies the destroy plan (depends on plan-destroy)

For detailed instructions and configuration options, see [terraform/modules/ses-mail/README.md](terraform/modules/ses-mail/README.md).

## Architecture

### Email Processing Flow

The system uses an event-driven architecture for processing incoming emails:

```text
SES → S3 → SNS (X-Ray tracing) → SQS Input Queue → EventBridge Pipes[router enrichment] → EventBridge Event Bus → Handler Queues → Lambda Processors
```

**Implementation Flow:**

1. **SES Receipt** - Email arrives and is scanned for spam/virus
2. **S3 Storage + SNS Notification** - SES stores email in S3 and triggers SNS (single action)
3. **SNS Topic** - Receives notification with X-Ray Active tracing enabled
4. **SQS Input Queue** - Receives messages from SNS for EventBridge Pipes processing
5. **EventBridge Pipes** - Enriches messages with routing decisions via router lambda
6. **EventBridge Event Bus** - Routes enriched messages to appropriate handler queues
7. **Handler SQS Queues** - Separate queues for Gmail forwarding and bouncing
8. **Handler Lambdas** - Process messages from handler queues

**Infrastructure Components:**

- **SNS Topic**: `ses-email-processing-{environment}` with X-Ray Active tracing
- **SQS Input Queue**: `ses-email-input-{environment}` with 3-retry DLQ policy
- **EventBridge Pipe**: `ses-email-router-{environment}` with router lambda enrichment
  - Source: SQS input queue
  - Enrichment: Router lambda function (DynamoDB routing rules lookup)
  - Target: EventBridge Event Bus
  - Logging: CloudWatch Logs at INFO level with execution data
- **Router Lambda**: `ses-mail-router-enrichment-{environment}` with X-Ray Active tracing
  - Queries DynamoDB for routing rules with hierarchical address matching
  - Enriches messages with routing decisions and email metadata
  - Timeout: 30 seconds, Memory: 128 MB
- **EventBridge Event Bus**: `ses-email-routing-{environment}` with routing rules
  - Routes messages to Gmail forwarder queue (action: `forward-to-gmail`)
  - Routes messages to bouncer queue (action: `bounce`)
- **Handler Queues**:
  - `ses-gmail-forwarder-{environment}` - Triggers Gmail forwarder lambda
  - `ses-bouncer-{environment}` - Triggers bouncer lambda
  - Both with 3-retry DLQ policies and CloudWatch alarms
- **Dead Letter Queues**: All queues have corresponding DLQs with 14-day retention
- **CloudWatch Alarms**: Monitors DLQ messages, queue age, and Pipes failures

**X-Ray Distributed Tracing:**

The entire email processing pipeline is instrumented with X-Ray tracing:

- SNS topic initiates traces with Active tracing
- SQS queues propagate trace context
- EventBridge Pipes maintains trace context through enrichment
- Router lambda adds custom annotations for email metadata
- Handler lambdas continue the trace for end-to-end visibility

**EventBridge Pipes Integration:**

EventBridge Pipes provides serverless message enrichment and routing:

- Automatically polls SQS input queue and invokes router lambda
- Manages retries and error handling with built-in DLQ support
- Logs all executions to CloudWatch for debugging and monitoring
- Transforms enriched output into EventBridge events with proper source and detail-type
- No custom polling or dispatch logic required - fully managed by AWS

## AWS myApplications Integration

The infrastructure is registered with AWS myApplications through AWS Service Catalog AppRegistry, providing application-level visibility and management capabilities in the AWS Console.

### What is myApplications?

AWS myApplications provides a centralized view to manage your applications and their resources. It integrates with AppRegistry to:

- View application health and status
- Track costs at the application level
- Monitor operational metrics
- Manage application metadata and documentation
- Access application resources in one place

**Accessing myApplications:**

```bash
# Get the myApplications URL from Terraform output
cd terraform/environments/test  # or prod
terraform output myapplications_url

# Or navigate in AWS Console:
# AWS Console → Systems Manager → AppManager → Applications
# Select "ses-mail-{environment}"
```

The AppRegistry application automatically discovers and includes all resources tagged with `Application=ses-mail-{environment}`. All infrastructure resources are tagged via Terraform provider `default_tags`, ensuring they automatically appear in the myApplications view.

**Note**: Optional tag-sync automation requires AWS Group Lifecycle Events (GLE) to be enabled at the account level. However, resources will still appear in myApplications based on the `Application` tag alone.

## AWS Resource Groups

The infrastructure includes an AWS Resource Group that provides a single view of all resources for each environment. All resources are automatically tagged with:

- **Project**: `ses-mail`
- **ManagedBy**: `terraform`
- **Environment**: `test` or `prod`
- **Application**: `ses-mail-{environment}` (combined tag for myApplications integration)

The Resource Group uses these tags to organize resources, making it easy to:

- View all related resources in one place
- Track costs by environment
- Manage resources collectively
- Monitor resource health

**Accessing the Resource Group:**

```bash
# Get the Resource Group URL from Terraform output
cd terraform/environments/test  # or prod
terraform output resource_group_url

# Or view directly in AWS Console:
# https://console.aws.amazon.com/resource-groups/group/ses-mail-{environment}
```

The Resource Group includes all infrastructure components:

- S3 buckets
- Lambda functions
- DynamoDB tables
- SQS queues
- SNS topics
- CloudWatch alarms and log groups
- IAM roles
- SES resources

## Email Routing Configuration

### DynamoDB Routing Rules Table

The system uses a DynamoDB table to store email routing rules. The table uses a single-table design pattern for extensibility.

**Table Structure:**

- **Primary Key (PK)**: `ROUTE#<email-pattern>` (e.g., `ROUTE#support@example.com`, `ROUTE#*@example.com`, `ROUTE#*`)
- **Sort Key (SK)**: `RULE#v1` (allows versioning)
- **Billing**: PAY_PER_REQUEST (no standing costs)
- **DynamoDB Streams**: Enabled with `NEW_AND_OLD_IMAGES` view type for SMTP credential management

**Routing Rules Attributes:**

- `entity_type`: `ROUTE` (for filtering)
- `recipient`: Email pattern (denormalized from PK)
- `action`: `forward-to-gmail` | `bounce`
- `target`: Gmail address for forwarding, or empty for bounces
- `enabled`: Boolean (true/false)
- `created_at`: ISO timestamp
- `updated_at`: ISO timestamp
- `description`: Human-readable description

**Hierarchical Matching:**

The router lambda performs lookups in this order (first match wins):

1. Exact match: `ROUTE#user+tag@example.com`
2. Normalized match: `ROUTE#user@example.com` (removes +tag)
3. Domain wildcard: `ROUTE#*@example.com`
4. Global wildcard: `ROUTE#*`

**DynamoDB Streams:**

The table has DynamoDB Streams enabled to support automated SMTP credential management:

- **Stream Enabled**: `true`
- **Stream View Type**: `NEW_AND_OLD_IMAGES` (captures both before and after values)
- **Purpose**: Triggers credential manager Lambda when new SMTP credential records are inserted
- **Stream ARN**: Available via `aws dynamodb describe-table --table-name ses-email-routing-{environment}`

The stream captures INSERT and MODIFY events for records with `PK="SMTP_USER"`. When administrators manually insert a new SMTP credential record with `status="pending"`, the stream triggers the credential manager Lambda function to automatically:

1. Create a programmatic-only IAM user for SMTP authentication with unique naming (`ses-smtp-user-{username}-{timestamp}`)
2. Generate IAM access keys for SMTP authentication
3. Log all operations with correlation IDs for traceability
4. Track success/failure metrics in CloudWatch

**Current Implementation Status (Tasks 3.1-3.4 Complete):**

- ✅ Core credential creation logic with X-Ray tracing
- ✅ Structured JSON logging with correlation IDs
- ✅ IAM user creation with programmatic-only access
- ✅ Access key generation
- ✅ Email restriction policy generation with StringLike conditions
- ✅ Automatic policy attachment to IAM users
- ✅ SMTP password conversion using AWS algorithm (Version 4)
- ✅ KMS encryption of credentials with customer managed key
- ✅ Credential storage in DynamoDB with status="active"
- ✅ Automatic IAM resource cleanup on record deletion
- ⏳ Error handling and DLQ processing (Task 4)

This event-driven approach eliminates the need for manual credential creation and ensures secure, automated provisioning of SMTP access.

**SMTP Password Conversion Algorithm:**

The system converts IAM secret access keys to SES SMTP passwords using AWS's Version 4 signing algorithm:

1. Chain of HMAC-SHA256 operations: date → region → service (ses) → terminal (aws4_request) → message (SendRawEmail)
2. Prepend version byte 0x04 and base64 encode
3. Encrypt credentials with customer managed KMS key (`alias/ses-mail-smtp-credentials-{environment}`)
4. Store encrypted blob in DynamoDB with IAM user ARN

**KMS Key Management:**

A dedicated customer managed KMS key is created for encrypting SMTP credentials:

- **Key Alias**: `alias/ses-mail-smtp-credentials-{environment}`
- **Encryption**: Credentials stored as encrypted base64-encoded JSON containing `access_key_id` and `smtp_password`
- **Rotation**: Key rotation enabled (automatic annual rotation)
- **Access**: Lambda execution role has `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey`, `kms:DescribeKey` permissions

**Automatic Resource Cleanup:**

When SMTP credential records are deleted from DynamoDB, the system automatically cleans up all associated IAM resources:

1. **DynamoDB Stream REMOVE Event Detection**: Lambda detects record deletion via DynamoDB Streams
2. **Access Key Deletion**: Lists and deletes all IAM access keys for the user
3. **Policy Deletion**: Lists and deletes all inline IAM policies attached to the user
4. **IAM User Deletion**: Removes the IAM user completely
5. **CloudWatch Metrics**: Publishes deletion success/failure metrics for monitoring
6. **Idempotent Operation**: Gracefully handles cases where IAM user was already deleted

This ensures no orphaned IAM resources remain after credential deletion, maintaining security and preventing resource clutter.

**Managing Routing Rules:**

Add rules via AWS CLI or Console:

```bash
# Example: Forward support emails to Gmail
aws dynamodb put-item \
  --table-name ses-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#support@example.com"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "support@example.com"},
    "action": {"S": "forward-to-gmail"},
    "target": {"S": "your-email@gmail.com"},
    "enabled": {"BOOL": true},
    "created_at": {"S": "2025-01-18T10:00:00Z"},
    "updated_at": {"S": "2025-01-18T10:00:00Z"},
    "description": {"S": "Forward support emails to Gmail"}
  }'

# Example: Bounce all unmatched emails (default rule)
aws dynamodb put-item \
  --table-name ses-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#*"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "*"},
    "action": {"S": "bounce"},
    "target": {"S": ""},
    "enabled": {"BOOL": true},
    "created_at": {"S": "2025-01-18T10:00:00Z"},
    "updated_at": {"S": "2025-01-18T10:00:00Z"},
    "description": {"S": "Default: bounce all unmatched emails"}
  }'
```

### Router Enrichment Lambda Function

The router enrichment lambda (`ses-mail-router-enrichment-{environment}`) is used by EventBridge Pipes to enrich SES email events with routing decisions based on DynamoDB routing rules.

**Functionality:**

- **Hierarchical DynamoDB Lookup**: Performs lookups in order of specificity (exact → normalized → domain wildcard → global wildcard)
- **Email Address Normalization**: Removes +tag from addresses (e.g., `user+newsletter@example.com` → `user@example.com`) for plus addressing support
- **Security Analysis**: Extracts and analyses DMARC, SPF, DKIM, spam, and virus verdicts from SES receipt
- **Fallback Behaviour**: Defaults to "bounce" action when DynamoDB is unavailable or no rule matches
- **X-Ray Tracing**: Active tracing enabled with custom annotations for message ID, source, and routing action

**Input Format** (from EventBridge Pipes):

```json
[{
  "eventSource": "aws:ses",
  "ses": {
    "mail": {
      "messageId": "...",
      "source": "sender@example.com",
      "destination": ["recipient@domain.com"],
      "commonHeaders": {...}
    },
    "receipt": {
      "spamVerdict": {"status": "PASS"},
      "virusVerdict": {"status": "PASS"},
      "dkimVerdict": {"status": "PASS"},
      "spfVerdict": {"status": "PASS"},
      "dmarcVerdict": {"status": "PASS"}
    }
  }
}]
```

**Output Format** (to EventBridge Event Bus):

```json
[{
  "Source": "ses.email.router",
  "DetailType": "Email Routing Decision",
  "Detail": {
    "messageId": "...",
    "eventSource": "aws:sqs",
    "body": "{...original SES event as JSON string...}",
    "originalMessageId": "abc123...",
    "actions": {
      "store": {
        "count": 0,
        "targets": []
      },
      "forward-to-gmail": {
        "count": 1,
        "targets": [
          {"target": "recipient@domain.com", "destination": "user@gmail.com"}
        ]
      },
      "bounce": {
        "count": 0,
        "targets": []
      }
    }
  }
}]
```

The router performs hierarchical DynamoDB lookups for each recipient and increments the appropriate action count. For `forward-to-gmail` actions, the `targets` array contains objects with both the original recipient (`target`) and the Gmail destination (`destination`). For `bounce` and `store` actions, targets only contain the recipient address.

**Testing the Router Lambda:**

```bash
# Test with sample SES event
AWS_PROFILE=ses-mail aws lambda invoke \
  --function-name ses-mail-router-enrichment-test \
  --cli-binary-format raw-in-base64-out \
  --payload file://test_event.json \
  response.json

# View logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow
```

### EventBridge Event Bus and Routing Rules

The system uses an EventBridge Event Bus (`ses-email-routing-{environment}`) to route enriched email messages to appropriate handler queues based on routing decisions from the router enrichment lambda.

**EventBridge Event Bus:**

- **Name**: `ses-email-routing-{environment}`
- **Type**: Custom event bus (not default)
- **Purpose**: Routes enriched messages from EventBridge Pipes to handler SQS queues
- **Logging**: CloudWatch log group `/aws/events/ses-email-routing-{environment}` (30-day retention)

**EventBridge Rules:**

The Event Bus has two rules that match on routing decisions:

1. **Gmail Forwarder Rule** (`route-to-gmail-{environment}`):
   - **Event Pattern**: Matches `action: "forward-to-gmail"` in routing decisions
   - **Source**: `ses.email.router`
   - **Target**: `ses-gmail-forwarder-{environment}` SQS queue
   - **Retry Policy**: Max 2 retries, 1 hour max event age
   - **Dead Letter Queue**: `ses-gmail-forwarder-dlq-{environment}`

2. **Bouncer Rule** (`route-to-bouncer-{environment}`):
   - **Event Pattern**: Matches `action: "bounce"` in routing decisions
   - **Source**: `ses.email.router`
   - **Target**: `ses-bouncer-{environment}` SQS queue
   - **Retry Policy**: Max 2 retries, 1 hour max event age
   - **Dead Letter Queue**: `ses-bouncer-dlq-{environment}`

**Event Flow:**

```text
EventBridge Pipes → EventBridge Event Bus
                           ↓
        Event Pattern Matching (on routingDecisions.action)
                           ↓
        ┌──────────────────┴──────────────────┐
        ↓                                     ↓
Gmail Forwarder Queue              Bouncer Queue
(action: forward-to-gmail)          (action: bounce)
        ↓                                     ↓
Gmail Forwarder Lambda              Bouncer Lambda
```

**CloudWatch Monitoring:**

- **Metric Filters**: Track EventBridge rule failures for both Gmail and bouncer rules
- **Alarms**: Trigger when EventBridge fails to deliver events to target queues
- **Namespace**: `SESMail/{environment}`
- **Metrics**: `EventBridgeGmailRuleFailures`, `EventBridgeBouncerRuleFailures`

**IAM Permissions:**

The EventBridge Event Bus uses an IAM role (`ses-mail-eventbridge-sqs-{environment}`) with permissions to:

- Send messages to Gmail forwarder SQS queue
- Send messages to bouncer SQS queue
- Send failed events to dead letter queues

**Testing EventBridge Rules:**

```bash
# List event buses
AWS_PROFILE=ses-mail aws events list-event-buses --region ap-southeast-2

# List rules on the event bus
AWS_PROFILE=ses-mail aws events list-rules \
  --event-bus-name ses-email-routing-test \
  --region ap-southeast-2

# List targets for a specific rule
AWS_PROFILE=ses-mail aws events list-targets-by-rule \
  --rule route-to-gmail-test \
  --event-bus-name ses-email-routing-test \
  --region ap-southeast-2

# Manually send a test event to the event bus
cat > test_event.json <<'EOF'
{
  "Source": "ses.email.router",
  "DetailType": "Email Routing Decision",
  "Detail": "{\"originalEvent\": {\"eventSource\": \"aws:ses\", \"ses\": {\"mail\": {\"messageId\": \"test123\"}}}, \"routingDecisions\": [{\"action\": \"forward-to-gmail\", \"recipient\": \"test@example.com\", \"target\": \"user@gmail.com\"}], \"emailMetadata\": {\"messageId\": \"test123\", \"source\": \"sender@example.com\"}}"
}
EOF

AWS_PROFILE=ses-mail aws events put-events \
  --entries file://test_event.json \
  --event-bus-name ses-email-routing-test \
  --region ap-southeast-2
```

**Event Pattern Examples:**

The EventBridge rules use event patterns to match routing decisions based on action counts. Here are the patterns used:

Gmail Forwarder Rule:

```json
{
  "source": ["ses.email.router"],
  "detail-type": ["Email Routing Decision"],
  "detail": {
    "actions": {
      "forward-to-gmail": {
        "count": [{ "numeric": [">", 0] }]
      }
    }
  }
}
```

This matches any event where at least one recipient has a `forward-to-gmail` action.

Bouncer Rule:

```json
{
  "source": ["ses.email.router"],
  "detail-type": ["Email Routing Decision"],
  "detail": {
    "actions": {
      "bounce": {
        "count": [{ "numeric": [">", 0] }]
      }
    }
  }
}
```

This matches any event where at least one recipient has a `bounce` action.

### Gmail Forwarder Lambda Function

The Gmail forwarder lambda (`ses-mail-gmail-forwarder-{environment}`) processes enriched email messages from the gmail-forwarder SQS queue and imports them into Gmail via the Gmail API.

**Functionality:**

- **SQS Event Processing**: Triggered by messages in the gmail-forwarder queue
- **Enriched Message Handling**: Extracts routing decisions and email metadata from EventBridge-enriched messages
- **Gmail API Integration**: Imports emails into Gmail with INBOX and UNREAD labels
- **S3 Email Management**: Fetches raw email from S3 and deletes after successful import
- **Token Management**: Automatically refreshes OAuth tokens and updates SSM Parameter Store
- **X-Ray Tracing**: Active tracing with custom annotations for message ID, recipient, action, and target
- **Error Handling**: Returns batch item failures for SQS retry logic

**Input Format** (from SQS/EventBridge):

The lambda receives SQS messages containing EventBridge events with the router's enriched data:

```json
{
  "Records": [{
    "body": "{\"detail\": {\"originalMessageId\": \"abc123\", \"body\": \"{...SES event JSON...}\", \"actions\": {\"forward-to-gmail\": {\"count\": 1, \"targets\": [{\"target\": \"recipient@domain.com\", \"destination\": \"user@gmail.com\"}]}}}}"
  }]
}
```

**Processing Flow:**

1. Parse SQS message body to extract EventBridge event detail
2. Extract `originalMessageId` and `actions.forward-to-gmail.targets` array
3. Parse SES event from `body` field to get email metadata (subject, source)
4. Fetch raw email from S3 (`emails/{originalMessageId}`)
5. For each target in targets array, import email into Gmail via Gmail API
6. Delete email from S3 after successful import of all targets
7. Return success or batch item failure for SQS retry

**Configuration:**

- **Runtime**: Python 3.12
- **Memory**: 128MB
- **Timeout**: 3 seconds (default)
- **Environment Variables**:
  - `GMAIL_TOKEN_PARAMETER`: SSM parameter path for Gmail OAuth token
  - `EMAIL_BUCKET`: S3 bucket containing email files

**Testing the Gmail Forwarder Lambda:**

```bash
# Create a test SQS message with enriched data
cat > test_enriched_message.json <<'EOF'
{
  "Records": [{
    "messageId": "test-msg-1",
    "receiptHandle": "test-receipt-handle",
    "body": "{\"originalEvent\": {\"eventSource\": \"aws:ses\", \"ses\": {\"mail\": {\"messageId\": \"abc123\", \"source\": \"sender@example.com\", \"destination\": [\"recipient@example.com\"]}}}, \"routingDecisions\": [{\"recipient\": \"recipient@example.com\", \"normalizedRecipient\": \"recipient@example.com\", \"action\": \"forward-to-gmail\", \"target\": \"your-email@gmail.com\", \"matchedRule\": \"ROUTE#recipient@example.com\"}], \"emailMetadata\": {\"messageId\": \"abc123\", \"source\": \"sender@example.com\", \"subject\": \"Test Email\", \"timestamp\": \"2025-01-18T10:00:00Z\"}}"
  }]
}
EOF

# Test the lambda function
AWS_PROFILE=ses-mail aws lambda invoke \
  --function-name ses-mail-gmail-forwarder-test \
  --cli-binary-format raw-in-base64-out \
  --payload file://test_enriched_message.json \
  response.json

# View the response
cat response.json

# View logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-gmail-forwarder-test --follow
```

**SQS Queue Configuration:**

- **Queue Name**: `ses-gmail-forwarder-{environment}`
- **Dead Letter Queue**: `ses-gmail-forwarder-dlq-{environment}` (14 day retention)
- **Visibility Timeout**: 30 seconds (10x lambda timeout)
- **Max Retries**: 3 (before moving to DLQ)
- **Event Source Mapping**: Batch size 1, max concurrency 10
- **CloudWatch Alarms**: DLQ messages >0, queue age >5 minutes

### Bouncer Lambda Function

The bouncer lambda (`ses-mail-bouncer-{environment}`) processes enriched email messages from the bouncer SQS queue and sends bounce notification emails via SES.

**Functionality:**

- **SQS Event Processing**: Triggered by messages in the bouncer queue
- **Enriched Message Handling**: Extracts routing decisions and email metadata from EventBridge-enriched messages
- **Bounce Notification**: Sends formatted bounce emails via SES to the original sender
- **Email Metadata**: Includes original sender, recipient, subject, timestamp, and routing rule information in bounce
- **X-Ray Tracing**: Active tracing with custom annotations for message ID, source, action, and environment
- **Error Handling**: Returns batch item failures for SQS retry logic
- **Professional Formatting**: Sends both HTML and plain text bounce messages

**Input Format** (from SQS/EventBridge):

The lambda receives SQS messages containing EventBridge events with the router's enriched data:

```json
{
  "Records": [{
    "body": "{\"detail\": {\"originalMessageId\": \"abc123\", \"body\": \"{...SES event JSON...}\", \"actions\": {\"bounce\": {\"count\": 1, \"targets\": [{\"target\": \"recipient@domain.com\"}]}}}}"
  }]
}
```

**Processing Flow:**

1. Parse SQS message body to extract EventBridge event detail
2. Extract `originalMessageId` and `actions.bounce.targets` array
3. Parse SES event from `body` field to get email metadata (subject, source, timestamp)
4. For each target in targets array, send a bounce notification via SES
5. Bounce email includes original message details
6. Return success or batch item failure for SQS retry

**Bounce Email Format:**

The bounce notification includes:

- Subject: `Mail Delivery Failed: {original subject}`
- Original message details (from, to, subject, timestamp)
- Reason for bounce (recipient not configured)
- Routing rule that triggered the bounce
- Professional HTML and plain text formatting

**Configuration:**

- **Runtime**: Python 3.12
- **Memory**: 128MB
- **Timeout**: 30 seconds (for SES API calls)
- **Environment Variables**:
  - `BOUNCE_SENDER`: Sender address for bounce notifications (e.g., `mailer-daemon@domain.com`)
  - `ENVIRONMENT`: Environment name (test/prod)

**Testing the Bouncer Lambda:**

```bash
# Create a test SQS message with enriched data
cat > test_bounce_message.json <<'EOF'
{
  "Records": [{
    "messageId": "test-msg-1",
    "body": "{\"originalEvent\": {\"eventSource\": \"aws:ses\", \"ses\": {\"mail\": {\"messageId\": \"abc123\", \"source\": \"sender@example.com\", \"destination\": [\"recipient@testmail.domain.com\"]}, \"receipt\": {\"spamVerdict\": {\"status\": \"PASS\"}, \"virusVerdict\": {\"status\": \"PASS\"}}}}, \"routingDecisions\": [{\"recipient\": \"recipient@testmail.domain.com\", \"normalizedRecipient\": \"recipient@testmail.domain.com\", \"action\": \"bounce\", \"target\": \"\", \"matchedRule\": \"ROUTE#*\", \"ruleDescription\": \"Default: bounce all unmatched emails\"}], \"emailMetadata\": {\"messageId\": \"abc123\", \"source\": \"sender@example.com\", \"subject\": \"Test Email\", \"timestamp\": \"2025-01-18T10:00:00Z\", \"securityVerdict\": {\"spam\": \"PASS\", \"virus\": \"PASS\"}}}"
  }]
}
EOF

# Test the lambda function
AWS_PROFILE=ses-mail aws lambda invoke \
  --function-name ses-mail-bouncer-test \
  --cli-binary-format raw-in-base64-out \
  --payload file://test_bounce_message.json \
  response.json

# View the response
cat response.json

# View logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-bouncer-test --follow
```

**SQS Queue Configuration:**

- **Queue Name**: `ses-bouncer-{environment}`
- **Dead Letter Queue**: `ses-bouncer-dlq-{environment}` (14 day retention)
- **Visibility Timeout**: 180 seconds (6x lambda timeout)
- **Max Retries**: 3 (before moving to DLQ)
- **Event Source Mapping**: Batch size 1, max concurrency 5
- **CloudWatch Alarms**: DLQ messages >0, queue age >5 minutes

**Important Notes:**

- SES sandbox mode requires sender email verification. In production with verified domain, bounces will be sent to any address.
- Bounce sender defaults to `mailer-daemon@{domain}` using the first domain from configuration.

## Monitoring and Alerting

The system includes comprehensive monitoring and alerting infrastructure built on CloudWatch to track email processing, lambda performance, and system health.

### CloudWatch Dashboard

A comprehensive CloudWatch dashboard (`ses-mail-dashboard-{environment}`) provides real-time visibility into system operations:

**Accessing the Dashboard:**

```bash
# Via AWS Console:
# CloudWatch → Dashboards → ses-mail-dashboard-test (or prod)

# Or get the direct URL via:
echo "https://console.aws.amazon.com/cloudwatch/home?region=ap-southeast-2#dashboards:name=ses-mail-dashboard-test"
```

**Dashboard Widgets:**

1. **Email Processing Overview** - Total emails accepted, spam detected, virus detected
2. **Handler Success/Failure Rates** - Custom metrics showing success/failure counts for:
   - Router enrichment operations
   - Gmail forwarding operations
   - Bounce sending operations
3. **Lambda Function Errors** - Error counts for all lambda functions (processor, router, gmail forwarder, bouncer)
4. **Lambda Function Invocations** - Invocation counts for all lambda functions
5. **SQS Queue Depths** - Current message counts in input, gmail-forwarder, and bouncer queues
6. **Dead Letter Queue Messages** - DLQ message counts (should normally be 0)
7. **Lambda Duration** - Average execution times for router, gmail forwarder, and bouncer lambdas
8. **Recent Email Logs** - CloudWatch Logs Insights query showing recent processed emails

### Custom Metrics

The lambda functions publish custom CloudWatch metrics to the `SESMail/{environment}` namespace for tracking operation success/failure rates:

**Router Enrichment Metrics:**

- `RouterEnrichmentSuccess` - Count of successfully enriched messages
- `RouterEnrichmentFailure` - Count of failed enrichments (using fallback routing)

**Gmail Forwarder Metrics:**

- `GmailForwardSuccess` - Count of successful Gmail imports
- `GmailForwardFailure` - Count of failed Gmail imports

**Bouncer Metrics:**

- `BounceSendSuccess` - Count of successful bounce notifications sent
- `BounceSendFailure` - Count of failed bounce notifications

**Querying Custom Metrics:**

```bash
# Get router enrichment success count for last hour
AWS_PROFILE=ses-mail aws cloudwatch get-metric-statistics \
  --namespace "SESMail/test" \
  --metric-name RouterEnrichmentSuccess \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Sum

# Get Gmail forwarding failure count
AWS_PROFILE=ses-mail aws cloudwatch get-metric-statistics \
  --namespace "SESMail/test" \
  --metric-name GmailForwardFailure \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Sum
```

### CloudWatch Alarms

The system includes CloudWatch alarms that trigger when operational thresholds are exceeded. All alarms publish to the SNS topic specified in `terraform.tfvars` for notifications.

**Dead Letter Queue Alarms:**

- `ses-email-input-dlq-messages-{environment}` - Input queue DLQ has messages
- `ses-gmail-forwarder-dlq-messages-{environment}` - Gmail forwarder DLQ has messages
- `ses-bouncer-dlq-messages-{environment}` - Bouncer DLQ has messages

**Queue Age Alarms:**

- `ses-email-input-queue-age-{environment}` - Messages aging >5 minutes in input queue
- `ses-gmail-forwarder-queue-age-{environment}` - Messages aging >5 minutes in Gmail queue
- `ses-bouncer-queue-age-{environment}` - Messages aging >5 minutes in bouncer queue

**Lambda Error Alarms:**

- `ses-mail-lambda-errors-{environment}` - Email processor lambda has >5 errors in 5 minutes
- `ses-mail-lambda-router-errors-{environment}` - Router enrichment lambda has >5 errors in 5 minutes
- `ses-mail-lambda-gmail-forwarder-errors-{environment}` - Gmail forwarder lambda has >5 errors in 5 minutes
- `ses-mail-lambda-bouncer-errors-{environment}` - Bouncer lambda has >5 errors in 5 minutes

**Email Processing Alarms:**

- `ses-mail-high-email-volume-{environment}` - More than 100 emails in 5 minutes
- `ses-mail-high-spam-rate-{environment}` - Spam rate >10% in 5 minutes

**EventBridge Alarms:**

- `eventbridge-pipes-failures-{environment}` - EventBridge Pipes enrichment failures
- `eventbridge-gmail-failures-{environment}` - EventBridge failed to deliver to Gmail queue
- `eventbridge-bouncer-failures-{environment}` - EventBridge failed to deliver to bouncer queue

**Viewing Alarm Status:**

```bash
# List all alarms for the test environment
AWS_PROFILE=ses-mail aws cloudwatch describe-alarms \
  --alarm-name-prefix ses- \
  --region ap-southeast-2

# Get specific alarm details
AWS_PROFILE=ses-mail aws cloudwatch describe-alarms \
  --alarm-names ses-gmail-forwarder-dlq-messages-test
```

### X-Ray Distributed Tracing

The entire email processing pipeline is instrumented with AWS X-Ray for end-to-end request tracing:

**Trace Components:**

- SNS topic initiates traces with Active tracing mode
- SQS queues propagate trace context through the pipeline
- EventBridge Pipes maintains trace context during enrichment
- Router lambda adds custom annotations (messageId, source, recipient, action)
- Handler lambdas (Gmail forwarder, bouncer) continue the trace with operation-specific annotations

**Viewing Traces:**

```bash
# Via AWS Console:
# X-Ray → Traces → Filter by service "ses-mail-router-enrichment-test"

# Or view service map:
# X-Ray → Service map → Select time range
```

**X-Ray Annotations:**

Router enrichment lambda annotations:

- `messageId` - SES message ID
- `source` - Email sender address
- `recipient` - Email recipient address
- `action` - Routing action (forward-to-gmail or bounce)

Gmail forwarder lambda annotations:

- `action` - forward-to-gmail
- `recipient` - Original recipient address
- `target` - Gmail target address
- `gmail_message_id` - Gmail message ID after import
- `import_status` - success or error

Bouncer lambda annotations:

- `messageId` - SES message ID
- `source` - Email sender address
- `environment` - Environment name (test/prod)
- `action` - bounce

### CloudWatch Logs

All lambda functions log to CloudWatch Logs with 30-day retention:

**Log Groups:**

- `/aws/lambda/ses-mail-email-processor-{environment}` - Email processor logs
- `/aws/lambda/ses-mail-router-enrichment-{environment}` - Router enrichment logs
- `/aws/lambda/ses-mail-gmail-forwarder-{environment}` - Gmail forwarder logs
- `/aws/lambda/ses-mail-bouncer-{environment}` - Bouncer logs
- `/aws/events/ses-email-routing-{environment}` - EventBridge Event Bus logs
- `/aws/vendedlogs/pipes/{environment}/ses-email-router` - EventBridge Pipes logs

**Viewing Logs:**

```bash
# Tail router enrichment logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow

# Tail Gmail forwarder logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-gmail-forwarder-test --follow

# Tail bouncer logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-bouncer-test --follow

# Query logs with CloudWatch Logs Insights
AWS_PROFILE=ses-mail aws logs start-query \
  --log-group-name /aws/lambda/ses-mail-router-enrichment-test \
  --start-time $(date -u -v-1H +%s) \
  --end-time $(date -u +%s) \
  --query-string 'fields @timestamp, @message | filter @message like /ERROR/ | sort @timestamp desc'
```

### CloudWatch Logs Insights Saved Queries

The system includes pre-configured CloudWatch Logs Insights queries for common troubleshooting scenarios. Access them via:

```bash
# Navigate to: CloudWatch → Logs → Logs Insights
# Select "Saved queries" and filter by "ses-mail/{environment}/"
```

**Available Saved Queries**:

1. **router-enrichment-errors** - Find router lambda errors with message details
2. **gmail-forwarder-failures** - Investigate Gmail forwarding failures
3. **bouncer-failures** - Debug bounce sending issues
4. **routing-decision-analysis** - Analyze routing decisions by action and matched rule
5. **email-end-to-end-trace** - Trace a specific email through the entire pipeline
6. **dlq-message-investigation** - Find failed messages and retry attempts
7. **performance-analysis** - Analyze lambda execution times across all functions

These queries are automatically created by Terraform and available immediately after deployment.

### Systems Manager Automation Runbooks

The system includes AWS Systems Manager automation runbooks for operational tasks:

#### SESMail-DLQ-Redrive-{environment}

Automatically redrive messages from dead letter queues back to source queues with velocity control:

```bash
# Via AWS Console:
# Systems Manager → Automation → Execute automation
# Select: SESMail-DLQ-Redrive-{environment}

# Via CLI:
AWS_PROFILE=ses-mail aws ssm start-automation-execution \
  --document-name "SESMail-DLQ-Redrive-test" \
  --parameters \
    "DLQUrl=https://sqs.ap-southeast-2.amazonaws.com/{account}/ses-gmail-forwarder-dlq-test,\
     SourceQueueUrl=https://sqs.ap-southeast-2.amazonaws.com/{account}/ses-gmail-forwarder-test,\
     MaxMessages=0,\
     VelocityPerSecond=10"
```

#### SESMail-Queue-HealthCheck-{environment}

Check the health of all queues and DLQs:

```bash
AWS_PROFILE=ses-mail aws ssm start-automation-execution \
  --document-name "SESMail-Queue-HealthCheck-test"
```

**For detailed operational procedures, incident response, and troubleshooting, see [OPERATIONS.md](OPERATIONS.md).**

### Monitoring Best Practices

1. **Set up SNS notifications**: Configure the `alarm_sns_topic_arn` in `terraform.tfvars` to receive alarm notifications via email or SMS

2. **Monitor DLQ alarms**: Dead letter queue messages indicate persistent failures that require investigation - see [OPERATIONS.md](OPERATIONS.md) for DLQ handling procedures

3. **Track custom metrics**: Review handler success/failure rates daily to identify trends

4. **Use X-Ray for debugging**: When issues occur, use X-Ray traces to identify bottlenecks and failures across the pipeline

5. **Review CloudWatch dashboard**: Check the dashboard regularly to ensure healthy operation

6. **Use saved queries**: Leverage CloudWatch Logs Insights saved queries for faster troubleshooting

7. **Run health checks**: Use the Systems Manager queue health check runbook for regular health monitoring

8. **Enable detailed monitoring**: For production environments, consider enabling detailed (1-minute) CloudWatch metrics for faster alerting

## SMTP Credential Management

The system provides automated SMTP credential management for outbound email sending through SES. This allows users to configure email clients (Gmail, Outlook, Thunderbird) to send emails through AWS SES SMTP.

### How It Works

The credential management system uses an event-driven architecture:

1. **Manual DynamoDB Record Creation**: Administrator manually inserts SMTP credential record with `status="pending"`
2. **DynamoDB Streams Trigger**: Stream detects new record and triggers credential manager Lambda
3. **Automated IAM Provisioning**: Lambda creates IAM user, generates access keys, applies email restrictions
4. **SMTP Password Conversion**: Secret access key is converted to SES SMTP password using AWS algorithm
5. **KMS Encryption**: Credentials are encrypted with customer managed KMS key
6. **DynamoDB Update**: Encrypted credentials stored in DynamoDB with `status="active"`

### SMTP Endpoint Configuration

Get your SES SMTP endpoint configuration from Terraform outputs:

```bash
# For test environment
cd terraform/environments/test
terraform output smtp_endpoint
terraform output smtp_ports

# Example output:
# smtp_endpoint = "email-smtp.ap-southeast-2.amazonaws.com"
# smtp_ports = {
#   recommended = {
#     port     = 587
#     security = "STARTTLS"
#     note     = "Recommended for most email clients"
#   }
# }
```

**SMTP Server Settings**:
- **Host**: `email-smtp.{region}.amazonaws.com` (from terraform output)
- **Port**: 587 (recommended - STARTTLS)
- **Security**: STARTTLS
- **Authentication**: Username/Password (SMTP credentials from DynamoDB)

### Creating SMTP Credentials

To create new SMTP credentials, manually insert a record into the DynamoDB table:

```bash
# Create SMTP credential record for a user
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-email-routing-test \
  --item '{
    "PK": {"S": "SMTP_USER"},
    "SK": {"S": "USER#john.doe"},
    "status": {"S": "pending"},
    "description": {"S": "John Doe email sending access"},
    "allowed_from_addresses": {"L": [
      {"S": "john.doe@example.com"},
      {"S": "*@marketing.example.com"}
    ]},
    "entity_type": {"S": "smtp_credential"}
  }'
```

**Required Fields**:
- `PK`: Always `"SMTP_USER"` (partition key for SMTP credential records)
- `SK`: `"USER#{username}"` (unique identifier, e.g., `USER#john.doe`)
- `status`: `"pending"` (triggers Lambda processing)
- `description`: Human-readable description of the credential
- `allowed_from_addresses`: Array of email patterns the user can send from (e.g., `["user@domain.com", "*@domain.com"]`)
- `entity_type`: `"smtp_credential"` (record type identifier)

**Email Restriction Patterns**:
- Exact address: `"john.doe@example.com"` (only this specific address)
- Domain wildcard: `"*@marketing.example.com"` (any address at this domain)
- Global wildcard: `"*"` (any address - use with caution)

### Retrieving SMTP Credentials

After the Lambda processes the record (usually within 1-2 seconds), retrieve the encrypted credentials:

```bash
# Get SMTP credential record
AWS_PROFILE=ses-mail aws dynamodb get-item \
  --table-name ses-email-routing-test \
  --key '{"PK": {"S": "SMTP_USER"}, "SK": {"S": "USER#john.doe"}}' \
  --output json

# The response will include:
# - status: "active" (processing complete)
# - encrypted_password: Base64-encoded encrypted credentials
# - iam_user_arn: ARN of created IAM user
# - created_at: Timestamp of credential creation
```

**Important**: The `encrypted_password` field contains KMS-encrypted credentials as a base64-encoded JSON blob with `access_key_id` and `smtp_password`. Only authorized administrators with KMS decrypt permissions can decrypt these credentials.

### Decrypting SMTP Credentials

To decrypt and use the SMTP credentials:

```bash
# Step 1: Get the encrypted password from DynamoDB
ENCRYPTED_PASSWORD=$(AWS_PROFILE=ses-mail aws dynamodb get-item \
  --table-name ses-email-routing-test \
  --key '{"PK": {"S": "SMTP_USER"}, "SK": {"S": "USER#john.doe"}}' \
  --query 'Item.encrypted_password.S' \
  --output text)

# Step 2: Decrypt using KMS
AWS_PROFILE=ses-mail aws kms decrypt \
  --key-id alias/ses-mail-smtp-credentials-test \
  --ciphertext-blob fileb://<(echo "$ENCRYPTED_PASSWORD" | base64 -d) \
  --query 'Plaintext' \
  --output text | base64 -d

# Output will be JSON with access_key_id and smtp_password:
# {"access_key_id": "AKIA...", "smtp_password": "BHdSG..."}
```

**Security Note**: Only decrypt credentials when needed and never store plaintext credentials. Distribute credentials securely to end users through secure channels.

### Configuring Email Clients

Once you have the SMTP credentials (username = `access_key_id`, password = `smtp_password`), configure your email client:

#### Gmail Configuration

1. **Gmail Settings** → **Accounts and Import** → **Send mail as** → **Add another email address**
2. **Name**: Your name as it will appear in sent emails
3. **Email address**: One of your `allowed_from_addresses` (e.g., `john.doe@example.com`)
4. **Next** → **SMTP Server Settings**:
   - SMTP Server: `email-smtp.ap-southeast-2.amazonaws.com` (from terraform output)
   - Port: `587`
   - Username: Your `access_key_id` from decrypted credentials
   - Password: Your `smtp_password` from decrypted credentials
   - Security: TLS (STARTTLS)
5. **Add Account** → Verify email address if required

#### Outlook Configuration

1. **File** → **Add Account** → **Manual setup**
2. **Account Type**: IMAP or POP (for receiving) + SMTP (for sending)
3. **Outgoing Mail Server (SMTP)**:
   - Server: `email-smtp.ap-southeast-2.amazonaws.com`
   - Port: `587`
   - Encryption: STARTTLS
   - Authentication: Required
   - Username: Your `access_key_id`
   - Password: Your `smtp_password`

#### Thunderbird Configuration

1. **Tools** → **Account Settings** → **Outgoing Server (SMTP)** → **Add**
2. **Server Name**: `email-smtp.ap-southeast-2.amazonaws.com`
3. **Port**: `587`
4. **Connection security**: STARTTLS
5. **Authentication method**: Normal password
6. **Username**: Your `access_key_id`
7. **Password**: Your `smtp_password` (will be stored securely)

### Monitoring SMTP Credential Usage

Monitor credential creation and usage through CloudWatch:

```bash
# Check CloudWatch metrics for credential operations
AWS_PROFILE=ses-mail aws cloudwatch get-metric-statistics \
  --namespace "SESMail/test" \
  --metric-name SMTPCredentialCreations \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Sum

# View credential manager Lambda logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-smtp-credential-manager-test --follow
```

### Disabling or Deleting SMTP Credentials

**To temporarily disable credentials**:

```bash
# Update status to disabled (IAM user access keys will be deactivated)
AWS_PROFILE=ses-mail aws dynamodb update-item \
  --table-name ses-email-routing-test \
  --key '{"PK": {"S": "SMTP_USER"}, "SK": {"S": "USER#john.doe"}}' \
  --update-expression "SET #status = :disabled" \
  --expression-attribute-names '{"#status": "status"}' \
  --expression-attribute-values '{":disabled": {"S": "disabled"}}'
```

**To permanently delete credentials**:

```bash
# Delete record from DynamoDB (triggers automatic IAM cleanup)
AWS_PROFILE=ses-mail aws dynamodb delete-item \
  --table-name ses-email-routing-test \
  --key '{"PK": {"S": "SMTP_USER"}, "SK": {"S": "USER#john.doe"}}'
```

When a credential record is deleted, the Lambda function automatically:
1. Detects the REMOVE event via DynamoDB Streams
2. Lists and deletes all IAM access keys for the user
3. Lists and deletes all inline IAM policies
4. Deletes the IAM user completely
5. Publishes CloudWatch metrics for the deletion operation

This ensures no orphaned IAM resources remain after credential deletion.

### DNS SPF Record Configuration

To authorize SES to send emails on behalf of your domain, add an SPF record to your DNS. The SPF record is included in the `dns_configuration_summary` output and also available as a separate `spf_record` output.

```bash
# Get SPF record recommendation from Terraform
cd terraform/environments/test
terraform output spf_record

# Example output for testmail.domain.com:
# {
#   "testmail.domain.com" = {
#     "name"    = "testmail.domain.com"
#     "type"    = "TXT"
#     "value"   = "v=spf1 include:amazonses.com ~all"
#     "purpose" = "Authorize SES to send emails on behalf of testmail.domain.com"
#     "note"    = "Soft fail (~all) - unauthorized senders marked as suspicious but accepted"
#   }
# }
```

**Configuring SPF Policy** (in `terraform.tfvars`):

```hcl
# SPF policy: "softfail" (~all) for testing, "fail" (-all) for production
spf_policy = "softfail"  # Default - recommended during testing

# For production, use hard fail after confirming SPF works correctly:
# spf_policy = "fail"

# Add specific mail server A records
spf_a_records = [
  "srv2.rrod.net",       # Authorize IP addresses from srv2.rrod.net A record
  "mail-out.rrod.net"    # Authorize IP addresses from mail-out.rrod.net A record
]

# Add additional SPF includes for other email services
spf_include_domains = [
  "outbound.mailhop.org",           # Mail relay service
  "_spf.google.com",                # Google Workspace
  "spf.protection.outlook.com"      # Microsoft 365
]
```

**SPF Mechanism Order**:
The SPF record is built in this order:
1. `v=spf1` - SPF version identifier
2. `a:hostname` - A records (authorize IPs from these hostnames' A records)
3. `mx:hostname` - MX records (authorize IPs from these hostnames' MX records - only use if the MX server also SENDS email)
4. `include:amazonses.com` - SES (always included)
5. `include:domain` - Additional includes (other email services)
6. `-all` or `~all` - Policy for unauthorized senders

**Important**: SPF is for authorizing who can SEND email on behalf of your domain. Don't add backup MX servers to SPF unless they also send email. Use the `backup_mx_records` configuration below for email receiving.

**SPF Policy Explanation**:
- `~all` (soft fail) - Default setting. Tells receiving servers "if the email doesn't match SPF, mark it as suspicious but still accept it"
  - **Use for**: Testing, initial setup, or when you have other email services not included in SPF
  - **Behavior**: Emails from unauthorized sources are typically delivered to spam/junk folders

- `-all` (hard fail) - Strict setting. Tells receiving servers "if the email doesn't match SPF, reject it completely"
  - **Use for**: Production environments after confirming all legitimate email sources are included
  - **Behavior**: Emails from unauthorized sources are rejected and not delivered
  - **Warning**: Test thoroughly before using - misconfigured SPF with `-all` can cause legitimate emails to be rejected

**The SPF record is automatically included in `dns_configuration_summary`** and will appear in the consolidated DNS records list when you run `terraform output dns_configuration_summary`.

### Backup MX Configuration

To configure backup MX servers for email receiving (failover), add them to `terraform.tfvars`:

```hcl
# Backup MX records for email receiving (not SPF)
backup_mx_records = [
  {
    hostname = "mail-in.rrod.net"
    priority = 20  # Higher number = lower preference (SES is priority 10)
  }
]
```

**MX Priority Explained**:
- Lower priority number = higher preference (tried first)
- SES primary MX has priority 10
- Backup MX should have priority > 10 (e.g., 20, 30, etc.)
- Email servers try MX records in order of priority
- If primary (SES) is unavailable, mail is delivered to backup

**Note**: Backup MX servers are for RECEIVING email only. They should NOT be added to the SPF record unless they also send email on behalf of your domain.

The backup MX records will automatically:
- Appear in the `dns_configuration_summary` output alongside the primary SES MX record
- Be included in the MTA-STS policy file (if MTA-STS is enabled) to authorize them for TLS email delivery

### Troubleshooting SMTP Issues

**Authentication Failures**:
- Verify SMTP credentials are correct (username = `access_key_id`, password = `smtp_password`)
- Check IAM user status in DynamoDB (`status` should be `"active"`)
- Verify IAM user exists and has active access keys
- Check credential manager Lambda logs for creation errors

**Email Sending Blocked**:
- Verify sender address matches one of the `allowed_from_addresses` patterns
- Check IAM policy attached to user restricts `ses:FromAddress` correctly
- Verify SES is out of sandbox mode (or recipient is verified in sandbox)
- Check CloudWatch Logs for SES rejection errors

**DNS/Deliverability Issues**:
- Verify SPF record is properly configured in DNS
- Ensure DKIM records are added and verified in SES
- Check DMARC policy is configured
- Monitor bounce and complaint rates in CloudWatch

**Lambda Processing Failures**:
- Check DLQ for failed credential creation events (Task 4 implementation)
- Review credential manager Lambda CloudWatch Logs
- Verify KMS key permissions allow encryption/decryption
- Check DynamoDB Streams are enabled and Lambda has read permissions

## Integration Testing

The system includes comprehensive integration tests that validate the entire email processing pipeline end-to-end, from SES receipt through to final handler processing (Gmail forwarding or bouncing).

### Prerequisites

Before running integration tests, ensure:

1. **AWS Profile**: `AWS_PROFILE` environment variable is set to `ses-mail`
2. **SES Verification**: Sender email address is verified in SES (for test environment sandbox)
3. **Test Domain**: Test domain is configured in SES receipt rules
4. **Gmail Token**: Gmail OAuth token is configured in SSM Parameter Store
5. **Infrastructure**: All infrastructure is deployed and healthy
6. **DynamoDB Rules**: Test routing rules will be created automatically (and cleaned up after tests)

### Running Integration Tests

The integration test script (`scripts/integration_test.py`) sends test emails through the complete pipeline and verifies each stage:

```bash
# Run all integration tests for test environment
AWS_PROFILE=ses-mail ./scripts/integration_test.py \
  --env test \
  --from sender@testmail.domain.com \
  --test-domain testmail.domain.com \
  --gmail-target your-email@gmail.com

# Run with verbose logging
AWS_PROFILE=ses-mail ./scripts/integration_test.py \
  --env test \
  --from sender@testmail.domain.com \
  --test-domain testmail.domain.com \
  --gmail-target your-email@gmail.com \
  --verbose

# Skip cleanup of test routing rules (for debugging)
AWS_PROFILE=ses-mail ./scripts/integration_test.py \
  --env test \
  --from sender@testmail.domain.com \
  --test-domain testmail.domain.com \
  --gmail-target your-email@gmail.com \
  --skip-cleanup
```

### Test Coverage

The integration tests validate:

1. **Forward to Gmail Test**:
   - Creates routing rule: `test-forward@domain → forward-to-gmail → gmail-target`
   - Sends test email through SES
   - Verifies message progression through pipeline stages:
     - SES → S3 → SNS → SQS Input Queue
     - EventBridge Pipes → Router Lambda (DynamoDB lookup)
     - EventBridge Event Bus → Gmail Forwarder Queue
     - Gmail Forwarder Lambda → Gmail API
   - Validates X-Ray trace spans across all components
   - Checks no messages in dead letter queues

2. **Bounce Test**:
   - Creates routing rule: `test-bounce@domain → bounce`
   - Sends test email through SES
   - Verifies message routing to bouncer queue
   - Validates bounce notification sent via SES
   - Confirms X-Ray tracing

3. **X-Ray Trace Verification**:
   - Waits for X-Ray trace to become available (30-60 second delay)
   - Verifies trace contains expected segments:
     - SNS topic (trace initiation)
     - SQS queues (trace propagation)
     - EventBridge Pipes (enrichment)
     - Router Lambda (routing decisions)
     - EventBridge Event Bus (routing)
     - Handler Queues (SQS)
     - Handler Lambdas (processing)
   - Validates custom annotations (messageId, source, action)

4. **Pipeline Monitoring**:
   - Monitors CloudWatch Logs for router enrichment decisions
   - Checks SQS queue depths at each stage
   - Verifies no errors in lambda function logs
   - Validates dead letter queue remains empty

### Test Output

The integration test script provides detailed output during execution:

```plain
==============================================================
Test: Forward to Gmail
==============================================================
Step 1: Creating test routing rule...
Step 2: Sending test email...
Step 3: Checking input queue...
Step 4: Waiting for router enrichment...
Step 5: Checking Gmail forwarder queue...
Step 6: Checking dead letter queues...
Step 7: Retrieving X-Ray trace...

==============================================================
INTEGRATION TEST REPORT
==============================================================
Environment: test
Timestamp: 2025-01-19T10:30:00Z
Total Tests: 2
Passed: 2
Failed: 0
==============================================================

Test: Forward to Gmail
Status: PASS
Details:
  - routing_rule_created: True
  - message_id: abc123def456
  - email_sent: True
  - input_queue_received: True
  - router_processed: True
  - routing_decision: forward-to-gmail
  - gmail_queue_received: True
  - dlq_messages: 0
  - xray_trace_found: True
  - trace_id: 1-507f191e810c19729de860ea-1a2b3c4d
  - trace_segments: {...}

Test: Bounce Email
Status: PASS
Details:
  - routing_rule_created: True
  - message_id: xyz789uvw012
  - email_sent: True
  - router_processed: True
  - routing_decision: bounce
  - bouncer_queue_received: True
  - dlq_messages: 0
  - xray_trace_found: True
  - trace_id: 1-507f191e810c19729de860ea-5e6f7g8h

==============================================================
Detailed report saved to: integration_test_report_test_1705660200.json
```

### Test Configuration

Example test configuration file (`scripts/test_config.json`):

```json
{
  "environments": {
    "test": {
      "from_address": "sender@testmail.domain.com",
      "test_domain": "testmail.domain.com",
      "gmail_target": "your-gmail@gmail.com",
      "timeout_settings": {
        "queue_wait": 60,
        "xray_trace": 120,
        "pipeline_processing": 30
      }
    }
  }
}
```

### Troubleshooting Test Failures

**Test fails with "Message not found in input queue":**

- Check SES receipt rule is active and publishing to SNS
- Verify SNS topic subscription to SQS input queue
- Check CloudWatch Logs for SES receipt errors
- Verify sender email is verified in SES (sandbox mode)

**Test fails with "Router logs not found":**

- Check EventBridge Pipes is active and invoking router lambda
- Verify router lambda has permissions to read DynamoDB
- Check router lambda CloudWatch Logs for errors
- Verify DynamoDB routing table exists and is accessible

**Test fails with "Message not found in handler queue":**

- Check EventBridge Event Bus rules are active
- Verify event pattern matching in EventBridge rules
- Check router enrichment added correct routing decision
- Verify EventBridge has permissions to send to SQS queues

**Test fails with "X-Ray trace not found":**

- Wait longer (X-Ray traces can take 60-90 seconds)
- Check SNS topic has Active tracing enabled
- Verify all lambda functions have X-Ray tracing enabled
- Check SQS queues have X-Ray tracing enabled
- Verify EventBridge Pipes propagates trace context

**Messages found in dead letter queues:**

- Check handler lambda CloudWatch Logs for errors
- Verify Gmail OAuth token is valid and not expired
- For bouncer: verify SES sending permissions and bounce sender email
- Review DLQ messages to identify root cause
- Use Systems Manager runbook to redrive messages after fixing issue

### Best Practices

1. **Run tests after deployments**: Always run integration tests after infrastructure changes
2. **Monitor test results**: Track test execution times to identify performance regressions
3. **Check X-Ray traces**: Review X-Ray service map to visualize complete pipeline
4. **Clean up test data**: Tests automatically clean up routing rules unless `--skip-cleanup` is used
5. **Use test environment**: Never run integration tests in production without careful planning
6. **Verify Gmail import**: For forward tests, check Gmail account to confirm email was imported
7. **Review bounce emails**: For bounce tests, check sender's inbox for bounce notification

### Advanced Testing Scenarios

For additional test scenarios not covered by the basic integration tests:

**Plus Addressing Normalization:**

```bash
# Manually create routing rule for normalized address
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#test@testmail.domain.com"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "test@testmail.domain.com"},
    "action": {"S": "forward-to-gmail"},
    "target": {"S": "your-gmail@gmail.com"},
    "enabled": {"BOOL": true},
    "created_at": {"S": "2025-01-19T10:00:00Z"},
    "updated_at": {"S": "2025-01-19T10:00:00Z"},
    "description": {"S": "Test plus addressing normalization"}
  }'

# Send email to test+tag@testmail.domain.com
# Router should normalize to test@testmail.domain.com and match rule
```

**Domain Wildcard Matching:**

```bash
# Create domain wildcard rule
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#*@testmail.domain.com"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "*@testmail.domain.com"},
    "action": {"S": "forward-to-gmail"},
    "target": {"S": "your-gmail@gmail.com"},
    "enabled": {"BOOL": true},
    "created_at": {"S": "2025-01-19T10:00:00Z"},
    "updated_at": {"S": "2025-01-19T10:00:00Z"},
    "description": {"S": "Catch-all for domain"}
  }'

# Send email to any-address@testmail.domain.com
# Should match wildcard rule
```

**Global Wildcard (Default Rule):**

```bash
# Create global wildcard rule
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#*"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "*"},
    "action": {"S": "bounce"},
    "target": {"S": ""},
    "enabled": {"BOOL": true},
    "created_at": {"S": "2025-01-19T10:00:00Z"},
    "updated_at": {"S": "2025-01-19T10:00:00Z"},
    "description": {"S": "Default: bounce all unmatched emails"}
  }'
```

**Error Scenario Testing:**

```bash
# Test DynamoDB unavailable (temporarily remove permissions)
# Expected: Router should use fallback routing (bounce)

# Test Gmail API failure (use invalid OAuth token)
# Expected: Message should retry and eventually go to DLQ

# Test Bouncer SES failure (use unverified sender)
# Expected: Message should retry and eventually go to DLQ
```
