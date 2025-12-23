# Design Document

## Overview

This design implements comprehensive end-to-end XRay distributed tracing for the SES email processing pipeline. The current system has partial XRay support with gaps at SES (no native support) and incomplete trace propagation across service boundaries. This solution bridges these gaps using custom instrumentation, trace context injection, and enhanced Lambda functions to provide complete visibility from email receipt to final processing.

## Architecture

### Current Architecture (With XRay Gaps)

```plain
SES Receipt Rule
├── S3 Action: Store email in S3
└── SNS Action: Publish to SNS topic (✅ XRay Active Tracing)
    └── SNS Topic → SQS Input Queue (✅ XRay Headers)
        └── EventBridge Pipes (❌ Limited XRay Support)
            ├── Source: SQS Input Queue
            ├── Enrichment: Router Lambda (✅ XRay Active)
            └── Target: EventBridge Event Bus (✅ XRay Passive)
                ├── Gmail Rule → Gmail Queue → Gmail Lambda (✅ XRay Active)
                └── Bounce Rule → Bounce Queue → Bounce Lambda (✅ XRay Active)
```

**XRay Problems**:

1. **SES**: No native XRay support - traces don't start until SNS
2. **EventBridge Pipes**: Limited XRay support - trace context may be lost during enrichment
3. **Complex Flow**: Many components = more points of failure for trace propagation

### Proposed Architecture (Simplified with Complete XRay)

```plain
SES Receipt Rule
├── S3 Action: Store email in S3
└── SNS Action: Publish to SNS topic (✅ XRay Active Tracing)
    └── SNS Topic → Router Lambda (✅ XRay Active - TRACE INITIATOR)
        └── EventBridge Event Bus (✅ XRay Passive - trace context injected)
            ├── Gmail Rule → Gmail Queue → Enhanced Gmail Lambda (✅ XRay Active)
            └── Bounce Rule → Bounce Queue → Enhanced Bounce Lambda (✅ XRay Active)
```

**Key Changes**:

1. **Remove EventBridge Pipes**: Eliminate XRay-problematic component
2. **Remove SQS Input Queue**: No longer needed without Pipes
3. **Enhanced Router Lambda**:
   - Triggered directly by SNS (not via Pipes)
   - Becomes the XRay trace initiator
   - Fetches email from S3
   - Makes routing decisions
   - Publishes enriched events to EventBridge with trace context
4. **Simplified Flow**: Fewer components = better XRay support

**Benefits**:

- Complete end-to-end XRay tracing from SNS through final processing
- Fewer components to maintain and monitor
- Better error handling (SNS retry/DLQ vs Pipes complexity)
- Lower cost (eliminate Pipes and SQS input queue)
- Faster processing (fewer hops)

## Components and Interfaces

### 1. Enhanced Router Lambda (Core Change)

**Current Role**: Enrichment function called by EventBridge Pipes
**New Role**: Primary email processor and XRay trace initiator

**New Responsibilities**:

1. **SNS Event Processing**: Handle SNS messages containing SES events
2. **XRay Trace Initiation**: Create initial trace segment for email processing
3. **Routing Logic**: Query DynamoDB for routing rules using SES event metadata (existing functionality)
4. **EventBridge Publishing**: Send enriched events to EventBridge Event Bus
5. **Trace Context Injection**: Include XRay trace context in EventBridge events

**Note**: Router Lambda does NOT need to fetch email content from S3. It only processes SES event metadata (sender, recipients, message ID) to make routing decisions. Handler Lambdas fetch actual email content when needed.

**Interface Changes**:

```python
# Current: Handles EventBridge Pipes enrichment format
def router_handler(pipes_event, context):
    # Receives: EventBridge Pipes SQS batch format
    pass

# New: Handles SNS events directly
@xray_recorder.capture('email_processing_pipeline')
def enhanced_router_handler(sns_event, context):
    """
    Process SNS message containing SES event
    - Extract SES event from SNS message
    - Initiate XRay trace with email metadata
    - Make routing decisions using SES event metadata
    - Publish to EventBridge with trace context
    """
    pass
```

### 2. Infrastructure Changes

**Components to Remove**:

- **EventBridge Pipes**: `aws_pipes_pipe.email_router`
- **SQS Input Queue**: `aws_sqs_queue.input_queue`
- **Pipes IAM Role**: `aws_iam_role.eventbridge_pipes_execution`
- **Pipes CloudWatch Logs**: `/aws/vendedlogs/pipes/...`

**Components to Add**:

- **SNS Subscription**: Router Lambda subscribes to existing SNS topic
- **Lambda DLQ**: Dead letter queue for failed Router Lambda invocations

**Components to Modify**:

- **Router Lambda**: Add SNS trigger, XRay enhancements (no S3 access needed)
- **SNS Topic**: Add Lambda subscription (keep existing SQS subscriptions if any)
- **EventBridge Events**: Include XRay trace context in event detail

### 3. Handler Lambda Enhancements

**Purpose**: Complete trace propagation and add detailed email processing annotations.

**Implementation**:

- **Trace Context Extraction**: Extract trace headers from SQS message attributes
- **Custom Subsegments**: Create detailed subsegments for Gmail API calls, SES bounces
- **Error Instrumentation**: Capture detailed error information in trace segments
- **Performance Metrics**: Add timing annotations for external API calls

**Enhanced Handler Structure**:

```python
@xray_recorder.capture('gmail_forwarding')
def enhanced_gmail_handler(event, context):
    """
    Enhanced Gmail forwarder with detailed XRay instrumentation
    Creates subsegments for S3 fetch, Gmail API calls, cleanup operations
    """
    pass
```

### 4. Cross-Service Trace Correlation

**Purpose**: Maintain trace correlation across service boundaries that don't natively support XRay.

**Implementation**:

- **Correlation ID Strategy**: Use SES message ID as correlation identifier
- **Custom Trace Linking**: Link related traces using correlation IDs
- **Metadata Propagation**: Carry email metadata through all processing stages

## Data Models

### XRay Trace Segment Structure

```json
{
  "trace_id": "1-67890abc-12345678901234567890abcd",
  "id": "1234567890123456",
  "name": "ses-email-processing",
  "start_time": 1640995200.0,
  "end_time": 1640995201.5,
  "annotations": {
    "email_message_id": "ses-message-id-123",
    "sender_domain": "example.com",
    "recipient_count": 2,
    "routing_action": "forward-to-gmail",
    "environment": "prod"
  },
  "metadata": {
    "email": {
      "subject": "Test Email",
      "sender": "sender@example.com",
      "recipients": ["user1@domain.com", "user2@domain.com"],
      "spam_verdict": "PASS",
      "virus_verdict": "PASS"
    }
  },
  "subsegments": [
    {
      "name": "s3_email_fetch",
      "start_time": 1640995200.1,
      "end_time": 1640995200.3
    },
    {
      "name": "gmail_api_import", 
      "start_time": 1640995200.5,
      "end_time": 1640995201.2
    }
  ]
}
```

### Trace Context Propagation Model

```json
{
  "trace_header": "Root=1-trace-id;Parent=parent-id;Sampled=1",
  "correlation_id": "ses-message-id-123",
  "email_metadata": {
    "message_id": "ses-message-id-123",
    "timestamp": "2025-01-18T10:00:00Z",
    "sender": "sender@example.com",
    "recipients": ["user@domain.com"]
  },
  "processing_context": {
    "environment": "prod",
    "pipeline_stage": "routing",
    "routing_decision": "forward-to-gmail"
  }
}
```

## Error Handling

### Architecture Option Failure Handling

**Option A Failure Scenarios**:

- **Lambda Failure**: Email remains in S3 but unprocessed (no retry mechanism)
- **Mitigation**: Implement CloudWatch alarm on Lambda errors + manual reprocessing script
- **Recovery**: Periodic scan of S3 for unprocessed emails (based on age/metadata)

**Option B Failure Scenarios** (Recommended):

- **Lambda Failure**: SNS retries Lambda invocation automatically
- **Persistent Failure**: SNS sends message to DLQ after retry exhaustion
- **DLQ Processing**: Separate Lambda processes DLQ messages for recovery
- **Monitoring**: CloudWatch alarms on SNS delivery failures and DLQ message count

### Trace Failure Scenarios

1. **Trace Initiation Failure**:
   - Fallback: Continue processing without tracing
   - Logging: Log trace initiation errors to CloudWatch
   - Monitoring: CloudWatch alarm for trace initiation failures

2. **Trace Context Loss**:
   - Recovery: Create new trace segment with correlation ID
   - Linking: Use correlation ID to link orphaned segments
   - Alerting: Monitor for trace context loss patterns

3. **XRay Service Unavailability**:
   - Graceful Degradation: Continue email processing without tracing
   - Circuit Breaker: Temporarily disable tracing if XRay is down
   - Recovery: Automatic re-enablement when XRay is available

### Error Instrumentation

```python
@xray_recorder.capture('error_handling')
def handle_processing_error(error, context):
    """
    Captures detailed error information in XRay trace
    Includes error type, message, stack trace, and context
    """
    segment = xray_recorder.current_segment()
    segment.add_exception(error)
    segment.add_annotation('error_type', type(error).__name__)
    segment.add_metadata('error_context', context)
```

## Testing Strategy

### Unit Testing

1. **Trace Initiation Tests**:
   - Test trace creation with valid SES events
   - Test error handling for malformed events
   - Test trace context generation and formatting

2. **Context Propagation Tests**:
   - Test trace header extraction and injection
   - Test correlation ID generation and usage
   - Test metadata preservation across service boundaries

3. **Handler Enhancement Tests**:
   - Test subsegment creation for external API calls
   - Test annotation and metadata addition
   - Test error capture and trace marking

### Integration Testing

1. **End-to-End Trace Flow**:
   - Send test email through complete pipeline
   - Verify trace appears in XRay console with all segments
   - Validate trace timing and annotation accuracy

2. **Service Boundary Testing**:
   - Test trace propagation through SNS → SQS
   - Test EventBridge Pipes trace preservation
   - Test EventBridge → SQS trace continuation

3. **Error Scenario Testing**:
   - Test trace behavior during Lambda failures
   - Test trace handling during external API failures
   - Test graceful degradation when XRay is unavailable

### Performance Testing

1. **Tracing Overhead**:
   - Measure processing latency with and without tracing
   - Test impact on Lambda cold start times
   - Validate memory usage impact

2. **Sampling Effectiveness**:
   - Test different sampling rates (1%, 5%, 10%)
   - Measure XRay cost impact at different sampling levels
   - Validate trace completeness at various sampling rates

## Implementation Phases

### Phase 1: Architecture Simplification

- Remove EventBridge Pipes and SQS input queue
- Implement Option B (Keep SNS) for reliability
- Add SNS subscription for Router Lambda with DLQ configuration
- Add XRay permissions to Router Lambda IAM role

### Phase 2: Router Lambda Enhancement

- Implement trace initiation in Router Lambda
- Add S3 email fetching capability to Router Lambda
- Enhance routing logic with XRay instrumentation
- Add trace context injection to EventBridge events

### Phase 3: Handler Instrumentation

- Enhance Gmail forwarder Lambda with detailed tracing
- Enhance bouncer Lambda with XRay instrumentation
- Add custom subsegments for external API calls
- Implement trace context extraction from EventBridge events

### Phase 4: Monitoring and Optimization

- Implement XRay-based CloudWatch dashboards
- Configure sampling rules for cost optimization
- Add XRay metrics to existing alarm infrastructure
- Remove obsolete EventBridge Pipes monitoring
