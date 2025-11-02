# Design Document

## Overview

This design addresses Google OAuth refresh token management limitations in testing mode by implementing a comprehensive multi-user solution. The system extends existing SMTP_USER records in the single table design to include Gmail refresh tokens, provides a Cognito-authenticated web UI for manual token renewal, and implements message queuing during token expiration periods.

The solution builds upon the existing SES mail infrastructure, leveraging the current single table design, CloudFront distribution, and Lambda functions while extending existing user records with Gmail functionality.

## Architecture

### High-Level Architecture

```mermaid
graph TB
    subgraph "Existing Flow"
        SES[SES] --> SNS[SNS Topic]
        SNS --> Router[Router Lambda]
        Router --> EventBridge[EventBridge]
        EventBridge --> GmailQueue[Gmail SQS Queue]
    end
    
    subgraph "Extended Components"
        GmailQueue --> GmailForwarder[Modified Gmail Forwarder]
        GmailForwarder --> TokenManager[Token Manager]
        GmailForwarder --> SingleTable[(Single Table - Extended)]
        
        CloudFront[CloudFront] --> WebUI[Cognito Web UI]
        WebUI --> CognitoAuth[Cognito User Pool]
        WebUI --> TokenAPI[Token API Lambda]
        TokenAPI --> TokenManager
        
        TokenManager --> SingleTable
        TokenManager --> EventBridge2[EventBridge Scheduled Rules]
        TokenManager --> CloudWatch[CloudWatch Alarms]
        
        QueueProcessor[Queue Processor Lambda] --> TokenManager
        QueueProcessor --> GmailAPI[Gmail API]
        
        SingleTable --> DynamoStreams[DynamoDB Streams]
        DynamoStreams --> EventProcessor[Event Processor Lambda]
    end
    
    subgraph "External"
        GmailAPI --> Gmail[Gmail Service]
        WebUI --> GoogleOAuth[Google OAuth]
    end
```

### Component Interactions

1. **Modified Gmail Forwarder**: Generates fresh access tokens from refresh tokens, queues messages on failure
2. **Token Manager**: Manages refresh tokens stored in extended SMTP_USER records
3. **Message Queue**: Stores failed messages in single table with user context
4. **Cognito Web UI**: Secure interface for token renewal using Cognito authentication
5. **Queue Processor**: Processes queued messages when refresh tokens are renewed

## Data Storage Infrastructure

### Single Table Design (Existing Table Extended)

**Table Configuration**:
- Table name: `ses-email-routing-{environment}` (existing table)
- Partition key: `PK` (String)
- Sort key: `SK` (String)
- Encryption: AWS managed encryption at rest
- Point-in-time recovery: Enabled
- Billing mode: Pay-per-request
- DynamoDB Streams: Enabled (NEW_AND_OLD_IMAGES)

**No Additional Indexes Required**:
- Uses direct PK/SK queries and scheduled processing
- EventBridge scheduled rules for refresh token expiration monitoring
- No GSI costs or complexity

**Entity Design** (extending existing single table):

| Entity Type | PK | SK | Key Attributes |
|-------------|----|----|----------------|
| Existing Route | `ROUTE#{email-pattern}` | `RULE#v1` | `entity_type`, `recipient`, `action`, `target`, `enabled` |
| Extended User | `SMTP_USER` | `USER#{cognito_user_id}` | `entity_type`, `status`, `encrypted_credentials`, `smtp_username`, `gmail_refresh_token`, `gmail_refresh_expires_at`, `gmail_address`, `gmail_renewal_count`, `gmail_status` |
| Queued Message | `GMAIL_QUEUE#{cognito_user_id}` | `MSG#{message_id}` | `entity_type`, `queued_at`, `retry_count`, `message_status`, `s3_bucket`, `s3_key`, `email_metadata` |

**Access Patterns**:
1. Get user record with Gmail tokens: Get Item PK=`SMTP_USER`, SK=`USER#{cognito_user_id}`
2. Get queued messages for user: Query PK=`GMAIL_QUEUE#{cognito_user_id}`
3. Monitor expiring tokens: EventBridge scheduled rules per user
4. Process queued messages: Triggered per user after token renewal
5. Update refresh token fields: Update Item with new gmail_refresh_token and gmail_refresh_expires_at

## Components and Interfaces

### 1. Modified Gmail Forwarder Lambda

**Purpose**: Process Gmail forwarding requests with per-user refresh token management

**Key Changes**:
- Generate fresh access tokens from refresh tokens (no storage of access tokens)
- Add user-specific message queuing on refresh token failures
- Extract user ID from routing information or Gmail destination mapping

**Interface**:
```python
def lambda_handler(event, context):
    # Process SQS messages from gmail-forwarder queue
    # For each message:
    #   1. Extract Cognito user ID from routing target information
    #   2. Generate fresh access token using refresh token for Cognito user ID
    #   3. If refresh token valid: proceed with Gmail API call
    #   4. If refresh token invalid/expired: queue message in single table
    #   5. Return appropriate status for SQS batch processing
```

**User ID Extraction**:
- Extract Cognito user ID directly from routing information (target field in routing rules)
- Routing rules now specify Cognito user ID instead of Gmail address as target
- No lookup required - direct mapping from route to user record

**Error Handling**:
- Refresh token expired → Queue message with user ID, return success to SQS
- API errors (non-auth) → Return failure to SQS for retry
- Access token generation failures → Queue message with user ID, alert administrators

### 2. Multi-User Token Manager Service

**Purpose**: Manage refresh tokens stored in extended SMTP_USER records

**Implementation**: Lambda function with the following methods:

```python
class MultiUserTokenManager:
    def get_user_record(cognito_user_id: str) -> Optional[dict]:
        # Get complete user record with Gmail tokens from single table
        # Single GetItem call: PK=SMTP_USER, SK=USER#{cognito_user_id}
    
    def get_fresh_access_token(cognito_user_id: str) -> Optional[str]:
        # Generate fresh access token using refresh token from user record
        # Return None if refresh token is expired or invalid
        # Never stores access tokens
    
    def validate_refresh_token(cognito_user_id: str) -> bool:
        # Validate refresh token is still valid (not expired after 7 days)
        # Check against stored gmail_refresh_expires_at
    
    def store_gmail_tokens(cognito_user_id: str, refresh_token: str, refresh_expires_at: datetime, gmail_address: str):
        # Update existing SMTP_USER record with Gmail refresh token fields
        # Update scheduled EventBridge rule for refresh token expiration monitoring
        # Cancel old scheduled rule if exists
    
    def get_gmail_token_status(cognito_user_id: str) -> dict:
        # Return Gmail refresh token expiration status from user record
        # Check gmail_refresh_expires_at against current time
    
    def update_scheduled_alert(cognito_user_id: str, refresh_expires_at: datetime):
        # Create/update EventBridge scheduled rule for refresh token expiration alert
        # Cancel previous rule if exists
        # Alert 1 day before 7-day refresh token expires
    
    def trigger_queue_processing(cognito_user_id: str):
        # Trigger processing of queued messages for specific user
```

### 3. User-Specific Message Queue System

**Purpose**: Store messages during refresh token expiration periods with user context

**Implementation**: Store queued messages directly in the single table

**Record Structure**:
```json
{
  "PK": "GMAIL_QUEUE#cognito-user-id-123",
  "SK": "MSG#ses-message-id-456",
  "entity_type": "GMAIL_QUEUE_MESSAGE",
  "queued_at": "2024-01-01T00:00:00Z",
  "retry_count": 0,
  "message_status": "pending",
  "s3_bucket": "ses-email-bucket-env",
  "s3_key": "emails/ses-message-id-456",
  "email_metadata": {
    "source": "sender@example.com",
    "subject": "Email subject",
    "destination": "user@gmail.com",
    "size": 1024
  },
  "last_attempt": null,
  "error_details": null,
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T00:00:00Z"
}
```

**Message Status Values**:
- `pending`: Waiting for valid refresh token
- `processing`: Currently being processed
- `completed`: Successfully delivered to Gmail
- `failed`: Permanent failure after max retries
- `cancelled`: Manually cancelled by administrator

**Processing Logic**:
- Query single table for user's queued messages where message_status = "pending"
- Process in chronological order (sort by queued_at)
- Atomic updates for retry counts, status, and error tracking
- Messages remain in table with final status for audit trail

### 4. Cognito-Authenticated Web UI for Token Management

**Purpose**: Secure interface for per-user manual token renewal

**Architecture**:
- Static React/HTML application hosted on S3
- Served via existing CloudFront distribution
- New CloudFront behavior: `/token-management/*`

**Authentication**:
- AWS Cognito User Pool for user authentication
- Cognito User IDs used as primary user identifier
- May require migration of existing SMTP usernames to Cognito User IDs
- IAM roles for API access control with user context
- Session management with secure cookies including User ID

**UI Components**:
1. **Login Page**: Cognito-based authentication
2. **Token Dashboard**: User-specific refresh token status, expiration information, renewal history
3. **Token Renewal**: Google OAuth flow integration for authenticated user
4. **User Profile**: Display current User ID and account information

**User ID Management**:
- Cognito User ID extracted from JWT token automatically
- No need for user ID in API paths - extracted from authentication context
- User information displayed prominently in UI header
- All operations scoped to authenticated user automatically

**API Endpoints** (via API Gateway + Lambda):
```
GET  /api/token/status               - Get current Gmail refresh token status for authenticated user
POST /api/token/renew                - Initiate OAuth renewal flow for authenticated user
GET  /api/token/callback             - OAuth callback handler for authenticated user
GET  /api/users/me                   - Get current authenticated user info
GET  /api/health                     - System health check
```

**Note**: User ID is extracted from Cognito JWT token, eliminating need for user ID in URL paths

### 5. User Queue Processor Lambda

**Purpose**: Process queued messages for specific users when their refresh tokens are available

**Trigger**: 
- Manual trigger from Web UI for specific user
- Automatic trigger after user refresh token renewal
- Scheduled trigger (every 15 minutes) to check for valid tokens per user
- Event-driven trigger when user records are updated via DynamoDB Streams

**Processing Logic**:
```python
def process_queued_messages(cognito_user_id: str = None):
    # If cognito_user_id provided, process only that user's queue
    # Otherwise, process all users with valid refresh tokens
    
    users_to_process = [cognito_user_id] if cognito_user_id else get_users_with_valid_tokens()
    
    for user in users_to_process:
        # 1. Validate current refresh token for user
        # 2. If valid, generate fresh access token and process messages from user's pending queue
        # 3. For each message:
        #    - Retrieve email from S3 using stored bucket/key
        #    - Attempt Gmail API call with fresh access token
        #    - On success: delete from queue and delete S3 email
        #    - On auth failure: stop processing for this user, alert admins
        #    - On other failure: increment retry count
        # 4. Update metrics and logs with user context
```

**User Isolation**:
- Process messages in user-specific batches
- Maintain separate retry counters per user
- Independent error handling per user
- Parallel processing for multiple users with valid refresh tokens

## Data Models

### Extended SMTP_USER Record

**Example Records** (in existing `ses-email-routing-{environment}` table):

| PK | SK | entity_type | status | encrypted_credentials | smtp_username | gmail_refresh_token | gmail_refresh_expires_at | gmail_address | gmail_renewal_count | gmail_status | created_at | updated_at |
|----|----|-----------|---------|--------------------|---------------|-------------------|--------------------------|---------------|-------------------|-------------|-----------|-----------|
| SMTP_USER | USER#cognito-user-id-123 | SMTP_USER | active | KMS_encrypted_blob | AKIAIOSFODNN7EXAMPLE | 1//04... | 2024-01-08T00:00:00Z | alice@gmail.com | 5 | valid | 2024-01-01T00:00:00Z | 2024-01-01T00:00:00Z |
| SMTP_USER | USER#cognito-user-id-456 | SMTP_USER | active | KMS_encrypted_blob | AKIAI44QH8DHBEXAMPLE | 1//05... | 2024-01-08T00:15:00Z | bob@gmail.com | 2 | valid | 2024-01-01T00:15:00Z | 2024-01-01T00:15:00Z |

### Example Routing Records (Updated to use Cognito User IDs)

| PK | SK | entity_type | recipient | action | target | enabled |
|----|----|-----------|-----------|---------|---------|---------| 
| ROUTE#alice@example.com | RULE#v1 | ROUTE | alice@example.com | forward-to-gmail | cognito-user-id-123 | true |
| ROUTE#bob@example.com | RULE#v1 | ROUTE | bob@example.com | forward-to-gmail | cognito-user-id-456 | true |

## Scheduled Processing Strategy

**Refresh Token Expiration Monitoring**:
- EventBridge scheduled rules created dynamically per user
- Schedule created/updated when refresh token is stored with 7-day expiration
- Rule triggers 1 day before refresh token expiration to send alerts
- Old rules automatically cancelled when new refresh tokens are stored
- DynamoDB Streams can trigger rule updates when user records change

**Queue Processing**:
- Triggered per user after refresh token renewal (immediate processing)
- EventBridge scheduled rule for periodic queue checks (every 15 minutes)
- Direct PK query: `GMAIL_QUEUE#{user_id}` to get all queued messages for user
- No GSI required - efficient single-partition queries

**Benefits of Scheduled Approach**:
- No GSI costs or complexity
- Predictable processing based on known refresh token expiration times
- Efficient per-user processing
- Easy to cancel/reschedule when tokens are renewed

## Error Handling

### Refresh Token Expiration Scenarios

1. **Refresh Token Expired** (7-day limit):
   - All new messages queued for that user
   - Critical alert sent to administrators
   - Web UI shows "Token Renewal Required" status for that user

2. **Access Token Generation Failure**:
   - Queue message for later retry
   - Log error with user context
   - Continue processing other users

3. **API Rate Limiting**:
   - Implement exponential backoff per user
   - Queue messages during rate limit periods
   - Resume processing when limits reset

### Queue Management

1. **Processing Failures**:
   - Update message status to "failed" after max retries
   - Manual intervention capabilities via Web UI
   - Audit trail preserved in single table

2. **Duplicate Messages**:
   - Deduplication based on SES message ID and user ID
   - Idempotent processing logic
   - Audit trail for duplicate detection

## Security Considerations

### Token Security

1. **Storage**: Refresh tokens stored in existing encrypted single table
2. **Access**: IAM roles with least privilege principle per user
3. **Rotation**: Secure refresh token renewal process via Cognito Web UI
4. **Audit**: All token operations logged and monitored with user context

### Web UI Security

1. **Authentication**: AWS Cognito User Pool
2. **Authorization**: User-based access control using Cognito User IDs
3. **Transport**: HTTPS only via CloudFront
4. **Session**: Secure session management with timeout

### API Security

1. **Authentication**: Cognito JWT tokens
2. **Rate Limiting**: API Gateway throttling
3. **Input Validation**: Comprehensive request validation
4. **Logging**: All API calls logged for audit with user context

## Monitoring and Alerting

1. **CloudWatch Metrics**:
   - Refresh token expiration countdown per user
   - Queue depth and age per user
   - Processing success/failure rates per user
   - Gmail API call latency and errors
   - Access token generation success/failure rates

2. **Alarms**:
   - Refresh token expires in < 1 day (via scheduled EventBridge rules)
   - Queue depth > 100 messages per user
   - Processing failures > 5% rate per user
   - Access token generation failures
   - Web UI authentication failures

3. **Dashboards**:
   - Real-time system status per user
   - Historical performance trends
   - Error rate analysis per user
   - Refresh token renewal history per user

## Benefits of This Design

1. **Extends Existing Infrastructure**: Reuses existing single table and SMTP_USER records
2. **No Additional Tables**: All data stored in existing `ses-email-routing-{environment}` table
3. **No Lookup Tables**: Direct Cognito user ID mapping eliminates need for Gmail address lookups
4. **Cognito Integration**: Secure authentication with user ID migration to Cognito IDs
5. **No Access Token Storage**: Generates fresh access tokens as needed
6. **Scheduled Processing**: No GSI costs, efficient per-user processing
7. **User Isolation**: Complete separation of tokens and queues per user
8. **Simplified Routing**: Route targets directly specify Cognito user IDs
9. **Audit Trail**: Persistent message history for troubleshooting
10. **Scalable**: Single table design scales with user growth