# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SES Mail is an AWS-based email receiving system that processes emails through SES and forwards them to Gmail via the Gmail API. The system is currently being modernized from a direct Lambda invocation architecture to a fully event-driven architecture using SNS, SQS, EventBridge Pipes, and EventBridge Event Bus.

## Common Commands

### Terraform Workflow

**IMPORTANT**: Always use the Makefile for Terraform operations. Never run `terraform` commands directly. Always specify `ENV=test` and use `AWS_PROFILE=ses-mail`.

```bash
# Plan changes for test environment
AWS_PROFILE=ses-mail make plan ENV=test

# Apply changes for test environment
AWS_PROFILE=ses-mail make apply ENV=test

# Validate configuration
AWS_PROFILE=ses-mail make validate ENV=test
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

# Query routing rules
AWS_PROFILE=ses-mail aws dynamodb get-item \
  --table-name ses-email-routing-test \
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

### Current Architecture (Legacy - Being Replaced)

```plain
SES → S3 → Lambda (validator - sync) → Lambda (email_processor - async) → Gmail API
```

- `email_validator.py`: Synchronous RequestResponse lambda that bounces all emails
- `email_processor.py`: Async lambda that fetches email from S3, imports to Gmail, deletes from S3

### Target Architecture (Modernization In Progress)

```plain
SES → S3 → SNS → SQS → EventBridge Pipes[router enrichment] → EventBridge Event Bus →
  → SQS (gmail-forwarder) → Lambda (gmail handler)
  → SQS (bouncer) → Lambda (bouncer handler)
```

Key changes:

- **SNS with X-Ray tracing**: Replaces direct lambda invocation
- **EventBridge Pipes**: Enriches messages via router lambda (DynamoDB lookup)
- **EventBridge Event Bus**: Routes messages to appropriate queues based on routing decisions
- **Handler lambdas**: Process specific actions (Gmail forwarding, bouncing)
- **DynamoDB**: Stores routing rules with hierarchical address matching

See `.kiro/specs/ses-email-routing-modernization/` for complete design documentation.

### DynamoDB Single-Table Design Pattern

The routing table uses generic PK/SK keys with prefixed values for extensibility:

**Table**: `ses-email-routing-{environment}`

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
            ├── email_processor.py   # Gmail forwarder lambda
            ├── email_validator.py   # Sync validator (to be removed)
            └── package/             # Lambda dependencies (pip install -t)
```


**Important**: Read the markdown files in the above 

## Key Development Patterns

### Task-Driven Development

1. Read specification documents in `.kiro/specs/{project}/` (the current project is `ses-email-routing-modernization`)
   1. Files include `requirements.md`, `design.md`, and `tasks.md`
2. Follow tasks in order from `tasks.md`
3. A task is not complete until:
   - The code is written
   - Updated `README.md` with user-facing documentation
   - Ran `AWS_PROFILE=ses-mail make plan ENV=test`
   - Deployed with `AWS_PROFILE=ses-mail make apply ENV=test`
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
- Archive created automatically during `AWS_PROFILE=ses-mail make plan ENV=test` or `AWS_PROFILE=ses-mail make apply ENV=test`
- Gmail API integration uses OAuth token from SSM Parameter Store

**Lambda handlers**:

- Current: `email_processor.lambda_handler` - SES event → Gmail import
- Future: Router enrichment, Gmail handler, Bouncer handler

### IAM Role Naming Convention

- Email processor: `ses-mail-lambda-execution-{env}`
- Email validator: `ses-mail-lambda-validator-{env}` (to be removed)
- Router enrichment: `ses-mail-lambda-router-{env}`
- EventBridge Pipes: `ses-mail-pipes-execution-{env}` (future)

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

Current:

1. SES receives email → stores in S3 (`emails/{messageId}`)
2. Validator lambda (sync) → bounces all emails, returns STOP_RULE_SET
3. Email processor lambda (async, but currently skipped due to validator)

Target (after modernization):

1. SES receives email → stores in S3 → publishes to SNS
2. SNS → SQS input queue
3. EventBridge Pipes → router lambda enriches message (DynamoDB lookup)
4. EventBridge Event Bus → routes to handler queues
5. Handler lambdas process from queues (Gmail forward or bounce)
6. X-Ray distributed tracing across entire pipeline

## Monitoring and Operations

### CloudWatch Resources

- Dashboard: `ses-mail-dashboard-{env}`
- Log groups: `/aws/lambda/ses-mail-*-{env}`
- Metric filters: emails accepted, spam, virus, lambda errors
- Alarms: high volume, high spam rate, lambda errors (→ SNS topic)

### Email Flow Debugging

```bash
# View Lambda logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-email-processor-test --follow

# Check email in S3
AWS_PROFILE=ses-mail aws s3 ls s3://ses-mail-storage-{account-id}-test/emails/
```

## State Management

- State stored in S3: `s3://terraform-state-{account-id}/ses-mail/{environment}.tfstate`
- Bucket auto-created by Makefile on first run
- Versioning enabled for state history
- Server-side encryption with AES256
- No DynamoDB locking (using S3 native locking)
