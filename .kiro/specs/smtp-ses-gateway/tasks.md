# Implementation Plan

A task is not complete until:

- The code has been written
- It has been successfully deployed to test: `AWS_PROFILE=ses-mail make apply ENV=test`
- It has been tested
- The `README.md` has been updated
- The task has been marked as done in this file
- A `git commit` has been made

- [ ] 1. Set up SES domain configuration and outputs
  - Create Terraform configuration for SES domain identity
  - Configure SES DKIM signing for domains
  - Create Terraform outputs for required DNS records (domain verification, DKIM CNAMEs)
  - Create outputs for recommended SPF, DMARC, and MTA-STS records
  - _Requirements: 1.1, 8.1, 8.2_

- [x] 2. Configure DynamoDB Streams for existing table
  - Enable DynamoDB Streams on existing table if not already enabled
  - Configure stream to capture INSERT and MODIFY events
  - Set up appropriate IAM permissions for Lambda to read streams
  - _Requirements: 4.2, 5.2_

- [ ] 3. Create SMTP credential manager Lambda function
  - [x] 3.1 Implement core credential creation logic
    - Write Lambda function to process DynamoDB Stream events with X-Ray tracing
    - Implement structured JSON logging with correlation IDs
    - Detect new records with PK="SMTP_USER" and status="pending"
    - Create programmatic-only IAM users with unique names
    - Generate IAM access keys for SMTP authentication
    - _Requirements: 4.1, 4.3, 9.1, 9.2_

  - [x] 3.2 Implement email restriction policy generation
    - Create dynamic IAM policy based on allowed_from_addresses field
    - Apply StringLike condition for ses:FromAddress in policy
    - Attach inline policy to created IAM user
    - _Requirements: 4.1, 4.4_

  - [x] 3.3 Implement SMTP password conversion and encryption
    - Convert IAM secret access key to SES SMTP password using AWS algorithm
    - Encrypt SMTP credentials using KMS with X-Ray tracing
    - Store encrypted credentials in DynamoDB record with detailed logging
    - Update record status to "active" upon successful completion
    - Log all operations with correlation IDs for traceability
    - _Requirements: 4.2, 4.5, 9.1, 9.3, 9.4_

  - [x] 3.4 Remove IAM user and credentials when the record is deleted
    - Handle REMOVE events from DynamoDB Streams
    - List and delete all IAM access keys for the user
    - List and delete all inline IAM policies
    - Delete the IAM user
    - Publish CloudWatch metrics for deletion operations
    - Implement idempotent cleanup (handle already-deleted users gracefully)

- [ ] 4. Implement error handling and DLQ processing
  - [ ] 4.1 Configure SQS Dead Letter Queue for credential manager
    - Create SQS DLQ with appropriate retention settings
    - Configure Lambda function to send failed events to DLQ
    - Set up CloudWatch alarms for DLQ message count
    - _Requirements: 7.4_

  - [ ] 4.2 Create DLQ processor Lambda function
    - Write Lambda to process messages from DLQ with X-Ray tracing
    - Implement retry logic with exponential backoff
    - Update DynamoDB record status to "failed" for permanent failures
    - Log detailed structured error information with correlation IDs
    - Trace all AWS service interactions for performance monitoring
    - _Requirements: 7.4, 7.5, 9.1, 9.3, 9.5_

- [ ] 5. Set up SES bounce and complaint handling
  - Create SNS topics for bounce and complaint notifications
  - Configure SES to publish bounce and complaint events to SNS
  - Set up CloudWatch alarms for high bounce/complaint rates
  - Create documentation for handling reputation issues
  - _Requirements: 2.4, 7.2, 7.5_

- [ ] 6. Implement monitoring and logging
  - [ ] 6.1 Configure CloudWatch dashboards
    - Create dashboard showing SMTP credential creation metrics
    - Add widgets for SES sending statistics (sends, bounces, complaints)
    - Include DLQ message count and Lambda error rates
    - Add X-Ray service map and trace analytics widgets
    - Include Lambda performance metrics from X-Ray traces
    - _Requirements: 7.1, 7.3, 9.4_

  - [ ] 6.2 Set up CloudWatch alarms
    - Create alarms for Lambda function errors
    - Set up alerts for DLQ message accumulation
    - Configure notifications for high bounce/complaint rates
    - _Requirements: 7.5_

- [ ] 7. Create comprehensive documentation
  - [ ] 7.1 Write DNS setup documentation
    - Document step-by-step domain verification process
    - Provide examples of SPF, DKIM, DMARC record setup
    - Include MTA-STS and TLS-RPT configuration instructions
    - Add troubleshooting guide for common DNS issues
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [ ] 7.2 Create SMTP client configuration guides
    - Write Gmail SMTP configuration instructions
    - Document Outlook and Thunderbird setup procedures
    - Include troubleshooting steps for connection issues
    - Provide testing procedures for verifying setup
    - _Requirements: 1.3, 1.4_

  - [ ] 7.3 Document administrative procedures
    - Create guide for manually adding SMTP credential records to DynamoDB
    - Document credential status monitoring procedures
    - Provide instructions for disabling/enabling credentials
    - Include security best practices and recommendations
    - _Requirements: 4.3, 7.4, 8.5_

- [ ] 8. Create integration tests
  - [ ] 8.1 Test credential creation workflow
    - Write test to insert pending record in DynamoDB
    - Verify Lambda creates IAM user with correct permissions
    - Validate SMTP credentials are properly encrypted and stored
    - Test record status updates to "active"
    - _Requirements: 4.1, 4.2, 4.5_

  - [ ] 8.2 Test email restriction enforcement
    - Create test IAM users with different allowed_from_addresses
    - Verify IAM policies correctly restrict sending addresses
    - Test that unauthorized from addresses are blocked
    - _Requirements: 4.1, 4.4_

  - [ ] 8.3 Test error handling and DLQ processing
    - Simulate Lambda failures and verify DLQ behaviour
    - Test DLQ processor retry logic
    - Verify proper error logging and status updates
    - _Requirements: 7.4_

- [ ] 9. Deploy and validate system
  - Deploy Terraform configuration to target environment
  - Verify all resources are created correctly
  - Test end-to-end workflow with sample SMTP credential
  - Validate monitoring dashboards and alarms are functional
  - _Requirements: 3.1, 5.1, 5.3_
