# Development Guide

This guide covers integration testing, development workflows, and contributing to the SES Mail system.

## Table of Contents

- [Development Setup](#development-setup)
- [Integration Testing](#integration-testing)
- [Contributing Guidelines](#contributing-guidelines)
- [Makefile Commands](#makefile-commands)
- [Terraform Conventions](#terraform-conventions)

## Development Setup

### Prerequisites

- Python 3.8 or higher
- Terraform 1.0 or higher
- AWS CLI configured with appropriate profile
- Git for version control

### Local Environment Setup

```bash
# Clone the repository
git clone <repository-url>
cd ses-mail

# Create Python virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On macOS/Linux
# OR
.venv\Scripts\activate     # On Windows

# Install Python dependencies
pip3 install -r requirements.txt
```

### Environment Variables

```bash
# Set AWS profile for all operations
export AWS_PROFILE=ses-mail

# Optional: Set default environment
export ENV=test
```

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
AWS_PROFILE=ses-mail python3 scripts/integration_test.py \
  --env test \
  --from sender@testmail.domain.com \
  --test-domain testmail.domain.com \
  --gmail-target your-email@gmail.com

# Run with verbose logging
AWS_PROFILE=ses-mail python3 scripts/integration_test.py \
  --env test \
  --from sender@testmail.domain.com \
  --test-domain testmail.domain.com \
  --gmail-target your-email@gmail.com \
  --verbose

# Skip cleanup of test routing rules (for debugging)
AWS_PROFILE=ses-mail python3 scripts/integration_test.py \
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

#### Test fails with "Message not found in input queue"

**Causes:**
- SES receipt rule not active or not publishing to SNS
- SNS topic subscription to SQS input queue missing
- Sender email not verified in SES (sandbox mode)

**Solutions:**
```bash
# Check SES receipt rule status
AWS_PROFILE=ses-mail aws ses describe-active-receipt-rule-set

# Verify SNS topic subscription
AWS_PROFILE=ses-mail aws sns list-subscriptions

# Check CloudWatch Logs for SES receipt errors
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow
```

#### Test fails with "Router logs not found"

**Causes:**
- EventBridge Pipes not active or not invoking router lambda
- Router lambda missing DynamoDB permissions
- DynamoDB routing table not accessible

**Solutions:**
```bash
# Check EventBridge Pipes status
AWS_PROFILE=ses-mail aws pipes list-pipes

# Check router lambda logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow

# Verify DynamoDB table exists
AWS_PROFILE=ses-mail aws dynamodb describe-table --table-name ses-mail-email-routing-test
```

#### Test fails with "Message not found in handler queue"

**Causes:**
- EventBridge Event Bus rules not active
- Event pattern not matching routing decision
- EventBridge missing SQS send permissions

**Solutions:**
```bash
# Check EventBridge rules
AWS_PROFILE=ses-mail aws events list-rules --event-bus-name ses-mail-email-routing-test

# Check rule targets
AWS_PROFILE=ses-mail aws events list-targets-by-rule --rule route-to-gmail-test --event-bus-name ses-mail-email-routing-test

# Check EventBridge logs
AWS_PROFILE=ses-mail aws logs tail /aws/events/ses-mail-email-routing-test --follow
```

#### Test fails with "X-Ray trace not found"

**Causes:**
- X-Ray trace not yet available (takes 60-90 seconds)
- SNS topic Active tracing not enabled
- Lambda functions X-Ray tracing not enabled

**Solutions:**
```bash
# Wait longer (traces can take 60-90 seconds)
sleep 90

# Check SNS topic tracing configuration
AWS_PROFILE=ses-mail aws sns get-topic-attributes --topic-arn <arn>

# Verify Lambda X-Ray tracing
AWS_PROFILE=ses-mail aws lambda get-function --function-name ses-mail-router-enrichment-test
```

#### Messages found in dead letter queues

**Causes:**
- Handler lambda errors (check CloudWatch Logs)
- Gmail OAuth token expired
- SES sending permissions missing (bouncer)

**Solutions:**
```bash
# Check handler lambda logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-gmail-forwarder-test --follow

# Refresh OAuth token
AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test

# Receive DLQ message to inspect
AWS_PROFILE=ses-mail aws sqs receive-message --queue-url <dlq-url> --max-number-of-messages 1
```

### Best Practices for Testing

1. **Run tests before deploying to production**: Always run integration tests in test environment first
2. **Use verbose mode for debugging**: Add `--verbose` flag when investigating failures
3. **Clean up after tests**: Don't use `--skip-cleanup` unless debugging
4. **Monitor X-Ray traces**: Review traces for performance bottlenecks
5. **Check CloudWatch dashboards**: Verify metrics align with expected behavior

### Advanced Testing Scenarios

#### Test Plus Addressing Support

```bash
# Manually create routing rule for normalized address
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-mail-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#test@testmail.domain.com"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "test@testmail.domain.com"},
    "action": {"S": "forward-to-gmail"},
    "target": {"S": "your-email@gmail.com"},
    "enabled": {"BOOL": true},
    "created_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "updated_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "description": {"S": "Test plus addressing"}
  }'

# Send email to test+tag@testmail.domain.com
# Router should normalize to test@testmail.domain.com and match rule
AWS_PROFILE=ses-mail aws ses send-email \
  --from sender@testmail.domain.com \
  --destination "ToAddresses=test+newsletter@testmail.domain.com" \
  --message "Subject={Data='Test Plus Addressing'},Body={Text={Data='Testing +tag normalization'}}"
```

#### Test Domain Wildcard Matching

```bash
# Create domain wildcard rule
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-mail-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#*@testmail.domain.com"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "*@testmail.domain.com"},
    "action": {"S": "forward-to-gmail"},
    "target": {"S": "catchall@gmail.com"},
    "enabled": {"BOOL": true},
    "created_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "updated_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "description": {"S": "Catch-all wildcard"}
  }'

# Send email to any-address@testmail.domain.com
# Should match wildcard rule
```

#### Test DynamoDB Unavailable Scenario

```bash
# Test DynamoDB unavailable (temporarily remove permissions)
# Expected: Router should use fallback routing (bounce)

# Restore permissions after test
```

#### Test Gmail API Failure

```bash
# Test Gmail API failure (use invalid OAuth token)
# Expected: Message should retry and eventually go to DLQ
```

## Contributing Guidelines

### Terraform Conventions

**IMPORTANT**: Always use the Makefile for Terraform operations. Never run `terraform` commands directly.

#### Never Use `jsonencode` with IAM Policies

When writing Terraform, never use `jsonencode` where an `aws_iam_policy_document` can be used.

**Bad:**
```hcl
resource "aws_iam_role_policy" "example" {
  role = aws_iam_role.example.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "s3:GetObject"
      Resource = "*"
    }]
  })
}
```

**Good:**
```hcl
data "aws_iam_policy_document" "example" {
  statement {
    effect = "Allow"
    actions = ["s3:GetObject"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "example" {
  role = aws_iam_role.example.id
  policy = data.aws_iam_policy_document.example.json
}
```

#### Always Use AWS_PROFILE and ENV

```bash
# Always specify AWS_PROFILE and ENV
AWS_PROFILE=ses-mail make plan ENV=test

# Never run terraform directly
terraform plan  # ❌ WRONG
```

#### Automatic Formatting

`terraform fmt` is automatically run by Makefile targets. No need to run it manually.

### Task-Driven Development

1. **Read specification documents** in `.kiro/specs/{project}/`:
   - `requirements.md` - Project requirements
   - `design.md` - Technical design
   - `tasks.md` - Implementation tasks

2. **Follow tasks in order** from `tasks.md`

3. **A task is not complete until:**
   - The code is written
   - Updated appropriate documentation in `docs/` directory (SETUP.md, OPERATIONS.md, ARCHITECTURE.md, or DEVELOPMENT.md)
   - Ran `AWS_PROFILE=ses-mail make plan ENV=test >/dev/null`
   - Deployed with `AWS_PROFILE=ses-mail make apply ENV=test >/dev/null`
   - It is tested
   - Marked task as `[x]` in `tasks.md`
   - A git commit has been made

### Git Workflow

#### Never Skip Hooks

- **NEVER** use `--no-verify` or `--no-gpg-sign` flags
- **NEVER** use `git commit --amend` unless:
  1. User explicitly requested amend, OR commit succeeded but pre-commit hook auto-modified files
  2. HEAD commit was created by you in this conversation
  3. Commit has NOT been pushed to remote
- **NEVER** force push to main/master

#### Committing Changes

Only create commits when requested by the user. Follow these steps:

1. Run git commands in parallel:
   ```bash
   git status
   git diff
   git log
   ```

2. Analyze changes and draft concise commit message (1-2 sentences focusing on "why")

3. Add relevant files and create commit:
   ```bash
   git add <files>
   git commit -m "$(cat <<'EOF'
   Your commit message here.

   🤖 Generated with [Claude Code](https://claude.com/claude-code)

   Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
   EOF
   )"
   git status
   ```

4. **NEVER** push to remote unless user explicitly requests it

#### Creating Pull Requests

When user asks to create a PR:

1. Run in parallel:
   ```bash
   git status
   git diff
   git log [base-branch]...HEAD
   git diff [base-branch]...HEAD
   ```

2. Analyze ALL commits (not just latest)

3. Create PR:
   ```bash
   gh pr create --title "PR title" --body "$(cat <<'EOF'
   ## Summary
   - Bullet point 1
   - Bullet point 2

   ## Test plan
   - [ ] Test item 1
   - [ ] Test item 2

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

### Lambda Development

Lambda functions are in `terraform/modules/ses-mail/lambda/`:

- Source files committed to git
- Dependencies installed to `lambda/package/` via Makefile
- Archive created automatically during `make plan` or `make apply`
- Gmail API integration uses OAuth token from SSM Parameter Store

**Lambda handlers:**
- `router_enrichment.lambda_handler` - SNS event → DynamoDB lookup → EventBridge
- `gmail_forwarder.lambda_handler` - SQS event → Fetch from S3 → Gmail API → Delete from S3
- `bouncer.lambda_handler` - SQS event → Send bounce via SES
- `smtp_credential_manager.lambda_handler` - DynamoDB Streams → IAM user lifecycle

### IAM Role Naming Convention

- Router enrichment: `ses-mail-lambda-router-{env}`
- Gmail forwarder: `ses-mail-lambda-gmail-forwarder-{env}`
- Bouncer: `ses-mail-lambda-bouncer-{env}`
- SMTP credential manager: `ses-mail-lambda-credential-manager-{env}`
- Tag sync: `ses-mail-lambda-tag-sync-{env}`

### Code Style

- **Python**: Follow PEP 8
- **Terraform**: Use `terraform fmt` (automatically run by Makefile)
- **Documentation**: Use Markdown with proper headings and code blocks
- **Comments**: Only where logic isn't self-evident

### Documentation Updates

When making changes:

- Update appropriate guide in `docs/` directory:
  - `docs/SETUP.md` - Setup procedures
  - `docs/OPERATIONS.md` - Operational commands
  - `docs/ARCHITECTURE.md` - Technical design
  - `docs/DEVELOPMENT.md` - This file
- Keep `README.md` brief (it's the entry point, not the manual)
- Ensure code examples are tested and work

## Makefile Commands

### Terraform Operations

| Command | Description |
|---------|-------------|
| `make init ENV=test` | Initialize Terraform backend |
| `make validate ENV=test` | Validate Terraform configuration |
| `make plan ENV=test` | Create Terraform plan file |
| `make show-plan ENV=test` | Show existing plan (creates if needed) |
| `make apply ENV=test` | Apply Terraform plan |
| `make outputs ENV=test` | Show Terraform outputs |
| `make package ENV=test` | Package Lambda functions |
| `make plan-destroy ENV=test` | Create destroy plan |
| `make destroy ENV=test` | Apply destroy plan |
| `make clean` | Remove generated files |

### Usage Examples

```bash
# Standard deployment workflow (suppress output)
AWS_PROFILE=ses-mail make validate ENV=test
AWS_PROFILE=ses-mail make plan ENV=test >/dev/null
AWS_PROFILE=ses-mail make apply ENV=test >/dev/null

# Or without output redirection (to see full Terraform output)
AWS_PROFILE=ses-mail make validate ENV=test
AWS_PROFILE=ses-mail make plan ENV=test
AWS_PROFILE=ses-mail make apply ENV=test

# Show what would be deployed without creating plan
AWS_PROFILE=ses-mail terraform -chdir=terraform/environments/test plan

# Get infrastructure outputs
AWS_PROFILE=ses-mail make outputs ENV=test
```

**Note**: The `>/dev/null` redirect suppresses Terraform output. Remove it to see detailed plan and apply output.

### Makefile Features

The Makefile handles:

- Automatic S3 state bucket creation (`terraform-state-{account-id}`)
- Backend configuration with environment-specific state keys
- Lambda function packaging with dependencies
- Plan file creation and clean-up
- Automatic `terraform fmt` execution

## Terraform Conventions

### Multi-Environment Management

- Environments can share an AWS account or use separate accounts
- When sharing account (`join_existing_deployment` set), test environment:
  - Creates its own ruleset (for reference) but doesn't activate it
  - Adds rules to prod environment's active ruleset
  - Must be deployed after prod environment
- Environments isolated via separate `terraform.tfvars` and state files
- State key format: `ses-mail/{environment}.tfstate`
- Common module in `modules/ses-mail/` shared across environments

### Deployment Order

When test and prod share AWS account, deploy in this order:

1. **Prod first**: `AWS_PROFILE=ses-mail make apply ENV=prod >/dev/null`
2. **Test second**: `AWS_PROFILE=ses-mail make apply ENV=test >/dev/null`

Required because test adds rules to prod's active SES ruleset.

If environments use separate accounts, deploy in any order.

### Single-Table DynamoDB Design

When extending DynamoDB table:

- Use generic PK/SK keys with prefixed values (e.g., `ROUTE#`, `CONFIG#`, `METRICS#`)
- Denormalize key data into attributes for easier querying
- Use SK for versioning or hierarchical sorting (e.g., `RULE#v1`, `RULE#v2`)
- Add `entity_type` attribute for filtering queries

## Further Reading

- [Setup Guide](SETUP.md) - First-time deployment and configuration
- [Operations Guide](OPERATIONS.md) - Day-to-day operations and troubleshooting
- [Architecture Guide](ARCHITECTURE.md) - Technical deep-dive into system design
- [Terraform Module README](../terraform/modules/ses-mail/README.md) - Infrastructure details
