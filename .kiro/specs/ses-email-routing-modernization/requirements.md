# Requirements Document

## Introduction

This feature modernizes the existing SES email processing system by replacing the synchronous validator lambda with an event-driven architecture using AWS serverless services. The new system will use SNS, SQS, and EventBridge to create a scalable, cost-effective email routing system that can handle multiple processing actions (bouncing, forwarding to Gmail, etc.) with proper error handling and monitoring.

## Requirements

### Requirement 1

**User Story:** As a system administrator, I want to remove the synchronous validator lambda from the SES receipt rule, so that email processing becomes fully asynchronous and more scalable.

#### Acceptance Criteria

1. WHEN an email is received by SES THEN the system SHALL store the email in S3 and publish an SNS notification without calling any synchronous lambda functions
2. WHEN the SES receipt rule is configured THEN it SHALL only contain S3 storage action and SNS publish action
3. WHEN the validator lambda is removed THEN all associated IAM permissions and resources SHALL be cleaned up

### Requirement 2

**User Story:** As a system administrator, I want to use EventBridge Pipes with enrichment and EventBridge Event Bus for routing, so that message enrichment and multi-target routing are handled by AWS services without requiring custom dispatch logic.

#### Acceptance Criteria

1. WHEN an SNS message is published from S3 email storage THEN it SHALL be sent to an SQS queue for EventBridge Pipes processing
2. WHEN EventBridge Pipes processes the message THEN it SHALL use a router lambda for enrichment to add routing decisions
3. WHEN the router enrichment function processes an email THEN it SHALL look up routing rules in DynamoDB using recipient address matching (target@domain.com, *@domain.com, * with most specific match winning)
4. WHEN the router enrichment function processes an email THEN it SHALL analyze DMARC result headers and other email security indicators
5. WHEN the router enrichment completes THEN EventBridge Pipes SHALL send the enriched message to an EventBridge Event Bus
6. WHEN the EventBridge Event Bus receives enriched messages THEN it SHALL route them to appropriate target SQS queues based on EventBridge rules matching the routing decisions
7. WHEN EventBridge Pipes or Event Bus routing fails THEN messages SHALL be sent to dead letter queues with CloudWatch alarms

### Requirement 3

**User Story:** As a system administrator, I want EventBridge Event Bus rules to route enriched messages to separate SQS queues for different email processing actions, so that each type of processing can be handled independently with proper error handling and retry logic.

#### Acceptance Criteria

1. WHEN EventBridge Event Bus receives an enriched message with "forward_to_gmail" action THEN it SHALL route the message to the gmail-forwarder SQS queue
2. WHEN EventBridge Event Bus receives an enriched message with "bounce" action THEN it SHALL route the message to the bouncer SQS queue
3. WHEN EventBridge Event Bus processes messages THEN it SHALL support routing to multiple target queues for emails requiring multiple actions
4. WHEN EventBridge rules are configured THEN they SHALL match on the routing decisions added by the enrichment function
5. WHEN any SQS queue receives a message THEN it SHALL have a corresponding dead letter queue configured
6. WHEN messages fail processing multiple times THEN they SHALL be moved to the dead letter queue
7. WHEN dead letter queues receive messages THEN CloudWatch alarms SHALL be triggered

### Requirement 4

**User Story:** As a system administrator, I want a Gmail forwarder lambda that processes messages from the gmail-forwarder queue, so that emails can be forwarded to Gmail accounts using the existing email processor logic.

#### Acceptance Criteria

1. WHEN a message arrives in the gmail-forwarder SQS queue THEN the Gmail forwarder lambda SHALL be triggered
2. WHEN the Gmail forwarder lambda processes a message THEN it SHALL use the existing email_processor.py logic to forward emails to Gmail
3. WHEN the Gmail forwarder successfully processes an email THEN it SHALL delete the message from the SQS queue
4. WHEN the Gmail forwarder fails to process an email THEN the message SHALL be retried according to the queue's retry policy
5. IF the Gmail forwarder fails after all retries THEN the message SHALL be sent to the dead letter queue

### Requirement 5

**User Story:** As a system administrator, I want a bouncer lambda that processes messages from the bouncer queue, so that emails can be bounced back to senders when required.

#### Acceptance Criteria

1. WHEN a message arrives in the bouncer SQS queue THEN the bouncer lambda SHALL be triggered
2. WHEN the bouncer lambda processes a message THEN it SHALL send a bounce message using SES
3. WHEN the bouncer successfully sends a bounce THEN it SHALL delete the message from the SQS queue
4. WHEN the bouncer fails to send a bounce THEN the message SHALL be retried according to the queue's retry policy
5. IF the bouncer fails after all retries THEN the message SHALL be sent to the dead letter queue

### Requirement 6

**User Story:** As a system administrator, I want comprehensive monitoring and alerting for the email processing system, so that no failures go unreported and system health can be monitored.

#### Acceptance Criteria

1. WHEN any lambda function fails THEN CloudWatch alarms SHALL be triggered
2. WHEN any dead letter queue receives messages THEN CloudWatch alarms SHALL be triggered
3. WHEN SQS queues have messages that are too old THEN CloudWatch alarms SHALL be triggered
4. WHEN the system processes emails THEN metrics SHALL be recorded for successful and failed processing
5. WHEN alarms are triggered THEN they SHALL be configured to send notifications (SNS topic for alerts)

### Requirement 7

**User Story:** As a system administrator, I want the new architecture to use only serverless AWS services with $0 standing costs, so that the system remains cost-effective when not processing emails.

#### Acceptance Criteria

1. WHEN the system is deployed THEN it SHALL only use services with pay-per-use pricing (Lambda, SNS, SQS, EventBridge, S3, CloudWatch)
2. WHEN the system is idle THEN it SHALL incur no hourly charges
3. WHEN the system is deployed THEN it SHALL NOT use any services with hourly costs (EC2, ECS, OpenSearch, RDS, etc.)

### Requirement 8

**User Story:** As a system administrator, I want the router to use DynamoDB for storing and looking up email routing rules with support for email address normalization, so that routing decisions can be configured dynamically without code changes and work correctly with plus addressing.

#### Acceptance Criteria

1. WHEN the router processes an email THEN it SHALL query DynamoDB for routing rules using a hierarchical lookup strategy
2. WHEN looking up routing rules for an email address THEN the system SHALL check in order: full address (foo+thing@example.com), normalized address (foo@example.com), domain wildcard (*@example.com), and global wildcard (*) with the first match taking precedence
3. WHEN DynamoDB contains routing rules THEN they SHALL have separate fields for the routing action (e.g., "forward-to-gmail", "bounce") and the target parameter (e.g., "foo@example.com")
4. WHEN the router enriches messages THEN it SHALL include both the routing action and target parameter from DynamoDB in the enriched message
5. WHEN new routing rules are needed THEN they SHALL be addable to DynamoDB without code deployment
6. WHEN the router processes emails THEN it SHALL support multiple actions per email recipient
7. WHEN the router enriches messages THEN it SHALL include both the original recipient address and the normalized address used for routing
8. WHEN DynamoDB is unavailable THEN the router SHALL have a fallback behavior (e.g., default to bounce)

### Requirement 9

**User Story:** As a system administrator, I want the system to use EventBridge Pipes and Event Bus for message enrichment and routing to avoid custom dispatch logic, so that retry and failure behaviors are handled entirely by AWS services.

#### Acceptance Criteria

1. WHEN the system enriches messages THEN it SHALL use EventBridge Pipes with lambda enrichment instead of custom lambda dispatch code
2. WHEN the system routes messages THEN it SHALL use EventBridge Event Bus rules instead of custom routing logic
3. WHEN handler lambdas process messages THEN they SHALL receive all necessary data in the message payload without making external lookups
4. WHEN the router enrichment function runs THEN it SHALL only enrich the message data without making routing calls to other services
5. WHEN EventBridge services handle message flow THEN they SHALL manage message delivery, retries, and dead letter queue routing automatically
6. WHEN handler lambdas fail THEN AWS SQS SHALL handle retry logic automatically without custom retry implementation

### Requirement 10

**User Story:** As a system administrator, I want X-Ray distributed tracing enabled throughout the email processing pipeline, so that I can trace email processing across all AWS services and identify performance bottlenecks or failures.

#### Acceptance Criteria

1. WHEN SNS publishes messages THEN it SHALL have Active Tracing enabled to start X-Ray traces
2. WHEN SQS queues process messages THEN they SHALL propagate X-Ray trace context
3. WHEN EventBridge Pipes and Event Bus process messages THEN they SHALL propagate X-Ray trace context
4. WHEN Lambda functions process messages THEN they SHALL have X-Ray tracing enabled and propagate trace context
5. WHEN the system processes an email end-to-end THEN a complete X-Ray trace SHALL be available showing the entire processing pipeline
6. WHEN X-Ray traces are generated THEN they SHALL include custom annotations for email metadata (message ID, recipient, action taken)

