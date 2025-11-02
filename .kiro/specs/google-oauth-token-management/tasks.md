# Implementation Plan

Convert the feature design into a series of prompts for a code-generation LLM that will implement each step with incremental progress. Make sure that each prompt builds on the previous prompts, and ends with wiring things together. There should be no hanging or orphaned code that isn't integrated into a previous step. Focus ONLY on tasks that involve writing, modifying, or testing code.

A task is not complete until:

- The code has been written
- It has been successfully deployed to test: `AWS_PROFILE=ses-mail make apply ENV=test`
- It has been tested
- The `README.md` has been updated
- The task has been marked as done in this file
- A `git commit` has been made


- [ ] 1. Extend DynamoDB single table schema and create token management utilities
  - Add new fields to existing SMTP_USER records for Gmail refresh tokens
  - Create utility functions for reading/writing Gmail token fields in single table
  - Create queued message record management for single table with S3 location storage
  - Update routing records to use Cognito user IDs as targets instead of Gmail addresses
  - _Requirements: 7.1, 7.2, 4.1, 4.4_

- [ ] 2. Implement multi-user token manager service
  - [ ] 2.1 Create token manager Lambda function with refresh token operations
    - Implement get_user_record() for reading extended SMTP_USER records by Cognito user ID
    - Implement get_fresh_access_token() using Google OAuth refresh flow
    - Implement validate_refresh_token() with expiration checking
    - Implement store_gmail_tokens() for updating SMTP_USER records
    - _Requirements: 4.1, 4.4, 4.5_
  
  - [ ] 2.2 Implement EventBridge scheduled rule management for token expiration
    - Create update_scheduled_alert() for dynamic rule creation/cancellation
    - Implement refresh token expiration monitoring (1 day before expiry)
    - Add rule cleanup when tokens are renewed
    - _Requirements: 5.1, 5.3_

- [ ] 3. Modify Gmail forwarder Lambda for refresh token integration
  - [ ] 3.1 Update Gmail forwarder to use token manager service
    - Modify gmail_forwarder.py to extract Cognito user ID from routing target
    - Call token manager for fresh access tokens using Cognito user ID
    - Remove automatic refresh token logic and access token storage
    - _Requirements: 1.1, 1.3, 1.4_
  
  - [ ] 3.2 Implement message queuing on refresh token failures
    - Add message queuing logic when refresh tokens are expired/invalid
    - Store S3 location and email metadata in single table with Cognito user ID context
    - Implement graceful error handling without throwing exceptions
    - Prevent deletion of S3 email when queuing for later processing
    - _Requirements: 1.2, 1.5, 3.1, 3.2_
  
  - [ ] 3.3 Update error handling and logging with user context
    - Add Cognito user ID context to all log messages
    - Implement proper SQS batch response handling for queued messages
    - Add CloudWatch metrics for refresh token failures per user
    - _Requirements: 1.4, 1.5, 5.4_

- [ ] 4. Create queue processor Lambda for handling queued messages
  - [ ] 4.1 Implement queue processor with user-specific processing
    - Create Lambda function to process queued messages per Cognito user ID
    - Implement S3 email retrieval using stored bucket/key information
    - Implement user isolation and parallel processing logic
    - Add retry logic with exponential backoff per user
    - Delete S3 email after successful Gmail delivery
    - _Requirements: 3.3, 3.4_
  
  - [ ] 4.2 Add triggers for queue processing
    - Implement manual trigger capability from API
    - Add automatic trigger after refresh token renewal
    - Create scheduled trigger for periodic queue checks (15 minutes)
    - _Requirements: 3.3_
  
  - [ ]* 4.3 Add queue processing monitoring and metrics
    - Implement CloudWatch metrics for queue processing success/failure rates
    - Add per-user queue depth and age monitoring
    - Create alarms for queue backup scenarios
    - _Requirements: 5.2, 5.4_

- [ ] 5. Set up Cognito User Pool and authentication infrastructure
  - [x] 5.1 Create Cognito User Pool with custom user ID attributes
    - Set up Cognito User Pool for web UI authentication
    - Configure user pool domain and OAuth configuration
    - Set up user registration and management policies
    - _Requirements: 6.1, 6.2_
  
  - [ ] 5.2 Implement user ID migration strategy
    - Create migration utility to convert existing SMTP usernames to Cognito User IDs
    - Update existing SMTP_USER records with new Cognito user ID format
    - Update existing routing records to use Cognito user IDs as targets
    - _Requirements: 6.2, 7.2_

- [ ] 6. Create token management API with Cognito authentication
  - [ ] 6.1 Implement API Gateway with Cognito authorizer
    - Set up API Gateway with Cognito User Pool authorizer
    - Configure CORS for CloudFront integration
    - Add request validation and rate limiting
    - Extract Cognito user ID from JWT tokens automatically
    - _Requirements: 6.1, 6.3_
  
  - [ ] 6.2 Create token status and renewal API endpoints
    - Implement GET /api/token/status endpoint (user ID from JWT)
    - Implement POST /api/token/renew for OAuth flow initiation
    - Implement GET /api/token/callback for OAuth callback handling
    - Add user context validation and authorization
    - _Requirements: 2.3, 2.4, 2.5, 6.3_
  

  
  - [ ]* 6.3 Add user management and health check endpoints
    - Implement GET /api/users/me for current user information
    - Implement GET /api/health for system health monitoring
    - Add comprehensive error handling and logging
    - _Requirements: 6.5, 6.6_

- [ ] 7. Build web UI for token management
  - [ ] 7.1 Create React application with Cognito authentication
    - Set up React application with AWS Amplify for Cognito integration
    - Implement login/logout functionality with Cognito User Pool
    - Add user session management and token handling
    - _Requirements: 6.1, 6.4_
  
  - [ ] 7.2 Implement token status dashboard
    - Create dashboard showing current refresh token status for authenticated user
    - Display token expiration information and renewal history
    - Implement real-time status updates
    - _Requirements: 2.3, 6.4, 6.5_
  
  - [ ] 7.3 Create Google OAuth renewal flow interface
    - Implement token renewal interface with Google OAuth integration
    - Add secure OAuth callback handling
    - Provide user feedback during renewal process
    - _Requirements: 2.4, 2.5_
  


- [ ] 8. Deploy and configure CloudFront integration
  - [ ] 8.1 Configure S3 bucket for web UI hosting
    - Set up S3 bucket for static web application hosting
    - Configure bucket policies for CloudFront access
    - Set up build and deployment pipeline
    - _Requirements: 2.1_
  
  - [ ] 8.2 Update CloudFront distribution with new behavior
    - Add new CloudFront behavior for /token-management/* path
    - Configure caching policies for API and static content
    - Set up HTTPS-only access and security headers
    - _Requirements: 2.1_

- [ ] 9. Implement monitoring and alerting infrastructure
  - [ ] 9.1 Create CloudWatch dashboards for per-user monitoring
    - Build dashboards showing refresh token status per user
    - Add queue depth and processing metrics per user
    - Include API performance and error rate monitoring
    - _Requirements: 5.4, 5.5_
  
  - [ ] 9.2 Set up CloudWatch alarms for critical scenarios
    - Create alarms for refresh token expiration (1 day before)
    - Add alarms for queue backup scenarios per user
    - Implement alerts for processing failure rates
    - _Requirements: 5.1, 5.2, 5.3_
  
  - [ ]* 9.3 Configure SNS notifications for alerts
    - Set up SNS topics for different alert types
    - Configure email/SMS notifications for administrators
    - Add alert escalation policies
    - _Requirements: 5.3_

- [ ] 10. Integration testing and deployment
  - [ ] 10.1 Create integration tests for end-to-end flow
    - Test email ingestion → queuing → token renewal → processing flow
    - Verify user isolation and security boundaries
    - Test error scenarios and recovery mechanisms
    - _Requirements: All requirements_
  
  - [ ] 10.2 Deploy infrastructure updates via Terraform
    - Update existing Terraform modules with new resources
    - Deploy DynamoDB table schema extensions
    - Deploy new Lambda functions and API Gateway configuration
    - _Requirements: 7.1, 7.2, 7.4_
  
  - [ ] 10.3 Perform user acceptance testing with web UI
    - Test Cognito authentication flow
    - Verify token renewal process through web interface
    - Test queue management and message processing
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_