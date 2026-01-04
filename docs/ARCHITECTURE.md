# Architecture Guide

This document provides a technical deep-dive into the SES Mail system architecture, design patterns, and AWS service integrations.

## Table of Contents

- [System Overview](#system-overview)
- [Email Processing Flow](#email-processing-flow)
- [Outbound Email Metrics Architecture](#outbound-email-metrics-architecture)
- [Component Details](#component-details)
- [Design Patterns](#design-patterns)
- [AWS Integrations](#aws-integrations)
- [Security Architecture](#security-architecture)
- [Performance & Scalability](#performance--scalability)

## System Overview

### Purpose

SES Mail is a serverless email receiving and forwarding system built on AWS that:

- **Receives emails** via Amazon SES for custom domains
- **Routes intelligently** using DynamoDB-based rules with hierarchical matching
- **Forwards to Gmail** via Gmail API for seamless inbox integration
- **Handles failures gracefully** with automatic retry and recovery workflows

### Design Goals

1. **Fully serverless**: No servers to manage, automatic scaling
2. **Event-driven**: Asynchronous processing with decoupled components
3. **Highly available**: Multi-AZ deployment with automatic failover
4. **Observable**: End-to-end tracing with X-Ray and structured logging
5. **Cost-effective**: Pay-per-use billing with PAY_PER_REQUEST DynamoDB
6. **Secure**: Encryption at rest (KMS), in transit (TLS), IAM least-privilege

### High-Level Architecture

```
┌─────────┐     ┌────┐     ┌─────┐     ┌─────┐     ┌──────────────┐     ┌──────────┐
│   SES   │────▶│ S3 │────▶│ SNS │────▶│ SQS │────▶│ EventBridge  │────▶│   SQS    │
│         │     └────┘     └─────┘     └─────┘     │    Pipes     │     │ (Handler)│
└─────────┘                                        │  [Enrichment]│     └──────────┘
                                                   └──────┬───────┘           │
                                                          │                   │
                                                   ┌──────▼────┐              │
                                                   │  Router   │              │
                                                   │  Lambda   │              │
                                                   └──────┬────┘              │
                                                          │                   │
                                                   ┌──────▼─────┐             │
                                                   │  DynamoDB  │             │
                                                   │  (Rules)   │             │
                                                   └────────────┘             │
                                                                              │
                                                   ┌──────────────────────────▼────┐
                                                   │ EventBridge Event Bus          │
                                                   └───────┬───────────┬────────────┘
                                                           │           │
                                            ┌──────────────▼─┐   ┌─────▼──────────┐
                                            │ Gmail Forwarder│   │    Bouncer     │
                                            │     Lambda     │   │     Lambda     │
                                            └────────┬───────┘   └────────────────┘
                                                     │
                                            ┌────────▼─────┐
                                            │  Gmail API   │
                                            └──────────────┘
```

## Email Processing Flow

### Step-by-Step Flow

1. **Email Receipt (SES)**
   - External sender sends email to `user@example.com`
   - SES receives email and performs validation:
     - Spam detection (SpamAssassin)
     - Virus scanning (ClamAV)
     - SPF verification
     - DKIM verification
     - DMARC policy check

2. **Storage (S3)**
   - SES stores complete email message in S3 bucket: `ses-mail-storage-{account-id}-{env}/emails/{messageId}`
   - Bucket lifecycle: Messages deleted after 7 days (configurable)
   - Server-side encryption with AES256

3. **Notification (SNS)**
   - SES publishes notification to SNS topic: `ses-email-processing-{env}`
   - X-Ray Active tracing initiates distributed trace
   - Notification includes: messageId, source, destination, spam/virus verdicts

4. **Queueing (SQS)**
   - SNS delivers message to SQS input queue: `ses-email-input-{env}`
   - Queue configuration:
     - Visibility timeout: 30 seconds
     - Receive message wait time: 20 seconds (long polling)
     - Max receive count: 3 (then moves to DLQ)
     - Message retention: 4 days

5. **Enrichment (EventBridge Pipes)**
   - Pipe: `ses-email-router-{env}` automatically polls SQS queue
   - Invokes router lambda for each message
   - Waits for enrichment response
   - Publishes enriched message to EventBridge Event Bus
   - Logs execution details to CloudWatch

6. **Routing Decision (Router Lambda)**
   - Lambda: `ses-mail-router-enrichment-{env}`
   - Performs hierarchical DynamoDB lookups (exact → normalized → wildcard → global)
   - Extracts security verdicts from SES receipt
   - Returns routing decision with action and target
   - Adds X-Ray annotations for traceability

7. **Event Routing (EventBridge Event Bus)**
   - Event Bus: `ses-mail-email-routing-{env}`
   - Evaluates routing rules based on `action` field:
     - `forward-to-gmail` → routes to Gmail forwarder queue
     - `bounce` → routes to bouncer queue
   - Multiple targets can receive same event

8. **Handler Processing (SQS → Lambda)**
   - **Gmail Forwarder Path**:
     - SQS queue: `ses-gmail-forwarder-{env}`
     - Lambda: `ses-mail-gmail-forwarder-{env}`
     - Fetches email from S3
     - Authenticates with Gmail API using OAuth
     - Imports message to Gmail inbox
     - Deletes email from S3

   - **Bouncer Path**:
     - SQS queue: `ses-bouncer-{env}`
     - Lambda: `ses-mail-bouncer-{env}`
     - Generates bounce message
     - Sends via SES

9. **Error Handling**
   - Each queue has Dead Letter Queue (DLQ)
   - Failed messages move to DLQ after max receive count
   - CloudWatch alarms monitor DLQ depth
   - Step Functions workflow processes retry queue for OAuth token expiration

### Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     Email Processing Timeline                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  0s: Email arrives at SES                                      │
│      ├─ Spam/virus scan                                        │
│      └─ Store in S3                                            │
│                                                                 │
│  <1s: SNS notification published                               │
│       └─ X-Ray trace initiated                                 │
│                                                                 │
│  <2s: SQS receives message                                     │
│       └─ Long polling reduces latency                          │
│                                                                 │
│  <3s: EventBridge Pipes triggers router lambda                 │
│       ├─ DynamoDB lookup (single-digit ms)                     │
│       └─ Routing decision made                                 │
│                                                                 │
│  <4s: EventBridge routes to handler queue                      │
│                                                                 │
│  <6s: Gmail forwarder lambda processes                         │
│       ├─ Fetch from S3 (~100ms)                                │
│       ├─ Gmail API import (~1s)                                │
│       └─ Delete from S3 (~100ms)                               │
│                                                                 │
│  <7s: Email appears in Gmail inbox                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Typical latency**: 5-10 seconds from SES receipt to Gmail inbox

## Outbound Email Metrics Architecture

The system tracks all outbound emails sent via SES SMTP using an event-driven metrics pipeline. Metrics are automatically published to CloudWatch for monitoring delivery success, bounce rates, and spam complaints.

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Outbound Email Metrics Pipeline                                │
│                                                                  │
│  SMTP Client                                                     │
│       │                                                          │
│       ├──> SES SMTP (port 587)                                   │
│       │         │                                                │
│       │         ├──> SES Configuration Set (auto-associated)     │
│       │         │         │                                      │
│       │         │         ├──> Event Destination: Send           │
│       │         │         ├──> Event Destination: Delivery       │
│       │         │         ├──> Event Destination: Bounce/Reject  │
│       │         │         └──> Event Destination: Complaint      │
│       │         │                     │                          │
│       │         │                     v                          │
│       │         │         ┌───────────────────────┐              │
│       │         │         │  SNS Topics (4)       │              │
│       │         │         │  - outbound-send      │              │
│       │         │         │  - outbound-delivery  │              │
│       │         │         │  - outbound-bounce    │              │
│       │         │         │  - outbound-complaint │              │
│       │         │         └───────────────────────┘              │
│       │         │                     │                          │
│       │         │                     v                          │
│       │         │         ┌───────────────────────┐              │
│       │         │         │  Lambda Function      │              │
│       │         │         │  outbound_metrics_    │              │
│       │         │         │  publisher.py         │              │
│       │         │         │                       │              │
│       │         │         │  - Parse SES events   │              │
│       │         │         │  - Classify bounces   │              │
│       │         │         │  - Batch metrics      │              │
│       │         │         └───────────────────────┘              │
│       │         │                     │                          │
│       │         │                     v                          │
│       │         │         ┌───────────────────────┐              │
│       │         │         │  CloudWatch Metrics   │              │
│       │         │         │  SESMail/{env}        │              │
│       │         │         │                       │              │
│       │         │         │  - OutboundSend       │              │
│       │         │         │  - OutboundDelivery   │              │
│       │         │         │  - OutboundBounce     │              │
│       │         │         │  - OutboundBounceHard │              │
│       │         │         │  - OutboundBounceSoft │              │
│       │         │         │  - OutboundComplaint  │              │
│       │         │         │  - OutboundReject     │              │
│       │         │         └───────────────────────┘              │
│       │         │                     │                          │
│       │         │         ┌───────────┴───────────┐              │
│       │         │         v                       v              │
│       │         │   ┌──────────────┐      ┌──────────────┐      │
│       │         │   │  Dashboard   │      │  Alarms      │      │
│       │         │   │  4 Widgets   │      │  Bounce >5%  │      │
│       │         │   │              │      │  Complaint   │      │
│       │         │   └──────────────┘      │  >0.1%       │      │
│       │         │                         └──────────────┘      │
│       │         │                                                │
│       │         v                                                │
│       │   Recipient's Inbox                                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

#### SES Configuration Set

- **Resource**: `aws_ses_configuration_set.outbound`
- **Purpose**: Enables event tracking for outbound emails
- **Features**:
  - Reputation metrics tracking (bounce/complaint rates)
  - Event destinations for send, delivery, bounce, reject, complaint
  - Automatically associated with all verified domains
- **Association**: Via `aws_sesv2_email_identity` with `configuration_set_name`
- **Impact**: Zero - works transparently without SMTP client changes

#### Event Destinations

Each event type has a dedicated SNS topic:
- **Send events**: Published when SES accepts email for delivery
- **Delivery events**: Published when recipient server accepts email
- **Bounce events**: Published when email bounces (hard or soft)
- **Reject events**: Published when SES rejects email (invalid sender, etc.)
- **Complaint events**: Published when recipient marks email as spam

All SNS topics have:
- X-Ray Active tracing enabled
- IAM policies allowing SES to publish
- Lambda subscription for metrics processing

#### Outbound Metrics Publisher Lambda

**Function**: `terraform/modules/ses-mail/lambda/outbound_metrics_publisher.py`

**Responsibilities**:
1. Parse SNS notifications from SES event destinations
2. Extract event type (send, delivery, bounce, reject, complaint)
3. Classify bounce type:
   - **Hard bounce**: Permanent failure (bad address, domain doesn't exist)
   - **Soft bounce**: Temporary failure (mailbox full, server down)
4. Batch metrics (max 20 per PutMetricData call)
5. Publish to CloudWatch `SESMail/{environment}` namespace

**Runtime**: Python 3.12 with AWS Lambda Powertools
**Memory**: 256 MB
**Timeout**: 30 seconds
**Tracing**: X-Ray Active

**Bounce Classification Logic**:
```python
# Hard bounces (permanent failures)
bounce_type = "Permanent"
bounce_subtypes = ["General", "NoEmail", "Suppressed"]

# Soft bounces (temporary failures)
bounce_type = "Transient"
bounce_subtypes = ["General", "MailboxFull", "MessageTooLarge", "ContentRejected"]
```

**Error Handling**:
- Malformed events logged but don't fail function
- CloudWatch PutMetricData failures logged with stack trace
- X-Ray subsegments for debugging

#### CloudWatch Metrics

**Namespace**: `SESMail/{environment}`

| Metric | Unit | Description |
|--------|------|-------------|
| OutboundSend | Count | Emails accepted by SES |
| OutboundDelivery | Count | Emails successfully delivered |
| OutboundBounce | Count | Total bounces (hard + soft) |
| OutboundBounceHard | Count | Permanent bounces |
| OutboundBounceSoft | Count | Temporary bounces |
| OutboundComplaint | Count | Spam complaints |
| OutboundReject | Count | SES rejections |

**Retention**: Default CloudWatch retention (indefinite for metrics)

#### Dashboard Widgets

Four widgets added to existing `ses-mail-dashboard-{environment}`:

1. **Outbound Email Volume** (line graph, y=44, x=0, 12x6)
   - Metrics: Send, Delivery, Bounce, Complaint, Reject
   - Period: 5 minutes
   - Stat: Sum

2. **Outbound Delivery & Error Rates** (line graph, y=44, x=12, 12x6)
   - Metric Math:
     - `delivery_rate = (delivery / send) * 100`
     - `bounce_rate = (bounce / send) * 100`
     - `complaint_rate = (complaint / send) * 100`
   - Annotations:
     - Warning: 5% bounce rate
     - Critical: 10% bounce rate
   - Y-axis: Percentage (0-100%)

3. **Outbound Bounce Types** (stacked area, y=50, x=0, 12x6)
   - Metrics: BounceHard, BounceSoft
   - Period: 5 minutes
   - Helps distinguish address hygiene (hard) vs delivery issues (soft)

4. **AWS SES Reputation Metrics** (line graph, y=50, x=12, 12x6)
   - Native AWS/SES metrics:
     - `Reputation.BounceRate`
     - `Reputation.ComplaintRate`
   - Dimension: `ConfigurationSet = ses-mail-outbound-{env}`
   - Annotations:
     - Warning: 0.1% complaint rate
     - Critical: 5% bounce rate

#### CloudWatch Alarms

**High Bounce Rate Alarm**:
- **Metric**: `(OutboundBounce / OutboundSend) * 100`
- **Threshold**: 5%
- **Evaluation**: 2 consecutive 5-minute periods
- **Datapoints**: 2 out of 2
- **Action**: SNS topic notification
- **Rationale**: Industry standard; sustained >10% risks SES suspension

**High Complaint Rate Alarm**:
- **Metric**: `(OutboundComplaint / OutboundSend) * 100`
- **Threshold**: 0.1%
- **Evaluation**: 2 consecutive 5-minute periods
- **Datapoints**: 2 out of 2
- **Action**: SNS topic notification
- **Severity**: CRITICAL - AWS may suspend account above 0.1%

### Data Flow

1. **Email Sent** (t=0s)
   - SMTP client connects to `email-smtp.ap-southeast-2.amazonaws.com:587`
   - SES receives email from verified domain
   - Configuration Set automatically applied (domain-level association)
   - SES publishes "Send" event to SNS topic

2. **Event Processing** (t=0-2s)
   - SNS delivers notification to Lambda (async)
   - Lambda parses SES event JSON
   - Extracts event type, timestamp, messageId
   - Publishes `OutboundSend=1` to CloudWatch

3. **Delivery/Bounce/Complaint** (t=varies)
   - SES attempts delivery to recipient server
   - Outcome event published to appropriate SNS topic:
     - **Success**: "Delivery" event → `OutboundDelivery=1`
     - **Bounce**: "Bounce" event → `OutboundBounce=1`, plus Hard/Soft classification
     - **Complaint**: "Complaint" event (hours/days later) → `OutboundComplaint=1`
     - **Reject**: "Reject" event → `OutboundReject=1`

4. **Metrics Aggregation** (t=real-time)
   - CloudWatch aggregates metrics by period (5min default)
   - Dashboard updates every minute
   - Alarms evaluate every 5 minutes (2 datapoints required)

### Design Decisions

**Why SESv2 instead of SESv1?**
- SESv1 (`aws_ses_domain_identity`) doesn't support Configuration Set association
- SESv2 (`aws_sesv2_email_identity`) allows `configuration_set_name` attribute
- Migration from v1 to v2 required but DKIM tokens preserved (no DNS changes)

**Why SNS instead of direct CloudWatch Events?**
- SES event destinations only support SNS, Kinesis Firehose, or Pinpoint
- SNS provides natural fan-out if we add more subscribers later
- Lambda can batch multiple SNS messages for efficient CloudWatch puts

**Why custom metrics instead of native SES metrics?**
- Native SES metrics lack granularity (no send vs delivery breakdown)
- Custom metrics allow bounce type classification (hard vs soft)
- Enables metric math for rate calculations (bounce%, complaint%)
- Metrics in existing `SESMail/{environment}` namespace for consistency

**Why account-level instead of per-domain metrics?**
- Simpler implementation (no domain extraction from events)
- Adequate for single-domain deployments
- Can add domain dimension later if needed (Phase 2)

### Cost Estimate

**Monthly costs for 10,000 outbound emails**:
- SNS notifications: 40,000 events × $0.50/million = **$0.02**
- Lambda invocations: 40,000 × $0.20/million = **$0.01** (within free tier)
- Lambda duration: 40,000 × 200ms × $0.0000166667/GB-sec × 0.256GB = **$0.03**
- CloudWatch metrics: 7 custom metrics = **$0** (within free tier of 10)
- CloudWatch dashboard: 1 dashboard = **$3.00**
- CloudWatch alarms: 2 alarms = **$0** (within free tier of 10)

**Total: ~$3/month** (primarily dashboard cost)

### Scalability

**Current capacity**:
- SNS: 100,000 messages/sec per topic (far exceeds needs)
- Lambda: 1,000 concurrent executions (default account limit)
- CloudWatch: 40 transactions/sec per dimension (publish limit)

**Bottleneck**: CloudWatch PutMetricData at ~40 TPS
- At 4 events per email (send, delivery, bounce/reject, possible complaint)
- Theoretical max: ~10 emails/sec = 36,000 emails/hour
- Well above expected load for single-domain usage

**If scaling needed**:
- Batch more aggressively (currently max 20 metrics/call)
- Use embedded metric format (EMF) for higher throughput
- Request CloudWatch quota increase

### Monitoring the Metrics System

**Health checks**:
1. SNS topic subscriptions active
2. Lambda function not throttling/erroring
3. CloudWatch metrics publishing within 60 seconds
4. Dashboard widgets displaying data

**Troubleshooting**:
- Lambda logs: `/aws/lambda/ses-mail-outbound-metrics-{env}`
- X-Ray traces: Filter by annotation `service=ses-mail-outbound-metrics`
- Test with SES Mailbox Simulator: `success@simulator.amazonses.com`

## Component Details

### Amazon SES

**Purpose**: Email receiving and domain verification

**Configuration:**
- Verified domains via TXT records
- DKIM signing (3 CNAME records per domain)
- DMARC policy enforcement
- MTA-STS support (optional)
- Active receipt rule set: `ses-ruleset-{env}`

**Receipt Rule:**
- Action 1: Store in S3 bucket
- Action 2: Publish to SNS topic
- Executes as single atomic action

**Spam/Virus Protection:**
- SpamAssassin integration (spam scoring)
- ClamAV virus scanning
- Automatic DKIM/SPF/DMARC validation

### Amazon S3

**Purpose**: Temporary email storage

**Bucket:** `ses-mail-storage-{account-id}-{env}`

**Configuration:**
- Server-side encryption: AES256
- Lifecycle policy: Delete objects after 7 days
- Versioning: Disabled (not needed for temporary storage)
- Public access: Blocked (bucket policy allows SES and Lambdas only)

**Object key format:** `emails/{messageId}`

### Amazon SNS

**Purpose**: Fan-out email notifications

**Topic:** `ses-email-processing-{env}`

**Configuration:**
- X-Ray Active tracing enabled
- Subscription: SQS input queue with raw message delivery
- Access policy: SES can publish, SQS can subscribe

**Benefits:**
- Initiates distributed tracing
- Enables future fan-out to additional processors
- Decouples SES from downstream processing

### Amazon SQS

**Queues:**

1. **Input Queue**: `ses-email-input-{env}`
   - Source: SNS topic
   - Consumer: EventBridge Pipes
   - Visibility timeout: 30s
   - Message retention: 4 days
   - DLQ: `ses-email-input-dlq-{env}`

2. **Gmail Forwarder Queue**: `ses-gmail-forwarder-{env}`
   - Source: EventBridge Event Bus
   - Consumer: Gmail forwarder Lambda
   - Visibility timeout: 5 minutes
   - DLQ: `ses-gmail-forwarder-dlq-{env}`

3. **Bouncer Queue**: `ses-bouncer-{env}`
   - Source: EventBridge Event Bus
   - Consumer: Bouncer Lambda

4. **Retry Queue**: `ses-mail-gmail-forwarder-retry-{env}`
   - Purpose: OAuth token expiration failures
   - Visibility timeout: 15 minutes
   - Message retention: 14 days

**Common Configuration:**
- Long polling enabled (20s wait time)
- Redrive policy: 3 attempts before DLQ
- CloudWatch alarms on DLQ depth and message age

### EventBridge Pipes

**Pipe:** `ses-email-router-{env}`

**Purpose**: Serverless message enrichment and routing

**Configuration:**
- Source: SQS input queue
- Enrichment: Router lambda function
- Target: EventBridge Event Bus
- Batch size: 1 (process messages individually)
- Maximum batching window: 0 seconds
- Logging: CloudWatch Logs (INFO level with execution data)

**Benefits:**
- Managed polling (no custom code)
- Built-in retries and error handling
- CloudWatch integration for monitoring
- Preserves X-Ray trace context

### AWS Lambda

#### Router Enrichment Lambda

**Function:** `ses-mail-router-enrichment-{env}`

**Purpose**: Query DynamoDB for routing rules and enrich messages

**Configuration:**
- Runtime: Python 3.12
- Memory: 128 MB
- Timeout: 30 seconds
- X-Ray Active tracing
- VPC: None (DynamoDB is public endpoint)

**Environment Variables:**
- `ROUTING_TABLE`: DynamoDB table name
- `LOG_LEVEL`: INFO

**IAM Permissions:**
- `dynamodb:GetItem` on routing table
- `xray:PutTraceSegments`
- `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`

**Key Logic:**
1. Extract recipient addresses from SES event
2. For each recipient, perform hierarchical lookup:
   - Try exact match: `ROUTE#user+tag@example.com`
   - Try normalized match: `ROUTE#user@example.com` (remove +tag)
   - Try domain wildcard: `ROUTE#*@example.com`
   - Try global wildcard: `ROUTE#*`
3. Extract security verdicts from SES receipt
4. Build routing decision with actions and targets
5. Add X-Ray annotations: messageId, source, action

#### Gmail Forwarder Lambda

**Function:** `ses-mail-gmail-forwarder-{env}`

**Purpose**: Fetch email from S3 and import to Gmail via API

**Configuration:**
- Runtime: Python 3.12
- Memory: 512 MB (needs memory for email parsing)
- Timeout: 5 minutes
- X-Ray Active tracing
- Lambda layers: Google API client libraries

**Environment Variables:**
- `S3_BUCKET`: Email storage bucket
- `OAUTH_CLIENT_CREDENTIALS_PARAM`: SSM parameter for OAuth client
- `OAUTH_REFRESH_TOKEN_PARAM`: SSM parameter for refresh token
- `RETRY_QUEUE_URL`: Retry queue for token expiration failures

**IAM Permissions:**
- `s3:GetObject`, `s3:DeleteObject` on email bucket
- `ssm:GetParameter` on OAuth credential parameters
- `kms:Decrypt` for SecureString parameters
- `sqs:SendMessage` to retry queue
- `sqs:DeleteMessage` from handler queue
- `gmail.insert` via OAuth (not IAM)

**Key Logic:**
1. Retrieve OAuth credentials from SSM
2. Generate fresh access token from refresh token
3. Build Gmail API service
4. Fetch email from S3
5. Parse MIME message
6. Import to Gmail using `users.messages.import` API
7. Delete email from S3
8. If OAuth error: Queue to retry queue and exit

#### Bouncer Lambda

**Function:** `ses-mail-bouncer-{env}`

**Purpose**: Generate and send bounce messages

**Configuration:**
- Runtime: Python 3.12
- Memory: 256 MB
- Timeout: 1 minute

**Key Logic:**
1. Extract original sender and recipient
2. Generate bounce message (RFC 3464 format)
3. Send via SES `send_raw_email`

#### SMTP Credential Manager Lambda

**Function:** `ses-mail-credential-manager-{env}`

**Purpose**: Automated SMTP credential lifecycle management

**Trigger**: DynamoDB Streams from routing table

**Configuration:**
- Runtime: Python 3.12
- Memory: 256 MB
- Timeout: 1 minute
- Stream batch size: 1

**Key Logic:**

INSERT event (status="pending"):
1. Create IAM user: `ses-smtp-user-{username}-{timestamp}`
2. Generate IAM access keys
3. Convert secret key to SMTP password (AWS SigV4 algorithm)
4. Encrypt credentials with KMS
5. Update DynamoDB record with status="active" and encrypted credentials

REMOVE event:
1. List and delete all access keys for user
2. List and delete all inline policies
3. Delete IAM user
4. Publish CloudWatch metrics

### Amazon DynamoDB

**Table:** `ses-mail-email-routing-{env}`

**Purpose**: Store routing rules and SMTP credentials

**Configuration:**
- Billing mode: PAY_PER_REQUEST (no provisioned capacity)
- Partition key (PK): String
- Sort key (SK): String
- DynamoDB Streams: Enabled (NEW_AND_OLD_IMAGES)

**Table Design (Single-Table Pattern):**

Entity types stored in same table using generic PK/SK:

1. **Routing Rules**:
   - PK: `ROUTE#<email-pattern>`
   - SK: `RULE#v1`
   - Attributes: entity_type, recipient, action, target, enabled, timestamps

2. **SMTP Credentials**:
   - PK: `SMTP_USER#<username>`
   - SK: `CREDENTIALS#v1`
   - Attributes: entity_type, username, iam_user_arn, encrypted_credentials, status

**Access Patterns:**

1. Get routing rule: `GetItem` with PK=`ROUTE#email` and SK=`RULE#v1`
2. List routing rules: `Query` with entity_type=ROUTE (GSI recommended for production)
3. Get SMTP credentials: `GetItem` with PK=`SMTP_USER#username`

**DynamoDB Streams:**
- Stream ARN: Available via `describe-table`
- View type: NEW_AND_OLD_IMAGES (captures before and after)
- Triggers: Credential manager Lambda
- Batch size: 1 record at a time

### EventBridge Event Bus

**Event Bus:** `ses-mail-email-routing-{env}`

**Purpose**: Route enriched messages to appropriate handlers

**Rules:**

1. **Gmail Forwarder Rule** (`route-to-gmail-{env}`):
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
   Target: SQS queue `ses-gmail-forwarder-{env}`

2. **Bouncer Rule** (`route-to-bouncer-{env}`):
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
   Target: SQS queue `ses-bouncer-{env}`

**Logging:**
- CloudWatch log group: `/aws/events/ses-mail-email-routing-{env}`
- Log level: INFO with execution data
- Retention: 30 days

### AWS Step Functions

**State Machine:** `ses-mail-gmail-forwarder-retry-processor-{env}`

**Purpose**: Process retry queue after OAuth token refresh

**Workflow:**
1. Receive SQS messages from retry queue (batch of 10)
2. For each message:
   - Invoke Gmail forwarder Lambda
   - Wait for response
   - If success: Delete from retry queue
   - If failure: Retry with exponential backoff (30s, 60s, 120s)
   - If permanent failure: Move to retry DLQ
3. Continue processing until retry queue empty

**Execution Trigger:**
- Manual: Via OAuth refresh script
- Scheduled: Every hour (checks if queue has messages)

**State Machine:** `ses-mail-gmail-token-monitor-{env}`

**Purpose**: Monitor OAuth token expiration

**Workflow:**
1. Get token metadata from SSM Parameter Store
2. Calculate seconds until expiration using JSONata
3. Publish metric to CloudWatch
4. Run every 5 minutes (EventBridge Rule trigger)

**JSONata Expression:**
```jsonata
{
  "namespace": "SESMail/test",
  "metricName": "TokenSecondsUntilExpiration",
  "value": expires_at_epoch - ($millis() / 1000),
  "unit": "Seconds"
}
```

### Amazon CloudWatch

**Dashboard:** `ses-mail-dashboard-{env}`

**Widgets:**
- Email processing metrics (accepted, bounced, spam)
- Lambda metrics (invocations, errors, duration)
- Queue metrics (depth, age, DLQ)
- Token expiration countdown
- SMTP credential operations

**Alarms:**

1. **Token Expiration Alarms**:
   - `ses-mail-gmail-token-expiring-warning-{env}`: 24-hour threshold
   - `ses-mail-gmail-token-expiring-critical-{env}`: 6-hour threshold

2. **DLQ Alarms**:
   - `ses-mail-gmail-forwarder-dlq-messages-{env}`: DLQ has messages
   - `ses-mail-gmail-forwarder-retry-dlq-messages-{env}`: Retry DLQ has messages

3. **Lambda Error Alarms**:
   - Per-function alarms on error rates

4. **Queue Age Alarms**:
   - Alert if messages older than threshold

**Log Groups:**
- `/aws/lambda/ses-mail-router-enrichment-{env}`
- `/aws/lambda/ses-mail-gmail-forwarder-{env}`
- `/aws/lambda/ses-mail-bouncer-{env}`
- `/aws/lambda/ses-mail-credential-manager-{env}`
- `/aws/pipes/ses-email-router-{env}`
- `/aws/events/ses-mail-email-routing-{env}`
- `/aws/states/ses-mail-gmail-token-monitor-{env}`

**Retention:** 30 days (configurable)

**Structured Logging:**
- JSON format
- Correlation IDs for tracing
- Log levels: DEBUG, INFO, WARNING, ERROR

### AWS X-Ray

**Purpose**: Distributed tracing across the email processing pipeline

**Trace Propagation:**
1. SNS topic (Active tracing) → initiates trace
2. SQS queues → propagate trace context
3. EventBridge Pipes → maintain trace context
4. Router Lambda → add custom annotations
5. EventBridge Event Bus → propagate to handlers
6. Handler Lambdas → continue trace

**Custom Annotations:**
- `messageId`: SES message ID
- `source`: Email sender
- `recipient`: Email recipient
- `action`: Routing decision (forward-to-gmail, bounce)
- `routing_match`: Which rule matched (exact, normalized, wildcard)

**Service Map Shows:**
- End-to-end latency breakdown
- Error rates per component
- Bottlenecks and retry patterns

## Design Patterns

### Single-Table DynamoDB Design

**Rationale**: Use one table for multiple entity types to:
- Reduce costs (one table vs. many)
- Simplify access control (one IAM policy)
- Enable future extensibility without schema changes

**Implementation:**
- Generic PK/SK keys with prefixed values
- Entity type denormalized in attributes
- Prefixes: `ROUTE#`, `SMTP_USER#`, `CONFIG#` (future)

**Benefits:**
- Add new entity types without new tables
- Consistent access patterns
- Atomic transactions within partition key

**Trade-offs:**
- More complex queries (need to filter by entity_type)
- Less obvious schema (requires documentation)

**Future Extensions:**
- `CONFIG#<setting>` for system configuration
- `METRICS#<date>` for usage metrics
- `AUDIT#<timestamp>` for audit logs

### Hierarchical Address Matching

**Rationale**: Support flexible routing rules from specific to general

**Lookup Order:**
1. Exact match: `user+tag@example.com`
2. Normalized match: `user@example.com` (Gmail plus addressing)
3. Domain wildcard: `*@example.com`
4. Global wildcard: `*` (catch-all)

**Implementation:**
```python
def lookup_routing_rule(email):
    # Try exact match
    rule = dynamodb.get_item(PK=f"ROUTE#{email}")
    if rule: return rule

    # Try normalized (remove +tag)
    normalized = remove_plus_tag(email)
    rule = dynamodb.get_item(PK=f"ROUTE#{normalized}")
    if rule: return rule

    # Try domain wildcard
    domain = extract_domain(email)
    rule = dynamodb.get_item(PK=f"ROUTE#*@{domain}")
    if rule: return rule

    # Try global wildcard
    rule = dynamodb.get_item(PK="ROUTE#*")
    if rule: return rule

    # No match - use default (bounce)
    return default_bounce_rule()
```

**Benefits:**
- Flexibility: Support specific overrides and broad defaults
- Gmail compatibility: Plus addressing works naturally
- Fallback: Always have a default action

**Performance:**
- 4 DynamoDB GetItem calls maximum (single-digit milliseconds each)
- Early exit on first match
- No scans required (all queries use PK)

### Event-Driven Architecture

**Rationale**: Decouple components for reliability and scalability

**Principles:**
1. **Asynchronous processing**: No synchronous dependencies
2. **At-least-once delivery**: Idempotent handlers tolerate duplicates
3. **Dead letter queues**: Capture failures for analysis
4. **Retry with backoff**: Automatic retries with exponential backoff

**Benefits:**
- **Fault isolation**: One component failure doesn't cascade
- **Independent scaling**: Each component scales independently
- **Easy extension**: Add new handlers without changing existing ones
- **Observability**: Each integration point is traceable

**Trade-offs:**
- **Eventual consistency**: Small delay between SES receipt and Gmail delivery
- **Complexity**: More components to manage and monitor
- **Debugging**: Distributed traces required to follow message flow

### OAuth Token Refresh Strategy

**Challenge**: Google OAuth testing mode expires tokens every 7 days

**Solution**: Multi-layer monitoring and automatic retry

**Architecture:**
1. **Proactive monitoring**: Step Function checks expiration every 5 minutes
2. **CloudWatch alarms**: Two-tier alerting (24hr warning, 6hr critical)
3. **SNS notifications**: Email/SMS alerts to administrators
4. **Automatic retry queueing**: Lambda detects expiration and queues for retry
5. **Manual refresh**: Administrator runs script to get new token
6. **Automatic replay**: Script triggers Step Function to process retry queue

**Benefits:**
- Zero data loss (messages queued during expiration)
- Proactive alerts (refresh before expiration)
- Automatic recovery (no manual message replay)

**Production mode**: Move to production OAuth to eliminate 7-day expiration

## AWS Integrations

### AWS Service Catalog AppRegistry

**Purpose**: Register application for myApplications visibility

**Application:** `ses-mail-{env}`

**Configuration:**
- Application tagged with: `Application=ses-mail-{env}`
- All resources auto-tagged via Terraform default_tags
- AppRegistry discovers resources by tag

**Benefits:**
- Unified application view in AWS Console
- Cost tracking at application level
- Resource grouping and management
- Application health monitoring

**Access:**
```bash
cd terraform/environments/test
terraform output myapplications_url
# Or: AWS Console → Systems Manager → AppManager → Applications
```

### AWS Resource Groups

**Resource Group:** `ses-mail-{env}`

**Tags:**
- `Project`: ses-mail
- `ManagedBy`: terraform
- `Environment`: test or prod
- `Application`: ses-mail-{env}

**Benefits:**
- View all resources in one place
- Cost allocation by environment
- Bulk operations on tagged resources
- Resource inventory

### AWS Group Lifecycle Events (GLE)

**Purpose**: Sync tags across resources for AppRegistry

**Configuration:**
- Enable GLE at account level
- Optional for AppRegistry (works without it)
- Tag sync Lambda triggered on resource changes

**Note**: Auto-enablement via Terraform doesn't work yet - enable manually.

## Security Architecture

### Encryption

**At Rest:**
- S3: AES256 server-side encryption
- DynamoDB: AWS-managed encryption
- SMTP credentials: KMS customer-managed key
- SSM parameters: KMS encryption (SecureString)

**In Transit:**
- TLS 1.2+ for all AWS service communication
- Gmail API: HTTPS only
- SMTP: STARTTLS (port 587) or TLS (port 465)

### IAM Least Privilege

**Lambda Execution Roles:**

1. **Router Lambda**:
   - `dynamodb:GetItem` on routing table only
   - `logs:*` for CloudWatch Logs
   - `xray:*` for tracing

2. **Gmail Forwarder Lambda**:
   - `s3:GetObject`, `s3:DeleteObject` on email bucket only
   - `ssm:GetParameter` on OAuth parameters only
   - `kms:Decrypt` for SecureString parameters
   - `sqs:SendMessage` to retry queue only
   - `sqs:DeleteMessage`, `sqs:ReceiveMessage` from handler queue

3. **Credential Manager Lambda**:
   - `iam:CreateUser`, `iam:CreateAccessKey`, `iam:PutUserPolicy` (scoped to ses-smtp-user-* pattern)
   - `iam:DeleteUser`, `iam:DeleteAccessKey`, `iam:DeleteUserPolicy`
   - `dynamodb:UpdateItem` on routing table
   - `kms:Encrypt`, `kms:Decrypt` on SMTP credential key

**Service Roles:**
- EventBridge Pipes: Poll SQS, invoke Lambda, publish to Event Bus
- SES: Write to S3, publish to SNS
- Step Functions: Invoke Lambda, publish CloudWatch metrics

### SMTP Email Restrictions

**IAM Policy Pattern:**

Each SMTP IAM user has inline policy restricting `ses:SendEmail` and `ses:SendRawEmail` to specific From addresses:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["ses:SendEmail", "ses:SendRawEmail"],
    "Resource": "*",
    "Condition": {
      "StringLike": {
        "ses:FromAddress": [
          "app@example.com",
          "notifications@example.com"
        ]
      }
    }
  }]
}
```

**Benefits:**
- Prevents credential misuse for spoofing
- Limits blast radius if credentials compromised
- Enforces application-specific From addresses

### KMS Key Management

**SMTP Credential Encryption Key:**
- Alias: `alias/ses-mail-smtp-credentials-{env}`
- Type: Customer-managed symmetric key
- Rotation: Enabled (automatic annual rotation)
- Key policy: Only credential manager Lambda can encrypt/decrypt

**Benefits:**
- Credentials encrypted at rest in DynamoDB
- Audit trail via CloudTrail
- Separation of duties (different key per environment)

## Performance & Scalability

### Concurrency and Scaling

**Lambda:**
- Concurrent executions: 1000 (account default, can request increase)
- Each function scales independently
- Reserved concurrency: Not configured (use unreserved pool)

**SQS:**
- No throughput limits
- Long polling reduces empty receives
- Batching: EventBridge Pipes processes messages individually

**DynamoDB:**
- PAY_PER_REQUEST: Auto-scales to workload
- No provisioned capacity to manage
- Sub-10ms read latency (GetItem)

**EventBridge:**
- Soft limit: 2400 requests/second per Event Bus
- Can request increase if needed

### Latency Optimization

**Techniques:**

1. **Long Polling (SQS)**: Reduces receive latency from ~1s to <100ms
2. **EventBridge Pipes**: Managed polling eliminates cold starts
3. **Lambda Memory**: 512 MB for Gmail forwarder (faster CPU)
4. **DynamoDB Single-Item Queries**: GetItem vs. Scan
5. **S3 Transfer Acceleration**: Not enabled (not needed for single-region)

**Latency Breakdown:**

| Component | Typical Latency |
|-----------|----------------|
| SES → S3 | <500ms |
| SNS → SQS | <200ms |
| SQS → EventBridge Pipes | <1s (long polling) |
| Router Lambda | 50-200ms |
| EventBridge Event Bus | <100ms |
| SQS → Gmail Forwarder Lambda | <1s |
| Gmail API Import | 1-2s |
| **Total** | **5-10s** |

### Cost Optimization

**Strategies:**

1. **PAY_PER_REQUEST DynamoDB**: No idle capacity costs
2. **S3 Lifecycle**: Delete emails after 7 days
3. **Lambda Memory**: Right-sized (128MB router, 512MB forwarder)
4. **CloudWatch Log Retention**: 30 days (not indefinite)
5. **X-Ray Sampling**: 1% (not 100%)
6. **SQS Long Polling**: Reduces API calls

**Cost Breakdown (Estimate for 10,000 emails/month):**

| Service | Monthly Cost |
|---------|-------------|
| SES Receiving | $0 (1,000 free, then $0.10/1000) |
| S3 Storage | <$1 (temporary, 7-day lifecycle) |
| Lambda | <$2 (mostly within free tier) |
| DynamoDB | <$1 (PAY_PER_REQUEST, low traffic) |
| EventBridge | <$1 |
| SQS | <$1 (long polling reduces requests) |
| CloudWatch | $2-5 (logs and metrics) |
| **Total** | **~$10/month** |

**Cost Scaling (estimated):**

| Email Volume | Estimated Monthly Cost |
|--------------|----------------------|
| 10K emails/month | ~$10/month |
| 100K emails/month | ~$50-75/month |
| 500K emails/month | ~$200-250/month |
| 1M emails/month | ~$400-500/month |

**Note**: Costs scale approximately linearly with email volume. The main cost drivers at scale are:
- Lambda invocations and execution time
- S3 storage (mitigated by 7-day lifecycle)
- CloudWatch Logs (consider adjusting retention at high volumes)
- EventBridge events

**Cost optimization at scale**: Enable CloudWatch Logs retention policies, increase Lambda memory (faster = cheaper), use S3 Intelligent-Tiering if emails are retained longer.

### Multi-Environment Strategy

**Deployment Modes:**

1. **Separate AWS Accounts** (Recommended):
   - Complete isolation
   - Independent SES rulesets
   - Separate billing
   - No deployment order constraints

2. **Shared AWS Account**:
   - Single SES ruleset (AWS limitation)
   - Test joins prod's ruleset via `join_existing_deployment`
   - Must deploy prod first, then test
   - Shared billing (use tags for cost allocation)

**Environment Isolation:**

Even in shared account mode:
- Separate S3 buckets
- Separate DynamoDB tables
- Separate Lambda functions
- Separate SQS queues
- Environment-specific naming: `{resource}-{env}`

**State Management:**
- Terraform state: S3 bucket `terraform-state-{account-id}`
- State key: `ses-mail/{env}.tfstate`
- Backend locking: S3 native locking (no DynamoDB)

## Further Reading

- [Setup Guide](SETUP.md) - First-time deployment
- [Operations Guide](OPERATIONS.md) - Day-to-day management
- [Development Guide](DEVELOPMENT.md) - Testing and contributing
- [Terraform Module README](../terraform/modules/ses-mail/README.md) - Infrastructure details
