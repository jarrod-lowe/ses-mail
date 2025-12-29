# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SES Mail is an AWS-based email receiving system that processes emails through SES and forwards them to Gmail via the Gmail API. The system uses a fully event-driven architecture with SNS, SQS, EventBridge, and Lambda functions for routing and processing email.

## Common Commands

### Terraform Workflow

When writing terraform, never use `jsonencode` where an
`aws_iam_policy_document` can be used.

**IMPORTANT**: Always use the Makefile for Terraform operations. Never run `terraform` commands directly. Always specify `ENV=test` and use `AWS_PROFILE=ses-mail`.

```bash
# Plan changes for test environment
AWS_PROFILE=ses-mail make plan ENV=test >/dev/null

# Show the plan for test environment (will run plan if required)
AWS_PROFILE=ses-mail make show-plan ENV=test >/dev/null

# Apply changes for test environment (will run plan if required)
AWS_PROFILE=ses-mail make apply ENV=test >/dev/null

# Validate configuration
AWS_PROFILE=ses-mail make validate ENV=test

# Get terraform outputs
AWS_PROFILE=ses-mail make outputs ENV=test
```

`terraform fmt` is automatically run by any of those targets if needed.

The Makefile handles:

- Automatic S3 state bucket creation (`terraform-state-{account-id}`)
- Backend configuration with environment-specific state keys
- Lambda function packaging with dependencies
- Plan file creation and clean-up

### DynamoDB Routing Rules Management

```bash
# Add a routing rule (single-table design pattern)
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
    "created_at": {"S": "2025-01-18T10:00:00Z"},
    "updated_at": {"S": "2025-01-18T10:00:00Z"},
    "description": {"S": "Forward support emails to Gmail"}
  }'

# Query routing rules
AWS_PROFILE=ses-mail aws dynamodb get-item \
  --table-name ses-mail-email-routing-test \
  --key '{"PK": {"S": "ROUTE#support@example.com"}, "SK": {"S": "RULE#v1"}}'
```

### Gmail OAuth Token Management

```bash
# Update Gmail token in SSM Parameter Store
AWS_PROFILE=ses-mail aws ssm put-parameter \
  --name "/ses-mail/test/gmail-token" \
  --value "$(cat token.json)" \
  --type SecureString \
  --overwrite
```

## Architecture

The system uses a fully event-driven architecture:

```plain
SES → S3 → SNS → router_enrichment Lambda → EventBridge Event Bus →
  → SQS (gmail-forwarder) → Lambda (gmail_forwarder) → Gmail API
  → SQS (bouncer) → Lambda (bouncer) → SES Bounce
```

**Key components**:

- **SNS with X-Ray tracing**: SES publishes email receipt notifications to SNS topic
- **Router enrichment Lambda**: Subscribes to SNS, performs DynamoDB lookup for routing rules, publishes to EventBridge Event Bus
- **EventBridge Event Bus**: Routes messages to appropriate SQS queues based on routing action
- **Handler lambdas**: Process specific actions (Gmail forwarding via `gmail_forwarder.py`, bouncing via `bouncer.py`)
- **DynamoDB**: Stores routing rules with hierarchical address matching
- **Retry processing**: Step Functions workflow handles persistent failures with exponential backoff

See `.kiro/specs/*` for complete design documentation.

### DynamoDB Single-Table Design Pattern

The routing table uses generic PK/SK keys with prefixed values for extensibility:

**Table**: `ses-mail-email-routing-{environment}`

- **PK**: `ROUTE#<pattern>` (e.g., `ROUTE#support@example.com`, `ROUTE#*@example.com`, `ROUTE#*`)
- **SK**: `RULE#v1` (allows future versioning)
- **Billing**: PAY_PER_REQUEST (no standing costs)

**Denormalized attributes** (key data stored in values):

- `entity_type`: `ROUTE`
- `recipient`: Email pattern (denormalized from PK)
- `action`: `forward-to-gmail` | `bounce`
- `target`: Gmail address or empty string
- `enabled`: Boolean
- `created_at`, `updated_at`: ISO timestamps
- `description`: Human-readable text

**Hierarchical lookup strategy** (router lambda):

1. Exact match: `ROUTE#user+tag@example.com`
2. Normalized match: `ROUTE#user@example.com` (removes +tag for plus addressing)
3. Domain wildcard: `ROUTE#*@example.com`
4. Global wildcard: `ROUTE#*` (default/catch-all)

The generic PK/SK design allows future use cases without schema changes (e.g., `CONFIG#*`, `METRICS#*`).

## Project Structure

### Terraform Organization

```plain
terraform/
├── environments/
│   ├── test/           # Test environment
│   │   ├── main.tf              # Provider config + module invocation
│   │   ├── variables.tf         # Variable definitions
│   │   ├── terraform.tfvars     # Environment-specific values (not in git)
│   │   └── outputs.tf           # Pass-through outputs
│   └── prod/           # Production environment (same structure)
└── modules/
    └── ses-mail/       # Reusable SES mail module
        ├── iam.tf               # IAM roles and policies
        ├── ses.tf               # SES domain, rules, receipt configuration
        ├── s3.tf                # Email storage bucket
        ├── lambda.tf            # Lambda functions and packaging
        ├── dynamodb.tf          # Routing rules table
        ├── cloudwatch.tf        # Metrics, alarms, dashboard
        ├── mta-sts.tf           # MTA-STS policy hosting
        └── lambda/
            ├── router_enrichment.py        # Router lambda (DynamoDB lookup)
            ├── gmail_forwarder.py          # Gmail forwarder lambda
            ├── bouncer.py                  # Email bouncer lambda
            ├── smtp_credential_manager.py  # SMTP credential management
            └── package/                    # Lambda dependencies (pip install -t)
```

## Key Development Patterns

### Task-Driven Development

1. Read specification documents in `.kiro/specs/{project}/` (the current project is `google-oauth-token-management`)
   1. Files include `requirements.md`, `design.md`, and `tasks.md`
2. Follow tasks in order from `tasks.md`
3. A task is not complete until:
   - The code is written
   - Updated `README.md` with user-facing documentation
   - Ran `AWS_PROFILE=ses-mail make plan ENV=test >/dev/null`
   - Deployed with `AWS_PROFILE=ses-mail make apply ENV=test >/dev/null`
   - It is tested
   - Marked task as `[x]` in `tasks.md`
   - A git commit has been made

**Important**: You need to read all three specification documents.

### Multi-Environment Management

- Environments are isolated via separate terraform.tfvars and state files
- State key format: `ses-mail/{environment}.tfstate`
- Common module in `modules/ses-mail/` shared across environments
- Environment-specific values only in `terraform/environments/{env}/terraform.tfvars`
- You may only ever interact with the `test` environment

### Lambda Function Development

Lambda functions are in `terraform/modules/ses-mail/lambda/`:

- Source files committed to git
- Dependencies installed to `lambda/package/` via Makefile
- Archive created automatically during `AWS_PROFILE=ses-mail make plan ENV=test >/dev/null` or `AWS_PROFILE=ses-mail make apply ENV=test >/dev/null`
- Gmail API integration uses OAuth token from SSM Parameter Store

**Lambda handlers**:

- `router_enrichment.lambda_handler` - SNS event → DynamoDB lookup → EventBridge Event Bus
- `gmail_forwarder.lambda_handler` - SQS event → Fetch from S3 → Gmail API import → Delete from S3
- `bouncer.lambda_handler` - SQS event → Send bounce email via SES
- `smtp_credential_manager.lambda_handler` - DynamoDB Streams → IAM user creation/deletion for SMTP

### IAM Role Naming Convention

- Router enrichment: `ses-mail-lambda-router-{env}`
- Gmail forwarder: `ses-mail-lambda-gmail-forwarder-{env}`
- Bouncer: `ses-mail-lambda-bouncer-{env}`
- SMTP credential manager: `ses-mail-lambda-credential-manager-{env}`
- Tag sync: `ses-mail-lambda-tag-sync-{env}`

## Important Conventions

### AWS Profile

Always use `AWS_PROFILE=ses-mail` for all AWS CLI and Terraform operations.

### Single-Table Design

When extending DynamoDB table:

- Use generic PK/SK keys with prefixed values (e.g., `ROUTE#`, `CONFIG#`, `METRICS#`)
- Denormalize key data into attributes for easier querying
- Use SK for versioning or hierarchical sorting (e.g., `RULE#v1`, `RULE#v2`)
- Add `entity_type` attribute for filtering queries

### Email Processing Flow

The event-driven email processing flow:

1. SES receives email → stores in S3 (`emails/{messageId}`) → publishes to SNS topic
2. Router enrichment Lambda subscribes to SNS → performs DynamoDB lookup for routing rules
3. Router Lambda publishes routing decision to EventBridge Event Bus
4. EventBridge rules route to appropriate SQS queues based on action:
   - `forward-to-gmail` → gmail-forwarder queue
   - `bounce` → bouncer queue
5. Handler lambdas process messages from queues:
   - Gmail forwarder: Fetches email from S3, imports to Gmail API, deletes from S3
   - Bouncer: Sends bounce email via SES
6. Failed messages are retried via SQS DLQ and Step Functions workflow
7. X-Ray distributed tracing tracks requests across the entire pipeline

## Monitoring and Operations

### CloudWatch Resources

- Dashboard: `ses-mail-dashboard-{env}`
- Log groups: `/aws/lambda/ses-mail-*-{env}`
- Metric filters: emails accepted, spam, virus, lambda errors
- Alarms: high volume, high spam rate, lambda errors (→ SNS topic)

### Email Flow Debugging

```bash
# View Lambda logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-gmail-forwarder-test --follow
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-bouncer-test --follow

# Check email in S3
AWS_PROFILE=ses-mail aws s3 ls s3://ses-mail-storage-{account-id}-test/emails/

# Check SQS queues
AWS_PROFILE=ses-mail aws sqs get-queue-attributes --queue-url $(AWS_PROFILE=ses-mail aws sqs get-queue-url --queue-name ses-mail-gmail-forwarder-test --query 'QueueUrl' --output text) --attribute-names ApproximateNumberOfMessages

# View X-Ray traces
aws xray get-trace-summaries --start-time $(date -u -v-1H +%s) --end-time $(date -u +%s)
```

## State Management

- State stored in S3: `s3://terraform-state-{account-id}/ses-mail/{environment}.tfstate`
- Bucket auto-created by Makefile on first run
- Versioning enabled for state history
- Server-side encryption with AES256
- No DynamoDB locking (using S3 native locking)
