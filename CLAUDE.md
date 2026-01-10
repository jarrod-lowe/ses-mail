# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Documentation Structure

The project documentation has been reorganized for better accessibility:

- **[README.md](README.md)** - Project overview, quick start, and quick reference
- **[docs/SETUP.md](docs/SETUP.md)** - Complete setup guide (Google OAuth, AWS, Terraform, DNS)
- **[docs/OPERATIONS.md](docs/OPERATIONS.md)** - Day-to-day operations, monitoring, troubleshooting
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** - Technical deep-dive into system design
- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** - Integration testing, contributing guidelines

For user-facing documentation updates, modify the appropriate file in `docs/` rather than adding to README.md.

## Project Overview

SES Mail is an AWS-based email receiving system that processes emails through SES and forwards them to Gmail via the Gmail API. The system uses a fully event-driven architecture with SNS, SQS, EventBridge, and Lambda functions for routing and processing email.

For detailed architecture information, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Common Commands

### Terraform Workflow

When writing terraform, never use `jsonencode` where an
`aws_iam_policy_document` can be used.

**IMPORTANT**: Always use the Makefile for Terraform operations. Never run `terraform` commands directly. Always specify `ENV=test` and use `AWS_PROFILE=ses-mail`.

```bash
# Plan changes for test environment
AWS_PROFILE=ses-mail make plan ENV=test >/dev/null

# Show the plan for test environment (will run plan if required)
AWS_PROFILE=ses-mail make show-plan ENV=test

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

#### Deployment Order

When test and prod share the same AWS account (`join_existing_deployment = "prod"` in test environment), deploy in this order:

1. **Prod first**: `AWS_PROFILE=ses-mail make apply ENV=prod >/dev/null`
2. **Test second**: `AWS_PROFILE=ses-mail make apply ENV=test >/dev/null`

This is required because test adds rules to prod's active SES ruleset. If environments use separate AWS accounts, they can be deployed in any order.

#### Clean Operations

**Fast clean** (preserves Lambda layers):

```bash
AWS_PROFILE=ses-mail make clean ENV=test
```

Removes Terraform state, plans, and zip files but keeps installed Lambda layers. Use this for normal workflow between deployments.

**Full clean** (forces layer rebuild):

```bash
AWS_PROFILE=ses-mail make clean-all ENV=test
```

Removes everything including Lambda layers. Next apply will rebuild layers (~30-60s). Use when troubleshooting dependency issues or forcing complete rebuild.

### DynamoDB Routing Rules Management

For complete routing rule management, see [docs/OPERATIONS.md#email-routing-management](docs/OPERATIONS.md#email-routing-management).

Quick example:

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
    "created_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "updated_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "description": {"S": "Forward support emails to Gmail"}
  }'
```

### Gmail OAuth Token Management

For complete OAuth token management, see [docs/OPERATIONS.md#oauth-token-management](docs/OPERATIONS.md#oauth-token-management).

Quick command:

```bash
# Refresh OAuth token
AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test
```

### Integration Testing

**IMPORTANT**: Integration tests require activating the Python virtual environment before running.

For complete integration testing documentation, see [docs/DEVELOPMENT.md#integration-testing](docs/DEVELOPMENT.md#integration-testing).

Quick command:

```bash
# Run integration tests - MUST activate venv first
source .venv/bin/activate
AWS_PROFILE=ses-mail python3 scripts/integration_test.py \
  --env test \
  --from sender@testmail.domain.com \
  --test-domain testmail.domain.com \
  --gmail-target your-email@gmail.com
```

**Common mistake**: Running tests without activating venv will fail with import errors. Always use `source .venv/bin/activate` first.

## Architecture

The system uses a fully event-driven architecture:

```plain
SES → S3 → SNS → SQS → EventBridge Pipes [router Lambda] → Event Bus →
  → SQS (gmail-forwarder) → Lambda (gmail_forwarder) → Gmail API
  → SQS (bouncer) → Lambda (bouncer) → SES Bounce
```

**Key components**:

- **EventBridge Pipes**: Enriches messages via router lambda (DynamoDB lookup)
- **EventBridge Event Bus**: Routes messages based on routing action
- **Handler lambdas**: Process actions (Gmail forwarding, bouncing)
- **DynamoDB**: Stores routing rules with hierarchical address matching
- **Step Functions**: Retry workflows for failures and token expiration
- **X-Ray**: Distributed tracing across entire pipeline

For complete architecture details, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

See `.kiro/specs/*` for complete design documentation.

### DynamoDB Single-Table Design

**Quick reference**:

- **Table**: `ses-mail-email-routing-{environment}`
- **PK Format**: `ROUTE#<pattern>`, `SMTP_USER#<username>` (entity type prefix + identifier)
- **SK Format**: `RULE#v1`, `CREDENTIALS#v1` (allows versioning)
- **Lookup order**: Exact → Normalized (remove +tag) → Domain wildcard → Global wildcard

For complete schema details and design rationale, see [docs/ARCHITECTURE.md#design-patterns](docs/ARCHITECTURE.md#design-patterns).

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
   - Updated appropriate documentation in `docs/` (SETUP.md, OPERATIONS.md, ARCHITECTURE.md, or DEVELOPMENT.md)
   - Ran `AWS_PROFILE=ses-mail make plan ENV=test >/dev/null`
   - Deployed with `AWS_PROFILE=ses-mail make apply ENV=test >/dev/null`
   - It is tested
   - Marked task as `[x]` in `tasks.md`
   - A git commit has been made

**Important**: You need to read all three specification documents.

### Multi-Environment Management

- Environments can share an AWS account or use separate accounts
- When sharing an account (`join_existing_deployment` set in test), the test environment:
  - Creates its own ruleset (for reference) but doesn't activate it
  - Adds rules to the prod environment's active ruleset
  - Must be deployed after the prod environment
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

### SES Ruleset Sharing

AWS SES allows only one active receipt ruleset per account. When test and prod share an account, test joins prod's ruleset via `join_existing_deployment = "prod"` in `terraform/environments/test/terraform.tfvars`. This ensures:

- Test creates its own ruleset but doesn't activate it
- Test adds its rules to prod's active ruleset
- Both environments can receive email using the same SES configuration

### Single-Table Design

When extending DynamoDB table:

- Use generic PK/SK keys with prefixed values (e.g., `ROUTE#`, `CONFIG#`, `METRICS#`)
- Denormalize key data into attributes for easier querying
- Use SK for versioning or hierarchical sorting (e.g., `RULE#v1`, `RULE#v2`)
- Add `entity_type` attribute for filtering queries

### Email Processing Flow

**Quick summary**: SES → S3 → SNS → SQS → EventBridge Pipes → Router Lambda (DynamoDB) → Event Bus → Handler Queues → Handler Lambdas → Gmail API / SES Bounce

For detailed flow diagrams and timing, see [docs/ARCHITECTURE.md#email-processing-flow](docs/ARCHITECTURE.md#email-processing-flow).

## Monitoring and Operations

### CloudWatch Resources

- Dashboard: `ses-mail-dashboard-{env}`
- Log groups: `/aws/lambda/ses-mail-*-{env}`
- Metric filters: emails accepted, spam, virus, lambda errors
- Alarms: high volume, high spam rate, lambda errors (→ SNS topic)

### Debugging Commands

For debugging email flow, monitoring, and troubleshooting, see:

- [docs/OPERATIONS.md#monitoring-and-troubleshooting](docs/OPERATIONS.md#monitoring-and-troubleshooting) - Complete monitoring guide
- [docs/OPERATIONS.md#quick-command-reference](docs/OPERATIONS.md#quick-command-reference) - Common operations commands

## State Management

- State stored in S3: `s3://terraform-state-{account-id}/ses-mail/{environment}.tfstate`
- Bucket auto-created by Makefile on first run
- Versioning enabled for state history
- Server-side encryption with AES256
- No DynamoDB locking (using S3 native locking)
