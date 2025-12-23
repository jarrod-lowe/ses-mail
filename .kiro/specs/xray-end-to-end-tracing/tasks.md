# Implementation Plan

- [ ] 1. Remove EventBridge Pipes infrastructure
  - Remove EventBridge Pipes resource and associated IAM roles
  - Remove SQS input queue that was used by Pipes
  - Clean up Pipes-related CloudWatch logs and alarms
  - _Requirements: 1.1, 3.1_

- [ ] 2. Enhance Router Lambda for direct SNS processing
- [ ] 2.1 Add SNS trigger configuration to Router Lambda
  - Create SNS subscription for Router Lambda with DLQ configuration
  - Update Lambda IAM role with SNS invoke permissions
  - Configure retry policy and dead letter queue for failed invocations
  - _Requirements: 1.1, 3.1, 3.2_

- [ ] 2.2 Review Router Lambda IAM permissions for SNS integration
  - Router Lambda currently has S3 and DynamoDB access (S3 unused but harmless)
  - Add SNS invoke permissions to allow SNS to trigger Router Lambda
  - No changes needed to existing DynamoDB or XRay permissions
  - _Requirements: 1.1, 3.1_

- [ ] 2.3 Implement SNS event processing in Router Lambda
  - Modify Router Lambda handler to process SNS events instead of EventBridge Pipes format
  - Extract SES event data from SNS message body
  - Add error handling for malformed SNS messages
  - _Requirements: 1.1, 3.1_

- [ ] 3. Implement XRay trace initiation in Router Lambda
- [ ] 3.1 Add XRay trace creation with email metadata
  - Initialize XRay trace segment when processing SNS message
  - Add custom annotations for message ID, sender, recipients, routing action
  - Include email metadata in trace segment for filtering and analysis
  - _Requirements: 1.1, 1.4, 2.1, 2.2_

- [ ] 3.2 Focus XRay instrumentation on Router Lambda's actual operations
  - Router Lambda only performs DynamoDB lookups for routing decisions (no S3 access needed)
  - Add XRay subsegments for DynamoDB GetItem and Query operations
  - Add XRay annotations for routing rule matches and lookup performance
  - _Requirements: 1.1, 2.3_

- [ ] 3.3 Add XRay instrumentation to DynamoDB routing lookups
  - Enhance existing DynamoDB queries with XRay subsegments
  - Add annotations for routing rule matches and lookup performance
  - Include routing decision metadata in trace segments
  - _Requirements: 1.4, 2.1, 2.3_

- [ ] 4. Implement trace context propagation to EventBridge
- [ ] 4.1 Add trace context to EventBridge events
  - Extract current XRay trace context from Router Lambda
  - Include trace header in EventBridge event detail or metadata
  - Ensure trace context format is compatible with downstream services
  - _Requirements: 1.1, 3.3, 3.4_

- [ ] 4.2 Update EventBridge event structure for trace propagation
  - Modify EventBridge event detail to include XRay trace information
  - Add correlation ID using SES message ID for trace linking
  - Test EventBridge event publishing with trace context
  - _Requirements: 1.1, 3.3, 3.4_

- [ ] 5. Enhance handler Lambdas with XRay instrumentation
- [ ] 5.1 Add trace context extraction to Gmail forwarder Lambda
  - Extract XRay trace context from EventBridge events via SQS messages
  - Continue existing trace or create linked trace segment
  - Add custom annotations for Gmail API operations and email metadata
  - _Requirements: 1.1, 1.4, 2.1, 2.3_

- [ ] 5.2 Add detailed XRay subsegments to Gmail forwarder
  - Create subsegments for S3 email fetch, Gmail API authentication, email import
  - Add timing annotations for external API calls and performance monitoring
  - Include error details in trace segments for failed operations
  - _Requirements: 1.4, 2.3, 4.2_

- [ ] 5.3 Add trace context extraction to bouncer Lambda
  - Extract XRay trace context from EventBridge events via SQS messages
  - Continue existing trace or create linked trace segment
  - Add custom annotations for SES bounce operations and email metadata
  - _Requirements: 1.1, 1.4, 2.1, 2.3_

- [ ] 5.4 Add detailed XRay subsegments to bouncer Lambda
  - Create subsegments for SES bounce email composition and sending
  - Add timing annotations for SES API calls and performance monitoring
  - Include error details in trace segments for failed bounce operations
  - _Requirements: 1.4, 2.3, 4.2_

- [ ] 6. Configure XRay sampling and cost optimization
- [ ] 6.1 Implement XRay sampling rules for cost control
  - Create sampling rules with configurable rates (default 5% for production)
  - Set higher sampling rates for error conditions and specific email patterns
  - Configure sampling rules to be consistent across all Lambda functions
  - _Requirements: 5.1, 5.2, 5.3_

- [ ] 6.2 Add XRay cost monitoring and reporting
  - Create CloudWatch metrics for XRay trace volume and costs
  - Set up CloudWatch alarms for unexpected XRay usage spikes
  - Add XRay usage to existing cost monitoring dashboards
  - _Requirements: 5.3, 5.4_

- [ ] 7. Implement XRay-based monitoring and dashboards
- [ ] 7.1 Create XRay service map monitoring
  - Configure XRay service map to show complete email processing pipeline
  - Add service map annotations for easy identification of processing stages
  - Test service map visibility with sample email processing
  - _Requirements: 4.1, 4.3_

- [ ] 7.2 Add XRay metrics to CloudWatch dashboards
  - Include XRay trace statistics in existing email processing dashboards
  - Add widgets for trace latency, error rates, and throughput
  - Create XRay-based alarms for processing latency and error thresholds
  - _Requirements: 4.1, 4.2, 4.3_

- [ ] 7.3 Implement XRay trace search and analysis
  - Configure XRay trace search with email-specific filter expressions
  - Create saved searches for common troubleshooting scenarios
  - Test trace search functionality with various email processing patterns
  - _Requirements: 2.2, 4.2, 4.3_

- [ ] 8. Testing and validation
- [ ] 8.1 Implement end-to-end trace validation tests
  - Create integration tests that send test emails and verify complete traces
  - Validate trace continuity from SNS through final handler completion
  - Test trace annotations and metadata accuracy across all processing stages
  - _Requirements: 1.1, 1.4, 2.1, 2.2_

- [ ] 8.2 Test error scenario trace handling
  - Verify trace behavior during Lambda failures and retries
  - Test trace marking and error capture for various failure modes
  - Validate graceful degradation when XRay service is unavailable
  - _Requirements: 1.4, 4.1_

- [ ] 8.3 Performance impact testing
  - Measure processing latency impact of XRay instrumentation
  - Test Lambda cold start impact with XRay SDK initialization
  - Validate memory usage impact of trace data collection
  - _Requirements: 5.1, 5.2_

- [ ] 9. Documentation and cleanup
- [ ] 9.1 Update system documentation for new architecture
  - Document the simplified email processing flow without EventBridge Pipes
  - Update troubleshooting guides to include XRay trace analysis
  - Create runbooks for XRay-based incident response
  - _Requirements: 4.2, 4.3_

- [ ] 9.2 Clean up obsolete monitoring and alarms
  - Remove EventBridge Pipes related CloudWatch alarms and metrics
  - Update existing alarms to work with new SNS → Lambda architecture
  - Archive or remove obsolete log groups and metric filters
  - _Requirements: 4.1, 4.3_
