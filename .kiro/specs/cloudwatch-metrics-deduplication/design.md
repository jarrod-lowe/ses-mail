# Design Document: CloudWatch Metrics Deduplication

## Overview

This design addresses two critical issues in the SES email routing system's CloudWatch metrics:

1. **Metrics Duplication**: When Lambda functions are retried, metrics are published multiple times for the same processing event, leading to inaccurate counts
2. **Dashboard Metric Mismatch**: The CloudWatch dashboard references non-existent metrics (`EmailsAccepted`, `EmailsSpam`, `EmailsVirus`) instead of the actual metrics being published

The solution implements idempotent metric publishing using CloudWatch's built-in deduplication capabilities and updates the dashboard to reference correct metrics.

## Architecture

### Current State Problems

**Metrics Duplication Flow:**
```
SES Message → SNS → Lambda (Attempt 1) → Metrics Published
                 → Lambda (Retry)     → Metrics Published Again (DUPLICATE)
```

**Dashboard Mismatch:**
- Dashboard expects: `EmailsAccepted`, `EmailsSpam`, `EmailsVirus`
- Actually published: `RouterEnrichmentSuccess`, `RouterEnrichmentFailure`, `GmailForwardSuccess`, etc.

### Proposed Solution Architecture

**Recommended Approach: Simple Idempotency Flag**
```
SES Message → SNS → Lambda → Check if already processed → Publish metrics once
                          → Lambda Retry → Already processed flag set → Skip metrics
```

**Key Insight**: Use a simple boolean flag in the Lambda handler to track whether metrics have been published for the current invocation, regardless of execution environment reuse.

**Deduplication Mechanism**:
1. **Handler-Level Flag**: Set a flag when metrics are successfully published
2. **Early Return**: Check flag at start of metric publishing logic
3. **Exception Safety**: Only set flag after successful CloudWatch API call
4. **No External Dependencies**: Pure in-memory solution within handler execution

**Corrected Dashboard:**
- Replace non-existent metrics with actual published metrics
- Add new metrics for email processing overview based on routing decisions

## Components and Interfaces

### 1. Metric Deduplication Component

**Location**: `router_enrichment.py`, `gmail_forwarder.py`, `bouncer.py`

**Interface**:
```python
def publish_metrics_idempotent(
    success_count: int, 
    failure_count: int, 
    context: LambdaContext,
    message_ids: List[str]
) -> bool:
    """
    Publish metrics with deduplication to prevent double-counting during retries.
    
    Args:
        success_count: Number of successful operations
        failure_count: Number of failed operations  
        context: Lambda execution context
        message_ids: List of message IDs being processed
        
    Returns:
        bool: True if metrics were published, False if skipped due to deduplication
    """
```

**Complete Deduplication Flow**:
1. **Check Published Flag**: At start of handler, check if metrics already published
2. **Early Return**: If flag is set, skip metric publishing entirely
3. **Publish Metrics**: If not published, send metrics to CloudWatch
4. **Set Flag**: Only set flag after successful CloudWatch API response
5. **Retry Handling**: Retries see the flag and skip publishing

**Code Example**:
```python
def lambda_handler(event, context):
    """Main Lambda handler with metric deduplication."""
    
    # Flag to track if metrics have been published in this handler execution
    metrics_published = False
    
    success_count = 0
    failure_count = 0
    
    # Process messages...
    for record in event['Records']:
        try:
            # Process the record
            process_record(record)
            success_count += 1
        except Exception as e:
            logger.exception("Failed to process record")
            failure_count += 1
    
    # Publish metrics only once per handler execution
    if not metrics_published:
        try:
            publish_metrics(success_count, failure_count)
            metrics_published = True
            logger.info("Metrics published successfully")
        except Exception as e:
            logger.error("Failed to publish metrics", extra={"error": str(e)})
            # Don't set flag if publishing failed - allow retry
    else:
        logger.info("Metrics already published, skipping")
    
    return {"statusCode": 200, "body": "Success"}
```
    """
```

**Complete Deduplication Flow**:
1. **Generate Unique ID**: Combine Lambda request ID with hash of message IDs
2. **Check DynamoDB**: Query deduplication table to see if ID exists
3. **Conditional Publish**: If ID not found, publish metrics and record in DynamoDB
4. **Handle Retries**: If ID found, skip publishing (already done in previous execution)
5. **TTL Cleanup**: DynamoDB automatically removes old records after 24 hours

**Implementation Strategy**:
- Use a simple boolean flag within the Lambda handler execution
- Check flag before attempting to publish metrics
- Set flag only after successful metric publication
- Handle exceptions to ensure flag is only set on success

### 2. Dashboard Metric Mapping Component

**Location**: `cloudwatch.tf`

**Current Problematic Metrics**:
- `EmailsAccepted` → Does not exist
- `EmailsSpam` → Does not exist  
- `EmailsVirus` → Does not exist

**Replacement Strategy**:
- Map to actual routing decision metrics
- Create derived metrics from existing published data
- Use log-based metrics for email processing overview

### 3. New Email Processing Metrics

**Router-Based Email Metrics**:
```python
# New metrics to be published by router_enrichment.py
'EmailsProcessed'     # Total emails processed
'EmailsForwarded'     # Emails routed to Gmail
'EmailsBounced'       # Emails routed to bounce
'EmailsStored'        # Emails routed to storage
```

## Data Models

### Handler State Tracking

**Simple Flag-Based Approach**:
```python
def lambda_handler(event, context):
    """Lambda handler with built-in metric deduplication."""
    
    # Local variable to track metric publication within this handler execution
    metrics_published = False
    
    # ... processing logic ...
    
    # Publish metrics with deduplication check
    if not metrics_published:
        success = publish_metrics_safely(success_count, failure_count)
        if success:
            metrics_published = True
    
    return response
```

### Metric Data Structure

```python
@dataclass
class MetricBatch:
    """Represents a batch of metrics to be published with deduplication."""
    namespace: str
    metrics: List[Dict[str, Any]]
    deduplication_id: str
    timestamp: datetime
    
    def to_cloudwatch_format(self) -> Dict[str, Any]:
        """Convert to CloudWatch PutMetricData format."""
        return {
            'Namespace': self.namespace,
            'MetricData': [
                {
                    **metric,
                    'Timestamp': self.timestamp
                }
                for metric in self.metrics
            ]
        }
```

### Deduplication Strategy

**Handler-Level Deduplication**:
The approach relies on the fact that Lambda retries execute the same handler function, so a local variable can track whether metrics have been published within that specific handler execution.

**Safe Metric Publishing**:
```python
def publish_metrics_safely(success_count: int, failure_count: int) -> bool:
    """
    Safely publish metrics with error handling.
    
    Returns:
        bool: True if metrics were published successfully, False otherwise
    """
    try:
        metric_data = []
        
        if success_count > 0:
            metric_data.append({
                'MetricName': 'RouterEnrichmentSuccess',
                'Value': success_count,
                'Unit': 'Count'
            })
        
        if failure_count > 0:
            metric_data.append({
                'MetricName': 'RouterEnrichmentFailure',
                'Value': failure_count,
                'Unit': 'Count'
            })
        
        if metric_data:
            cloudwatch.put_metric_data(
                Namespace=f'SESMail/{ENVIRONMENT}',
                MetricData=metric_data
            )
            logger.info("Successfully published metrics", extra={
                "success_count": success_count,
                "failure_count": failure_count
            })
            return True
        
        return True  # No metrics to publish is considered success
        
    except Exception as e:
        logger.error("Failed to publish metrics", extra={
            "error": str(e),
            "success_count": success_count,
            "failure_count": failure_count
        })
        return False
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Converting EARS to Properties

Based on the prework analysis, I'll convert the testable acceptance criteria into correctness properties, eliminating redundancy:

**Property 1: Retry Deduplication**
*For any* message batch and Lambda execution context, when the same message batch is processed multiple times (due to retries), metrics should only be recorded once in CloudWatch
**Validates: Requirements 1.1, 1.2, 4.2**

**Property 2: Idempotent Metric Publishing**
*For any* set of metrics and deduplication ID, calling the publish_metrics function multiple times with the same parameters should result in only one set of metrics being recorded
**Validates: Requirements 1.3**

**Property 3: Dashboard Metric Consistency**
*For any* metric referenced in the CloudWatch dashboard configuration, that metric name should correspond to a metric that is actually published by the system
**Validates: Requirements 2.2, 2.3**

**Property 4: Namespace Format Preservation**
*For any* published metric, the namespace should follow the format "SESMail/{environment}" where environment is a valid environment identifier
**Validates: Requirements 3.3**

**Property 5: Functional Metric Publishing**
*For any* successful or failed message processing operation, the appropriate success or failure metrics should be published to CloudWatch
**Validates: Requirements 3.1, 3.2**

**Property 6: Unique Identifier Inclusion**
*For any* metric batch published to CloudWatch, the metric data should include a unique deduplication identifier and use appropriate CloudWatch API parameters
**Validates: Requirements 4.1, 4.3**

**Property 7: Execution State Tracking**
*For any* completed Lambda execution, the system should maintain state indicating whether metrics for that execution have been published
**Validates: Requirements 4.4**

## Error Handling

### Metric Publishing Failures

**Strategy**: Graceful degradation - metric publishing failures should not cause Lambda execution failures.

**Implementation**:
```python
def publish_metrics_with_fallback(metrics: MetricBatch) -> bool:
    """
    Publish metrics with error handling and fallback logging.
    
    Returns:
        bool: True if metrics were published successfully, False otherwise
    """
    try:
        cloudwatch.put_metric_data(**metrics.to_cloudwatch_format())
        return True
    except Exception as e:
        logger.error("Failed to publish metrics", extra={
            "error": str(e),
            "deduplication_id": metrics.deduplication_id,
            "metric_count": len(metrics.metrics)
        })
        return False
```

### Deduplication ID Collisions

**Strategy**: Use cryptographically strong hashing to minimize collision probability.

**Implementation**:
- Combine Lambda request ID (guaranteed unique per execution) with message content hash
- Use SHA-256 for message hashing to ensure low collision probability
- Include timestamp as additional entropy if needed

### CloudWatch API Throttling

**Strategy**: Implement exponential backoff with jitter.

**Implementation**:
```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type(ClientError)
)
def publish_metrics_with_retry(metrics: MetricBatch) -> None:
    """Publish metrics with automatic retry on throttling."""
    cloudwatch.put_metric_data(**metrics.to_cloudwatch_format())
```

## Testing Strategy

### Dual Testing Approach

This feature requires both unit tests and property-based tests to ensure comprehensive coverage:

**Unit Tests**:
- Test specific deduplication scenarios with known inputs
- Test dashboard configuration parsing and validation
- Test error handling edge cases (API failures, malformed data)
- Test metric format conversion and validation

**Property-Based Tests**:
- Test deduplication properties across random message batches and retry scenarios
- Test namespace format consistency across random environment values
- Test metric publishing behavior across random success/failure combinations
- Minimum 100 iterations per property test to ensure comprehensive coverage

**Property Test Configuration**:
- Use Python's `hypothesis` library for property-based testing
- Each property test must reference its design document property
- Tag format: **Feature: cloudwatch-metrics-deduplication, Property {number}: {property_text}**

**Integration Tests**:
- Test end-to-end metric publishing with actual CloudWatch (using test environment)
- Test dashboard functionality with published metrics
- Test Lambda retry scenarios using AWS Lambda test events

### Test Data Generation

**Message Batch Generation**:
```python
@given(
    message_ids=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=10),
    success_count=st.integers(min_value=0, max_value=100),
    failure_count=st.integers(min_value=0, max_value=100)
)
def test_deduplication_property(message_ids, success_count, failure_count):
    """Test that metrics are deduplicated across retries."""
    # Property test implementation
```

**Environment Generation**:
```python
@given(environment=st.sampled_from(['dev', 'staging', 'prod']))
def test_namespace_format_property(environment):
    """Test that namespace format is preserved."""
    # Property test implementation
```