# SES Mail System

## Initial Google Cloud Setup

* Go to <https://console.cloud.google.com>
* Click the current project (top left next to "Google Cloud")
* Click "New Project"
* Name it "ses-mail"
* Leave the organisation as "No organisation"
* Create
* ☰ -> APIs and services -> Library
* Search for "Gmail API"
* Select "Gmail API"
* Click "Enable"
* Select "OAuth consent screen" in the left-side menu
* It will say "Google auth platform not configured yet" - Click "Get started"
* App name: ses-mail
* User support email: select your email address
* Next
* Select External
* Next
* Add your email address for the contact address
* Next
* Agree to the policy
* Continue
* Create
* Audience -> Add users
* Add your gmail address
* Save
* Clients -> Create client
* Select "Create OAuth client"
* Application type: Desktop app
* Name: ses-mail
* Create
* Select "Download JSON" - and save it for later
* OK
* From the left menu -> Data Access -> Add or remove scopes
* Add "Gmail API .../auth/gmail.insert"
* Update
* Save
* Put the client secret file in this directory, named "client_secret.json"
* `python3 -m venv .venv`
* Either source the file mentioned, or let VSCode handle it and create a new terminal
* `pip3 install -r requirements.txt`
* `./scripts/create_refresh_token.py`
* In the browser window that pops up, select your account
* Continue
* Continue
* Close the tab
* You can use the scripts in `scripts/` to test the token
* After setting up the infrastructure, set the token parameter to the value of token.json
* Delete the `client_secret.json` and `token.json` files from the local filesystem

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

2. Review and customize the configuration for your environment:

   ```bash
   # For test environment
   vi terraform/environments/test/terraform.tfvars

   # For production environment
   vi terraform/environments/prod/terraform.tfvars

   # Update domain(s) and SNS topic ARN as needed
   # domain should be a list: ["mail.example.com", "mail2.example.com"]
   ```

3. Deploy the infrastructure (first pass):

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

4. Configure DNS records in Route53:

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

   * Name: `_amazonses.YOUR_DOMAIN`
   * Type: TXT
   * Value: The verification token from the output
   * TTL: 1800 (or default)

   **Email Receiving (MX record)**

   * Name: `YOUR_DOMAIN` (or leave blank if zone is YOUR_DOMAIN)
   * Type: MX
   * Value: `10 inbound-smtp.ap-southeast-2.amazonaws.com` (adjust region as needed)
   * TTL: 1800 (or default)

   **DKIM Authentication (3 CNAME records per domain)**

   * For each of the 3 DKIM tokens in the output:
     * Name: `{token}._domainkey.YOUR_DOMAIN`
     * Type: CNAME
     * Value: `{token}.dkim.amazonses.com`
     * TTL: 1800 (or default)

   **DMARC Policy (TXT record per domain)**

   * Name: `_dmarc.YOUR_DOMAIN`
   * Type: TXT
   * Value: `v=DMARC1; p=reject; rua=mailto:dmarc@YOUR_DOMAIN` (if prefix configured)
   * TTL: 1800 (or default)
   * Purpose: Prevents others from spoofing your domain

   **MTA-STS (if enabled - records per domain)**

   * Name: `_mta-sts.YOUR_DOMAIN`
   * Type: TXT
   * Value: From terraform output (contains policy ID)
   * TTL: 1800 (or default)

   * Name: `mta-sts.YOUR_DOMAIN`
   * Type: CNAME
   * Value: CloudFront distribution URL from output
   * TTL: 1800 (or default)

   * ACM validation CNAME records (from terraform output, one set per domain)

   **TLS Reporting (if email configured - per domain)**

   * Name: `_smtp._tls.YOUR_DOMAIN`
   * Type: TXT
   * Value: `v=TLSRPTv1; rua=mailto:tlsrpt@YOUR_DOMAIN`
   * TTL: 1800 (or default)

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

5. Complete MTA-STS setup (if enabled):

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

6. Upload your Gmail token to SSM Parameter Store:

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

7. Enable GLE

The auto-enablement of the GLE does not work yet. Run it manually with:

```bash
aws resource-groups update-account-settings \
--group-lifecycle-events-desired-status ACTIVE
```

### Workflow

All commands now require an `ENV` parameter to specify which environment (test or prod):

* **make package ENV=test**: Packages the Lambda function with dependencies (automatically run by make plan)
* **make plan ENV=test**: Creates a plan file showing what changes will be made
* **make apply ENV=test**: Applies the plan file (depends on plan, so will create it if missing)
* **make plan-destroy ENV=test**: Creates a destroy plan
* **make destroy ENV=test**: Applies the destroy plan (depends on plan-destroy)

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

* **SNS Topic**: `ses-email-processing-{environment}` with X-Ray Active tracing
* **SQS Input Queue**: `ses-email-input-{environment}` with 3-retry DLQ policy
* **EventBridge Pipe**: `ses-email-router-{environment}` with router lambda enrichment
  * Source: SQS input queue
  * Enrichment: Router lambda function (DynamoDB routing rules lookup)
  * Target: EventBridge Event Bus
  * Logging: CloudWatch Logs at INFO level with execution data
* **Router Lambda**: `ses-mail-router-enrichment-{environment}` with X-Ray Active tracing
  * Queries DynamoDB for routing rules with hierarchical address matching
  * Enriches messages with routing decisions and email metadata
  * Timeout: 30 seconds, Memory: 128 MB
* **EventBridge Event Bus**: `ses-email-routing-{environment}` with routing rules
  * Routes messages to Gmail forwarder queue (action: `forward-to-gmail`)
  * Routes messages to bouncer queue (action: `bounce`)
* **Handler Queues**:
  * `ses-gmail-forwarder-{environment}` - Triggers Gmail forwarder lambda
  * `ses-bouncer-{environment}` - Triggers bouncer lambda
  * Both with 3-retry DLQ policies and CloudWatch alarms
* **Dead Letter Queues**: All queues have corresponding DLQs with 14-day retention
* **CloudWatch Alarms**: Monitors DLQ messages, queue age, and Pipes failures

**X-Ray Distributed Tracing:**

The entire email processing pipeline is instrumented with X-Ray tracing:
* SNS topic initiates traces with Active tracing
* SQS queues propagate trace context
* EventBridge Pipes maintains trace context through enrichment
* Router lambda adds custom annotations for email metadata
* Handler lambdas continue the trace for end-to-end visibility

**EventBridge Pipes Integration:**

EventBridge Pipes provides serverless message enrichment and routing:
* Automatically polls SQS input queue and invokes router lambda
* Manages retries and error handling with built-in DLQ support
* Logs all executions to CloudWatch for debugging and monitoring
* Transforms enriched output into EventBridge events with proper source and detail-type
* No custom polling or dispatch logic required - fully managed by AWS

## AWS myApplications Integration

The infrastructure is registered with AWS myApplications through AWS Service Catalog AppRegistry, providing application-level visibility and management capabilities in the AWS Console.

### What is myApplications?

AWS myApplications provides a centralized view to manage your applications and their resources. It integrates with AppRegistry to:

* View application health and status
* Track costs at the application level
* Monitor operational metrics
* Manage application metadata and documentation
* Access application resources in one place

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

* **Project**: `ses-mail`
* **ManagedBy**: `terraform`
* **Environment**: `test` or `prod`
* **Application**: `ses-mail-{environment}` (combined tag for myApplications integration)

The Resource Group uses these tags to organize resources, making it easy to:

* View all related resources in one place
* Track costs by environment
* Manage resources collectively
* Monitor resource health

**Accessing the Resource Group:**

```bash
# Get the Resource Group URL from Terraform output
cd terraform/environments/test  # or prod
terraform output resource_group_url

# Or view directly in AWS Console:
# https://console.aws.amazon.com/resource-groups/group/ses-mail-{environment}
```

The Resource Group includes all infrastructure components:

* S3 buckets
* Lambda functions
* DynamoDB tables
* SQS queues
* SNS topics
* CloudWatch alarms and log groups
* IAM roles
* SES resources

## Email Routing Configuration

### DynamoDB Routing Rules Table

The system uses a DynamoDB table to store email routing rules. The table uses a single-table design pattern for extensibility.

**Table Structure:**

* **Primary Key (PK)**: `ROUTE#<email-pattern>` (e.g., `ROUTE#support@example.com`, `ROUTE#*@example.com`, `ROUTE#*`)
* **Sort Key (SK)**: `RULE#v1` (allows versioning)
* **Billing**: PAY_PER_REQUEST (no standing costs)

**Routing Rules Attributes:**

* `entity_type`: `ROUTE` (for filtering)
* `recipient`: Email pattern (denormalized from PK)
* `action`: `forward-to-gmail` | `bounce`
* `target`: Gmail address for forwarding, or empty for bounces
* `enabled`: Boolean (true/false)
* `created_at`: ISO timestamp
* `updated_at`: ISO timestamp
* `description`: Human-readable description

**Hierarchical Matching:**

The router lambda performs lookups in this order (first match wins):

1. Exact match: `ROUTE#user+tag@example.com`
2. Normalized match: `ROUTE#user@example.com` (removes +tag)
3. Domain wildcard: `ROUTE#*@example.com`
4. Global wildcard: `ROUTE#*`

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

* **Hierarchical DynamoDB Lookup**: Performs lookups in order of specificity (exact → normalized → domain wildcard → global wildcard)
* **Email Address Normalization**: Removes +tag from addresses (e.g., `user+newsletter@example.com` → `user@example.com`) for plus addressing support
* **Security Analysis**: Extracts and analyzes DMARC, SPF, DKIM, spam, and virus verdicts from SES receipt
* **Fallback Behavior**: Defaults to "bounce" action when DynamoDB is unavailable or no rule matches
* **X-Ray Tracing**: Active tracing enabled with custom annotations for message ID, source, and routing action

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
  "originalEvent": {...},
  "routingDecisions": [{
    "recipient": "recipient@domain.com",
    "normalizedRecipient": "recipient@domain.com",
    "action": "forward-to-gmail",
    "target": "user@gmail.com",
    "matchedRule": "ROUTE#recipient@domain.com",
    "ruleDescription": "Forward support emails to Gmail",
    "securityVerdict": {
      "spam": "PASS",
      "virus": "PASS",
      "dkim": "PASS",
      "spf": "PASS",
      "dmarc": "PASS"
    }
  }],
  "emailMetadata": {
    "messageId": "...",
    "source": "sender@example.com",
    "subject": "Email subject",
    "timestamp": "2025-01-18T10:00:00Z",
    "securityVerdict": {...}
  }
}]
```

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

* **Name**: `ses-email-routing-{environment}`
* **Type**: Custom event bus (not default)
* **Purpose**: Routes enriched messages from EventBridge Pipes to handler SQS queues
* **Logging**: CloudWatch log group `/aws/events/ses-email-routing-{environment}` (30-day retention)

**EventBridge Rules:**

The Event Bus has two rules that match on routing decisions:

1. **Gmail Forwarder Rule** (`route-to-gmail-{environment}`):
   * **Event Pattern**: Matches `action: "forward-to-gmail"` in routing decisions
   * **Source**: `ses.email.router`
   * **Target**: `ses-gmail-forwarder-{environment}` SQS queue
   * **Retry Policy**: Max 2 retries, 1 hour max event age
   * **Dead Letter Queue**: `ses-gmail-forwarder-dlq-{environment}`

2. **Bouncer Rule** (`route-to-bouncer-{environment}`):
   * **Event Pattern**: Matches `action: "bounce"` in routing decisions
   * **Source**: `ses.email.router`
   * **Target**: `ses-bouncer-{environment}` SQS queue
   * **Retry Policy**: Max 2 retries, 1 hour max event age
   * **Dead Letter Queue**: `ses-bouncer-dlq-{environment}`

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

* **Metric Filters**: Track EventBridge rule failures for both Gmail and bouncer rules
* **Alarms**: Trigger when EventBridge fails to deliver events to target queues
* **Namespace**: `SESMail/{environment}`
* **Metrics**: `EventBridgeGmailRuleFailures`, `EventBridgeBouncerRuleFailures`

**IAM Permissions:**

The EventBridge Event Bus uses an IAM role (`ses-mail-eventbridge-sqs-{environment}`) with permissions to:

* Send messages to Gmail forwarder SQS queue
* Send messages to bouncer SQS queue
* Send failed events to dead letter queues

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

The EventBridge rules use event patterns to match routing decisions. Here are the patterns used:

Gmail Forwarder Rule:
```json
{
  "source": ["ses.email.router"],
  "detail": {
    "routingDecisions": {
      "action": ["forward-to-gmail"]
    }
  }
}
```

Bouncer Rule:
```json
{
  "source": ["ses.email.router"],
  "detail": {
    "routingDecisions": {
      "action": ["bounce"]
    }
  }
}
```

### Gmail Forwarder Lambda Function

The Gmail forwarder lambda (`ses-mail-gmail-forwarder-{environment}`) processes enriched email messages from the gmail-forwarder SQS queue and imports them into Gmail via the Gmail API.

**Functionality:**

* **SQS Event Processing**: Triggered by messages in the gmail-forwarder queue
* **Enriched Message Handling**: Extracts routing decisions and email metadata from EventBridge-enriched messages
* **Gmail API Integration**: Imports emails into Gmail with INBOX and UNREAD labels
* **S3 Email Management**: Fetches raw email from S3 and deletes after successful import
* **Token Management**: Automatically refreshes OAuth tokens and updates SSM Parameter Store
* **X-Ray Tracing**: Active tracing with custom annotations for message ID, recipient, action, and target
* **Error Handling**: Returns batch item failures for SQS retry logic

**Input Format** (from SQS/EventBridge):

The lambda receives SQS messages containing enriched EventBridge messages:

```json
{
  "Records": [{
    "body": "{\"originalEvent\": {...}, \"routingDecisions\": [{\"recipient\": \"...\", \"action\": \"forward-to-gmail\", \"target\": \"user@gmail.com\"}], \"emailMetadata\": {\"messageId\": \"...\", \"source\": \"...\", \"subject\": \"...\"}}"
  }]
}
```

**Processing Flow:**

1. Parse SQS message body to extract enriched EventBridge message
2. Extract message ID, routing decision (action/target), and email metadata
3. Fetch raw email from S3 (`emails/{messageId}`)
4. Import email into Gmail via Gmail API
5. Delete email from S3 after successful import
6. Return success or batch item failure for SQS retry

**Configuration:**

* **Runtime**: Python 3.12
* **Memory**: 128MB
* **Timeout**: 3 seconds (default)
* **Environment Variables**:
  * `GMAIL_TOKEN_PARAMETER`: SSM parameter path for Gmail OAuth token
  * `EMAIL_BUCKET`: S3 bucket containing email files

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

* **Queue Name**: `ses-gmail-forwarder-{environment}`
* **Dead Letter Queue**: `ses-gmail-forwarder-dlq-{environment}` (14 day retention)
* **Visibility Timeout**: 30 seconds (10x lambda timeout)
* **Max Retries**: 3 (before moving to DLQ)
* **Event Source Mapping**: Batch size 1, max concurrency 10
* **CloudWatch Alarms**: DLQ messages >0, queue age >5 minutes

### Bouncer Lambda Function

The bouncer lambda (`ses-mail-bouncer-{environment}`) processes enriched email messages from the bouncer SQS queue and sends bounce notification emails via SES.

**Functionality:**

* **SQS Event Processing**: Triggered by messages in the bouncer queue
* **Enriched Message Handling**: Extracts routing decisions and email metadata from EventBridge-enriched messages
* **Bounce Notification**: Sends formatted bounce emails via SES to the original sender
* **Email Metadata**: Includes original sender, recipient, subject, timestamp, and routing rule information in bounce
* **X-Ray Tracing**: Active tracing with custom annotations for message ID, source, action, and environment
* **Error Handling**: Returns batch item failures for SQS retry logic
* **Professional Formatting**: Sends both HTML and plain text bounce messages

**Input Format** (from SQS/EventBridge):

The lambda receives SQS messages containing enriched EventBridge messages:

```json
{
  "Records": [{
    "body": "{\"originalEvent\": {...}, \"routingDecisions\": [{\"recipient\": \"...\", \"action\": \"bounce\", \"target\": \"\", \"matchedRule\": \"ROUTE#*\", \"ruleDescription\": \"Default: bounce all unmatched emails\"}], \"emailMetadata\": {\"messageId\": \"...\", \"source\": \"...\", \"subject\": \"...\"}}"
  }]
}
```

**Processing Flow:**

1. Parse SQS message body to extract enriched EventBridge message
2. Extract message ID, routing decisions (action/target/rule), and email metadata
3. For each recipient with action "bounce", send a bounce notification via SES
4. Bounce email includes original message details and routing rule information
5. Return success or batch item failure for SQS retry

**Bounce Email Format:**

The bounce notification includes:
* Subject: `Mail Delivery Failed: {original subject}`
* Original message details (from, to, subject, timestamp)
* Reason for bounce (recipient not configured)
* Routing rule that triggered the bounce
* Professional HTML and plain text formatting

**Configuration:**

* **Runtime**: Python 3.12
* **Memory**: 128MB
* **Timeout**: 30 seconds (for SES API calls)
* **Environment Variables**:
  * `BOUNCE_SENDER`: Sender address for bounce notifications (e.g., `mailer-daemon@domain.com`)
  * `ENVIRONMENT`: Environment name (test/prod)

**Testing the Bouncer Lambda:**

```bash
# Create a test SQS message with enriched data
cat > test_bounce_message.json <<'EOF'
{
  "Records": [{
    "messageId": "test-msg-1",
    "body": "{\"originalEvent\": {\"eventSource\": \"aws:ses\", \"ses\": {\"mail\": {\"messageId\": \"abc123\", \"source\": \"sender@example.com\", \"destination\": [\"recipient@testmail.rrod.net\"]}, \"receipt\": {\"spamVerdict\": {\"status\": \"PASS\"}, \"virusVerdict\": {\"status\": \"PASS\"}}}}, \"routingDecisions\": [{\"recipient\": \"recipient@testmail.rrod.net\", \"normalizedRecipient\": \"recipient@testmail.rrod.net\", \"action\": \"bounce\", \"target\": \"\", \"matchedRule\": \"ROUTE#*\", \"ruleDescription\": \"Default: bounce all unmatched emails\"}], \"emailMetadata\": {\"messageId\": \"abc123\", \"source\": \"sender@example.com\", \"subject\": \"Test Email\", \"timestamp\": \"2025-01-18T10:00:00Z\", \"securityVerdict\": {\"spam\": \"PASS\", \"virus\": \"PASS\"}}}"
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

* **Queue Name**: `ses-bouncer-{environment}`
* **Dead Letter Queue**: `ses-bouncer-dlq-{environment}` (14 day retention)
* **Visibility Timeout**: 180 seconds (6x lambda timeout)
* **Max Retries**: 3 (before moving to DLQ)
* **Event Source Mapping**: Batch size 1, max concurrency 5
* **CloudWatch Alarms**: DLQ messages >0, queue age >5 minutes

**Important Notes:**

* SES sandbox mode requires sender email verification. In production with verified domain, bounces will be sent to any address.
* Bounce sender defaults to `mailer-daemon@{domain}` using the first domain from configuration.

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
   * Router enrichment operations
   * Gmail forwarding operations
   * Bounce sending operations
3. **Lambda Function Errors** - Error counts for all lambda functions (processor, router, gmail forwarder, bouncer)
4. **Lambda Function Invocations** - Invocation counts for all lambda functions
5. **SQS Queue Depths** - Current message counts in input, gmail-forwarder, and bouncer queues
6. **Dead Letter Queue Messages** - DLQ message counts (should normally be 0)
7. **Lambda Duration** - Average execution times for router, gmail forwarder, and bouncer lambdas
8. **Recent Email Logs** - CloudWatch Logs Insights query showing recent processed emails

### Custom Metrics

The lambda functions publish custom CloudWatch metrics to the `SESMail/{environment}` namespace for tracking operation success/failure rates:

**Router Enrichment Metrics:**
* `RouterEnrichmentSuccess` - Count of successfully enriched messages
* `RouterEnrichmentFailure` - Count of failed enrichments (using fallback routing)

**Gmail Forwarder Metrics:**
* `GmailForwardSuccess` - Count of successful Gmail imports
* `GmailForwardFailure` - Count of failed Gmail imports

**Bouncer Metrics:**
* `BounceSendSuccess` - Count of successful bounce notifications sent
* `BounceSendFailure` - Count of failed bounce notifications

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
* `ses-email-input-dlq-messages-{environment}` - Input queue DLQ has messages
* `ses-gmail-forwarder-dlq-messages-{environment}` - Gmail forwarder DLQ has messages
* `ses-bouncer-dlq-messages-{environment}` - Bouncer DLQ has messages

**Queue Age Alarms:**
* `ses-email-input-queue-age-{environment}` - Messages aging >5 minutes in input queue
* `ses-gmail-forwarder-queue-age-{environment}` - Messages aging >5 minutes in Gmail queue
* `ses-bouncer-queue-age-{environment}` - Messages aging >5 minutes in bouncer queue

**Lambda Error Alarms:**
* `ses-mail-lambda-errors-{environment}` - Email processor lambda has >5 errors in 5 minutes
* `ses-mail-lambda-router-errors-{environment}` - Router enrichment lambda has >5 errors in 5 minutes
* `ses-mail-lambda-gmail-forwarder-errors-{environment}` - Gmail forwarder lambda has >5 errors in 5 minutes
* `ses-mail-lambda-bouncer-errors-{environment}` - Bouncer lambda has >5 errors in 5 minutes

**Email Processing Alarms:**
* `ses-mail-high-email-volume-{environment}` - More than 100 emails in 5 minutes
* `ses-mail-high-spam-rate-{environment}` - Spam rate >10% in 5 minutes

**EventBridge Alarms:**
* `eventbridge-pipes-failures-{environment}` - EventBridge Pipes enrichment failures
* `eventbridge-gmail-failures-{environment}` - EventBridge failed to deliver to Gmail queue
* `eventbridge-bouncer-failures-{environment}` - EventBridge failed to deliver to bouncer queue

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
* SNS topic initiates traces with Active tracing mode
* SQS queues propagate trace context through the pipeline
* EventBridge Pipes maintains trace context during enrichment
* Router lambda adds custom annotations (messageId, source, recipient, action)
* Handler lambdas (Gmail forwarder, bouncer) continue the trace with operation-specific annotations

**Viewing Traces:**

```bash
# Via AWS Console:
# X-Ray → Traces → Filter by service "ses-mail-router-enrichment-test"

# Or view service map:
# X-Ray → Service map → Select time range
```

**X-Ray Annotations:**

Router enrichment lambda annotations:
* `messageId` - SES message ID
* `source` - Email sender address
* `recipient` - Email recipient address
* `action` - Routing action (forward-to-gmail or bounce)

Gmail forwarder lambda annotations:
* `action` - forward-to-gmail
* `recipient` - Original recipient address
* `target` - Gmail target address
* `gmail_message_id` - Gmail message ID after import
* `import_status` - success or error

Bouncer lambda annotations:
* `messageId` - SES message ID
* `source` - Email sender address
* `environment` - Environment name (test/prod)
* `action` - bounce

### CloudWatch Logs

All lambda functions log to CloudWatch Logs with 30-day retention:

**Log Groups:**
* `/aws/lambda/ses-mail-email-processor-{environment}` - Email processor logs
* `/aws/lambda/ses-mail-router-enrichment-{environment}` - Router enrichment logs
* `/aws/lambda/ses-mail-gmail-forwarder-{environment}` - Gmail forwarder logs
* `/aws/lambda/ses-mail-bouncer-{environment}` - Bouncer logs
* `/aws/events/ses-email-routing-{environment}` - EventBridge Event Bus logs
* `/aws/vendedlogs/pipes/{environment}/ses-email-router` - EventBridge Pipes logs

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

### Monitoring Best Practices

1. **Set up SNS notifications**: Configure the `alarm_sns_topic_arn` in `terraform.tfvars` to receive alarm notifications via email or SMS

2. **Monitor DLQ alarms**: Dead letter queue messages indicate persistent failures that require investigation

3. **Track custom metrics**: Review handler success/failure rates daily to identify trends

4. **Use X-Ray for debugging**: When issues occur, use X-Ray traces to identify bottlenecks and failures across the pipeline

5. **Review CloudWatch dashboard**: Check the dashboard regularly to ensure healthy operation

6. **Set up log metric filters**: Create additional custom metric filters for application-specific error patterns

7. **Enable detailed monitoring**: For production environments, consider enabling detailed (1-minute) CloudWatch metrics for faster alerting
