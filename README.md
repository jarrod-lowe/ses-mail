# SES Mail System

An AWS-based email receiving system that processes emails through SES and forwards them to Gmail via the Gmail API using a fully event-driven architecture.

## What It Does

- **Receives emails** via Amazon SES for your custom domains
- **Routes intelligently** based on configurable rules stored in DynamoDB
- **Forwards to Gmail** using Gmail API for seamless inbox integration
- **Monitors proactively** with CloudWatch alarms and X-Ray tracing
- **Handles failures gracefully** with automatic retry queues and recovery workflows

## Architecture Overview

```text
SES → S3 → SNS → SQS → EventBridge Pipes [Router Lambda] → Event Bus → Handler Queues → Lambdas
                                              ↓
                                          DynamoDB
                                      (Routing Rules)
```

**Key Components:**

- **SES** - Receives and validates incoming email (spam/virus/DKIM/SPF/DMARC)
- **S3** - Stores email messages temporarily
- **EventBridge Pipes** - Enriches messages with routing decisions via Router Lambda
- **DynamoDB** - Stores routing rules with hierarchical address matching
- **Handler Lambdas** - Process emails (Gmail forwarding, bouncing)
- **Step Functions** - Retry workflows for token expiration and failures
- **X-Ray** - Distributed tracing across the entire pipeline

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed technical design.

## Prerequisites

Before setting up, ensure you have:

- ✅ AWS account with SES production access enabled
- ✅ Google Cloud project with Gmail API enabled
- ✅ Terraform >= 1.0 installed
- ✅ AWS CLI configured with appropriate profile
- ✅ Python 3.x for OAuth token management scripts

## Quick Start

**New to this system?** Follow these steps:

1. **Initial Setup** → [docs/SETUP.md](docs/SETUP.md)
   - Configure Google OAuth credentials
   - Deploy AWS infrastructure with Terraform
   - Set up DNS records
   - Initialize OAuth tokens

2. **Configure Routing** → [docs/OPERATIONS.md#email-routing-management](docs/OPERATIONS.md#email-routing-management)
   - Add routing rules to DynamoDB
   - Test email forwarding

3. **Set Up Monitoring** → [docs/MONITORING.md](docs/MONITORING.md)
   - Subscribe to SNS alerts
   - Access CloudWatch dashboard

4. **Verify Everything Works** → [docs/DEVELOPMENT.md#integration-testing](docs/DEVELOPMENT.md#integration-testing)
   - Run integration tests
   - Send test emails

## Quick Reference

### Common Commands

| Task | Command |
| ---- | ------- |
| **Deploy infrastructure** | `AWS_PROFILE=ses-mail make apply ENV=test` |
| **Show Terraform plan** | `AWS_PROFILE=ses-mail make show-plan ENV=test` |
| **Refresh OAuth token** | `AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test` |
| **Add routing rule** | See [Operations Guide](docs/OPERATIONS.md#email-routing-management) |
| **View logs** | `AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow` |
| **Check queue depth** | `AWS_PROFILE=ses-mail aws sqs get-queue-attributes --queue-url <url> --attribute-names ApproximateNumberOfMessages` |
| **Run integration tests** | `AWS_PROFILE=ses-mail python3 scripts/integration_test.py --env test` |

### Key Infrastructure

| Resource | Description |
| -------- | ----------- |
| **S3 Bucket** | `ses-mail-storage-{account-id}-{env}` - Email storage |
| **DynamoDB Table** | `ses-mail-email-routing-{env}` - Routing rules |
| **SNS Topic** | `ses-email-processing-{env}` - Email notifications |
| **EventBridge Bus** | `ses-mail-email-routing-{env}` - Event routing |
| **CloudWatch Dashboard** | `ses-mail-dashboard-{env}` - Monitoring |

## Documentation

### For Users

- **[Setup Guide](docs/SETUP.md)** - First-time deployment and configuration (Google Cloud, AWS, Terraform, DNS, OAuth)
- **[Operations Guide](docs/OPERATIONS.md)** - Core operations (routing rules, OAuth tokens, SMTP credentials, backups)
- **[Monitoring Guide](docs/MONITORING.md)** - System monitoring, troubleshooting, and incident response
- **[Recovery Guide](docs/RECOVERY.md)** - DLQ management, retry workflows, and automation runbooks

### For Developers

- **[Architecture Guide](docs/ARCHITECTURE.md)** - Technical deep-dive into system design and components
- **[Development Guide](docs/DEVELOPMENT.md)** - Integration testing, contributing, and development workflow
- **[Upgrading Guide](docs/UPGRADING.md)** - Terraform, Lambda, and dependency upgrade procedures

## Project Structure

```text
terraform/
├── environments/
│   ├── test/              # Test environment configuration
│   └── prod/              # Production environment configuration
└── modules/
    └── ses-mail/          # Reusable SES mail module
        ├── iam.tf         # IAM roles and policies
        ├── ses.tf         # SES domain and rules
        ├── lambda.tf      # Lambda functions
        ├── dynamodb.tf    # Routing rules table
        └── lambda/
            ├── router_enrichment.py
            ├── gmail_forwarder.py
            └── bouncer.py

scripts/
├── refresh_oauth_token.py    # OAuth token refresh script
└── integration_test.py        # Integration test suite

docs/
├── SETUP.md                   # Setup guide
├── OPERATIONS.md              # Operations guide
├── ARCHITECTURE.md            # Architecture deep-dive
└── DEVELOPMENT.md             # Development guide
```

## Key Features

### Hierarchical Email Routing

Routes emails based on DynamoDB rules with intelligent matching:

1. **Exact match**: `user+tag@example.com`
2. **Normalized match**: `user@example.com` (supports Gmail plus addressing)
3. **Domain wildcard**: `*@example.com`
4. **Global wildcard**: `*` (default catch-all)

### Event-Driven Architecture

Fully serverless processing pipeline:

- SNS with X-Ray Active tracing
- EventBridge Pipes for enrichment
- SQS queues for reliability
- Lambda for processing
- Step Functions for retry workflows
- Dead letter queues for failure handling

### OAuth Token Management

Automated token expiration monitoring:

- EventBridge Rule triggers Step Function every 5 minutes
- CloudWatch metrics track time until expiration
- Two-tier alarms (24-hour warning, 6-hour critical)
- SNS notifications for proactive token refresh
- Automatic retry queue processing after refresh

### Multi-Environment Support

- Supports separate AWS accounts or shared account deployment
- Environment-specific state management
- Shared Terraform modules for consistency
- Configurable domain and routing per environment

### SMTP Credential Management

Automated IAM user provisioning for SMTP:

- DynamoDB Streams trigger credential creation
- KMS-encrypted credential storage
- Per-user email sending restrictions
- Automatic IAM cleanup on deletion

## Monitoring & Observability

- **CloudWatch Dashboard** - Pre-configured dashboard with key metrics including outbound email delivery rates
- **Outbound Email Metrics** - Automatic tracking of sends, deliveries, bounces, and complaints with no SMTP client changes needed
- **X-Ray Tracing** - End-to-end distributed tracing from SES to Gmail
- **Structured Logging** - JSON-formatted logs with correlation IDs
- **CloudWatch Alarms** - Proactive alerting for failures, high bounce rates (>5%), and complaint rates (>0.1%)
- **SNS Notifications** - Email/SMS alerts for critical events

See [docs/MONITORING.md](docs/MONITORING.md) and [docs/OPERATIONS.md#outbound-email-monitoring](docs/OPERATIONS.md#outbound-email-monitoring) for complete guides.

## Troubleshooting

For common issues and solutions, see:

- **OAuth Issues** → [docs/OPERATIONS.md#oauth-token-management](docs/OPERATIONS.md#oauth-token-management)
- **Email Not Forwarding** → [docs/MONITORING.md#common-issues-and-solutions](docs/MONITORING.md#common-issues-and-solutions)
- **Terraform Errors** → [docs/SETUP.md](docs/SETUP.md)
- **Integration Test Failures** → [docs/DEVELOPMENT.md#troubleshooting-test-failures](docs/DEVELOPMENT.md#troubleshooting-test-failures)

## Getting Help

- **Documentation**: Start with [docs/SETUP.md](docs/SETUP.md) for initial setup
- **Operations**: See [docs/OPERATIONS.md](docs/OPERATIONS.md) for day-to-day tasks
- **Architecture**: Review [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for system design
- **Development**: Check [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for testing and contributing

## License

This project is for internal use. See organization policies for details.
