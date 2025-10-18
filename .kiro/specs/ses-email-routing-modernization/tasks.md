# Implementation Plan

A task is not complete until:

- The code has been written
- It has been successfully deployed to test: `AWS_PROFILE=ses-mail make apply ENV=test`
- It has been tested
- The `README.md` has been updated
- The task has been marked as done in this file
- A `git commit` has been made

- [x] 1. Set up DynamoDB routing rules table and infrastructure
  - Create DynamoDB table with proper schema for routing rules
  - Implement table with single-table pattern
  - Add sample routing rules for testing
  - _Requirements: 8.1, 8.3, 8.5_

- [x] 2. Create SNS topic with X-Ray tracing configuration
  - Replace direct lambda invocation with SNS publish action in SES rule
  - Configure SNS topic with Active tracing for X-Ray
  - Set up SNS to SQS subscription for input queue
  - _Requirements: 1.1, 1.2, 10.1_

- [x] 3. Implement SQS input queue and dead letter queue infrastructure
  - Create input queue for EventBridge Pipes source
  - Configure dead letter queue with appropriate retention
  - Set up CloudWatch alarms for DLQ monitoring
  - _Requirements: 3.4, 3.6, 6.2_

- [ ] 4. Create EventBridge Pipes configuration
  - Set up EventBridge Pipes with SQS source and Event Bus target
  - Configure pipes to use router lambda for enrichment
  - Enable logging and monitoring for pipes
  - _Requirements: 2.1, 2.2, 2.7_

- [x] 5. Implement router enrichment lambda function
  - Create lambda function that enriches SES messages with routing decisions
  - Implement DynamoDB lookup logic with hierarchical address matching
  - Add email address normalization for plus addressing support
  - Include DMARC and security header analysis
  - _Requirements: 2.3, 2.4, 8.1, 8.2, 8.7_

- [ ] 6. Create EventBridge Event Bus and routing rules
  - Set up custom Event Bus for email routing
  - Create EventBridge rules for different routing actions (Gmail, bouncer)
  - Configure rules to match enriched message routing decisions
  - _Requirements: 2.5, 2.6, 3.1, 3.2, 3.4_

- [ ] 7. Set up handler SQS queues with dead letter queues
  - Create Gmail forwarder SQS queue with DLQ and CloudWatch alarms
  - Create bouncer SQS queue with DLQ and CloudWatch alarms
  - Configure retry policies and message retention
  - _Requirements: 3.4, 3.5, 3.6, 6.2_

- [x] 8. Implement Gmail forwarder lambda handler
  - Refactor existing email_processor.py logic for SQS-triggered processing
  - Update lambda to process enriched messages from SQS queue
  - Ensure lambda receives all necessary data without external lookups
  - Add X-Ray tracing support
  - _Requirements: 4.1, 4.2, 4.3, 9.2, 10.4_

- [x] 9. Implement bouncer lambda handler
  - Create new lambda function for sending bounce messages via SES
  - Process enriched messages from bouncer SQS queue
  - Implement SES bounce sending with proper error handling
  - Add X-Ray tracing support
  - _Requirements: 5.1, 5.2, 5.3, 10.4_

- [ ] 10. Configure comprehensive monitoring and alerting
  - Set up CloudWatch alarms for all lambda function failures
  - Create alarms for SQS queue age and DLQ messages
  - Configure SNS topic for alert notifications
  - Add custom metrics for email processing success/failure rates
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [ ] 11. Enable X-Ray tracing across all components
  - Configure X-Ray tracing for all SQS queues
  - Enable tracing for EventBridge Pipes and Event Bus
  - Add X-Ray tracing to all lambda functions
  - Implement custom annotations for email metadata tracking
  - _Requirements: 10.2, 10.3, 10.4, 10.5, 10.6_

- [ ] 12. Update SES receipt rule configuration
  - Remove synchronous validator lambda action from SES rule
  - Update SES rule to only include S3 storage and SNS publish actions
  - Clean up validator lambda IAM permissions and resources
  - _Requirements: 1.1, 1.2, 1.3_

- [ ] 13. Create IAM roles and policies for new architecture
  - Create IAM role for EventBridge Pipes execution
  - Set up IAM policies for router lambda DynamoDB access
  - Configure IAM permissions for Event Bus and SQS access
  - Update existing lambda roles for new SQS-based processing
  - _Requirements: 2.1, 2.3, 4.1, 5.1_

- [ ] 14. Implement integration tests for end-to-end processing
  - Create test that sends email through complete pipeline
  - Verify X-Ray trace spans across all components
  - Test routing decisions for different email addresses
  - Validate handler processing and error scenarios
  - _Requirements: 2.1, 2.2, 2.3, 4.1, 5.1, 10.5_

- [ ] 15. Add monitoring dashboard and operational procedures
  - Create CloudWatch dashboard for email processing metrics
  - Document operational procedures for managing routing rules
  - Set up log aggregation and analysis for troubleshooting
  - Create runbook for handling DLQ messages and failures
  - _Requirements: 6.1, 6.4, 8.5_

- [x] 16. Add a Resource Group
  - Create an AWS Resource Group that includes all resources for a particular environment
  - Uses tag-based query matching on Project=ses-mail and Environment={env}
  - Outputs include Resource Group name and AWS Console URL

- [x] 17. Add a myApplication application
  - Create an AWS Service Catalog AppRegistry application for myApplications
  - Resources automatically discovered via Application=ses-mail-{env} tag (set in provider default_tags)
  - Optional tag-sync automation configured (requires GLE enabled at account level)
  - Provides application-level view in AWS Console Systems Manager AppManager
  - Outputs include application ID and myApplications Console URL
  - Tag-sync Lambda and IAM role created for automated resource discovery
