# Requirements Document

## Introduction

This feature addresses the limitations of Google OAuth tokens in testing mode, which expire after 7 days and cannot be automatically refreshed. The system will modify the Gmail forwarder to use access tokens without refresh attempts, provide a secure web UI for manual token renewal, and implement message queuing to hold emails during token expiration periods.

## Glossary

- **Gmail_Forwarder**: The Lambda function that forwards emails to Gmail using Google APIs
- **Token_Manager**: The system component responsible for managing Google OAuth refresh tokens per user
- **Web_UI**: The CloudFront-hosted interface for token management
- **Message_Queue**: Storage system for emails awaiting delivery during token expiration
- **Refresh_Token**: Long-lived token used to obtain new access tokens (7-day limit in testing mode)
- **User_ID**: Unique identifier for each user (Cognito User ID, may replace existing SMTP usernames)
- **SMTP_User_Record**: Existing user record in single table design that will be extended with Gmail token fields
- **Single_Table**: The existing `ses-email-routing-{environment}` DynamoDB table using single-table design

## Requirements

### Requirement 1

**User Story:** As a system administrator, I want the Gmail forwarder to generate fresh access tokens from refresh tokens without storing access tokens, so that token expiration is handled gracefully for each user.

#### Acceptance Criteria

1. WHEN the Gmail_Forwarder processes an email, THE Gmail_Forwarder SHALL generate a fresh access token using the refresh token for the target User_ID from the SMTP_User_Record
2. IF the refresh token for a User_ID is invalid or expired, THEN THE Gmail_Forwarder SHALL queue the message for later delivery with the User_ID context
3. THE Gmail_Forwarder SHALL NOT store access tokens or attempt to refresh expired refresh tokens automatically for any User_ID
4. WHEN an API call fails due to authentication for a User_ID, THE Gmail_Forwarder SHALL log the failure and queue the message with User_ID context
5. THE Gmail_Forwarder SHALL continue processing without throwing exceptions when refresh tokens are invalid for any User_ID

### Requirement 2

**User Story:** As a system administrator, I want a secure web interface for token renewal, so that I can manually refresh Google OAuth refresh tokens for specific users when needed.

#### Acceptance Criteria

1. THE Web_UI SHALL be accessible through the existing CloudFront distribution
2. THE Web_UI SHALL require Cognito authentication before allowing access to token management
3. WHEN a user accesses the token renewal interface, THE Web_UI SHALL display current refresh token status for their User_ID from their SMTP_User_Record
4. THE Web_UI SHALL provide a secure OAuth flow for obtaining new refresh tokens for the authenticated User_ID
5. WHEN token renewal is completed for a User_ID, THE Web_UI SHALL update the SMTP_User_Record with new refresh token and trigger queued message processing for that User_ID

### Requirement 3

**User Story:** As a system administrator, I want emails to be queued during refresh token expiration, so that no messages are lost when refresh tokens are invalid for specific users.

#### Acceptance Criteria

1. WHEN the Gmail_Forwarder cannot deliver an email due to refresh token issues for a User_ID, THE Message_Queue SHALL store the complete email data in the Single_Table with User_ID context
2. THE Message_Queue SHALL preserve email metadata including timestamps, routing information, and User_ID in the Single_Table
3. WHEN new valid refresh tokens are available for a User_ID, THE Message_Queue SHALL automatically process all queued messages for that User_ID from the Single_Table
4. THE Message_Queue SHALL maintain message order per User_ID and prevent duplicate deliveries
5. THE Message_Queue SHALL provide visibility into queued message count and status per User_ID from the Single_Table

### Requirement 4

**User Story:** As a system administrator, I want secure refresh token storage and management per user, so that Google OAuth credentials are protected from unauthorized access and isolated between users.

#### Acceptance Criteria

1. THE Token_Manager SHALL store refresh tokens in the existing SMTP_User_Record in the Single_Table with User_ID isolation
2. THE Token_Manager SHALL use AWS IAM roles and policies to restrict token access per User_ID
3. THE Token_Manager SHALL log all token access and renewal activities with User_ID context
4. WHEN refresh tokens are updated for a User_ID, THE Token_Manager SHALL update the SMTP_User_Record and notify relevant system components with User_ID context
5. THE Token_Manager SHALL provide refresh token expiration monitoring and alerting per User_ID

### Requirement 5

**User Story:** As a system administrator, I want monitoring and alerting for refresh token status per user, so that I can proactively manage token renewals for each user.

#### Acceptance Criteria

1. THE Token_Manager SHALL monitor refresh token expiration dates per User_ID using EventBridge scheduled rules and send alerts before expiration
2. THE Token_Manager SHALL track message queue depth per User_ID in the Single_Table and alert when messages are backing up
3. WHEN refresh tokens expire for a User_ID, THE Token_Manager SHALL send immediate notifications with User_ID context
4. THE Token_Manager SHALL provide metrics on refresh token usage and renewal frequency per User_ID
5. THE Token_Manager SHALL integrate with existing CloudWatch monitoring infrastructure with User_ID dimensions

### Requirement 6

**User Story:** As a user, I want to authenticate with AWS Cognito in the web interface, so that I can securely manage tokens and view queues for my own account.

#### Acceptance Criteria

1. THE Web_UI SHALL authenticate users using AWS Cognito User Pool
2. THE Web_UI SHALL use Cognito User IDs as the primary user identifier (may require updating existing SMTP_User_Record usernames to Cognito User IDs)
3. WHEN a user logs in via Cognito, THE Web_UI SHALL restrict access to only their User ID's refresh tokens and queued messages from the Single_Table
4. THE Web_UI SHALL prevent users from accessing other User ID's token information or queued messages
5. THE Web_UI SHALL display the authenticated user information prominently in the interface
6. THE Web_UI SHALL log all user actions with Cognito User ID context for audit purposes

### Requirement 7

**User Story:** As a system architect, I want to extend the existing single table design, so that Gmail token management integrates seamlessly with existing infrastructure.

#### Acceptance Criteria

1. THE Token_Manager SHALL use the existing `ses-email-routing-{environment}` Single_Table for all data storage
2. THE Token_Manager SHALL extend existing SMTP_User_Record entities with Gmail refresh token fields
3. THE Token_Manager SHALL use the existing DynamoDB Streams configuration for event processing
4. THE Token_Manager SHALL maintain consistency with existing single-table design patterns
5. THE Token_Manager SHALL NOT create additional DynamoDB tables or GSI indexes