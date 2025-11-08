# Implementation Plan

A task is not complete until:

- The code has been written
- It has been successfully deployed to test: `AWS_PROFILE=ses-mail make apply ENV=test`
- It has been tested
- The `README.md` has been updated
- The task has been marked as done in this file
- A `git commit` has been made

- [ ] 1. Set up infrastructure for retry processing and monitoring
  - Create SQS retry queue and dead letter queue with environment-specific naming
  - Create Step Function for retry processing with proper IAM roles
  - Create SNS topic for token expiration notifications
  - _Requirements: 4.1, 4.4, 5.1_

- [x] 2. Update Gmail Forwarder Lambda for token management
  - [x] 2.1 Modify Gmail Forwarder to generate fresh access tokens for each session
    - Remove existing token caching/updating logic
    - Implement `generate_access_token()` function using refresh token
    - Update SSM parameter path to use new environment-specific structure
    - _Requirements: 2.1, 2.2, 2.4_

  - [x] 2.2 Add retry queue integration for token expiration failures
    - Implement `queue_for_retry()` function to store failed SES events in SQS
    - Implement `is_token_expired_error()` function to detect OAuth expiration errors
    - Add error handling to queue messages when token expires
    - _Requirements: 4.1, 4.2_

  - [x] 2.3 Update SSM parameter access for new structure
    - Change parameter paths to `/ses-mail/{environment}/gmail-forwarder/oauth/`
    - Update IAM policies to access new parameter structure
    - Read complete client credentials JSON instead of separate client ID/secret
    - _Requirements: 1.1, 1.4_

- [x] 3. Create enhanced refresh script
  - [x] 3.1 Implement OAuth credential retrieval from SSM
    - Create `retrieve_oauth_credentials()` function to fetch complete client JSON
    - Parse Google OAuth client credentials from JSON format
    - Handle SSM parameter access errors gracefully
    - _Requirements: 1.1, 1.5_

  - [x] 3.2 Implement interactive OAuth flow
    - Create `perform_interactive_oauth_flow()` function with browser interaction
    - Set up temporary local web server for OAuth callback handling
    - Handle OAuth consent flow and authorization code exchange
    - _Requirements: 1.2, 1.3_

  - [x] 3.3 Implement token storage and expiration monitoring
    - Create `store_refresh_token()` function to save new token to SSM
    - Extract expiration time from refresh token JWT payload
    - Create `setup_expiration_alarm()` function to update CloudWatch alarm
    - _Requirements: 1.4, 3.1, 3.2_

  - [x] 3.4 Implement Step Function integration for retry processing
    - Create `trigger_retry_processing()` function to start Step Function execution
    - Pass retry queue information to Step Function
    - Handle Step Function invocation errors
    - _Requirements: 5.1, 5.2_

- [x] 4. Create Step Function definition for retry processing
  - [x] 4.1 Define Step Function state machine for message processing
    - Create state machine to read messages from SQS retry queue
    - Define states for invoking Gmail Forwarder Lambda with original SES events
    - Implement error handling and retry logic with exponential backoff
    - _Requirements: 4.4, 5.2, 5.3_

  - [x] 4.2 Configure Step Function IAM permissions
    - Grant permissions to read from SQS retry queue
    - Grant permissions to invoke Gmail Forwarder Lambda
    - Grant permissions to delete processed messages from queue
    - _Requirements: 5.2_

- [x] 5. Set up CloudWatch monitoring and alerting
  - [x] 5.1 Create CloudWatch alarm for token expiration
    - Define alarm to trigger 24 hours before token expiration
    - Configure SNS notification to administrators
    - Use environment-specific naming for alarm and SNS topic
    - _Requirements: 3.1, 3.3_

  - [x] 5.2 Configure CloudWatch metrics for retry processing
    - Set up metrics for retry queue depth and processing time
    - Create alarms for retry processing failures
    - Monitor Step Function execution success/failure rates
    - _Requirements: 5.4, 5.5_

- [x] 6. Update Terraform infrastructure
  - [x] 6.1 Add SQS resources for retry processing
    - Create retry queue with environment-specific naming
    - Create dead letter queue for permanently failed messages
    - Configure queue policies and visibility timeouts
    - _Requirements: 4.1, 4.4_

  - [x] 6.2 Add Step Function resources
    - Create Step Function state machine with proper IAM role
    - Define state machine for retry processing workflow
    - Configure Step Function logging and monitoring
    - _Requirements: 5.1, 5.2_

  - [x] 6.3 Add CloudWatch and SNS resources
    - Create SNS topic for token expiration notifications
    - Create CloudWatch alarms for token expiration and retry processing
    - Configure alarm actions and notification policies
    - _Requirements: 3.1, 3.3, 5.4_

  - [x] 6.4 Update existing Lambda IAM policies
    - Add permissions for Gmail Forwarder to access new SSM parameters
    - Add permissions for Gmail Forwarder to write to retry queue
    - Add permissions for refresh script to invoke Step Function
    - _Requirements: 1.1, 4.1, 5.1_

- [ ] 7. Create comprehensive error handling and logging
  - [x] 7.1 Implement structured logging in Gmail Forwarder
    - Add detailed logging for token operations and retry queue actions
    - Implement CloudWatch Insights compatible log format
    - Log all token expiration events and error details
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 7.2 Implement error handling in refresh script
    - Add comprehensive error handling for OAuth flow failures
    - Implement logging for credential access and token operations
    - Create audit trail for all token refresh activities
    - _Requirements: 6.1, 6.4, 6.5_

  - [x] 7.3 Add monitoring for Step Function execution
    - Log Step Function execution results and retry attempts
    - Monitor and alert on Step Function failures
    - Track retry queue processing metrics and completion status
    - _Requirements: 5.3, 5.4, 6.4_
