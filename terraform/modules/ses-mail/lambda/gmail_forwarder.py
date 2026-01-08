"""
Gmail Forwarder Lambda Function

This Lambda function is triggered by SQS messages from the gmail-forwarder queue.
It processes enriched email events from EventBridge and imports emails into Gmail.
"""

import base64
import json
import os
from http import HTTPStatus
from typing import Dict, Any, List

import boto3
from botocore.exceptions import ClientError

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

# X-Ray SDK for distributed tracing
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
patch_all()

# Configure structured JSON logging
from aws_lambda_powertools import Logger
logger = Logger(service="ses-mail-gmail-forwarder")

# Environment configuration
GMAIL_REFRESH_TOKEN_PARAMETER = os.environ.get('GMAIL_REFRESH_TOKEN_PARAMETER')
GMAIL_CLIENT_CREDENTIALS_PARAMETER = os.environ.get('GMAIL_CLIENT_CREDENTIALS_PARAMETER')
EMAIL_BUCKET = os.environ.get('EMAIL_BUCKET')
RETRY_QUEUE_URL = os.environ.get('RETRY_QUEUE_URL')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME')
CANARY_GMAIL_LABEL = os.environ.get('CANARY_GMAIL_LABEL')  # Required for canary: Gmail label name
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'unknown')
S3_PREFIX = 'emails'  # Hardcoded to match ses.tf configuration
GMAIL_USER_ID = 'me'
DEFAULT_LABEL_IDS = ['INBOX', 'UNREAD']

# X-Ray HTTP metadata keys (matching aws_xray_sdk.core.models.http)
XRAY_HTTP_URL = "url"
XRAY_HTTP_METHOD = "method"
XRAY_HTTP_STATUS = "status"

# Initialize AWS clients
s3_client = boto3.client('s3')
ssm_client = boto3.client('ssm')
sqs_client = boto3.client('sqs')
cloudwatch = boto3.client('cloudwatch')
dynamodb_client = boto3.client('dynamodb')


def extract_subject(ses_message: Dict[str, Any], max_length: int = 64) -> str:
    """
    Extract and safely truncate email subject from SES message headers.

    Args:
        ses_message: SES event message
        max_length: Maximum characters to return (default 64)

    Returns:
        str: Truncated subject or '(no subject)' if not found
    """
    headers = ses_message.get('mail', {}).get('headers', [])
    for header in headers:
        if header.get('name', '').lower() == 'subject':
            subject = header.get('value', '')
            if subject:
                # Truncate to max_length characters
                return subject[:max_length] if len(subject) > max_length else subject
    return '(no subject)'


def extract_canary_id(ses_message: Dict[str, Any]) -> str:
    """
    Extract canary ID from X-Canary-ID email header.

    Args:
        ses_message: SES event message

    Returns:
        str: Canary ID or empty string if not found
    """
    headers = ses_message.get('mail', {}).get('headers', [])
    for header in headers:
        if header.get('name', '').lower() == 'x-canary-id':
            return header.get('value', '')
    return ''


def write_canary_completion_record(canary_id: str, gmail_message_id: str, status: str = 'completed', error_message: str | None = None) -> None:
    """
    Write canary completion record to DynamoDB.

    Updates the existing tracking record with completion timestamp and Gmail message ID.

    Args:
        canary_id: Canary identifier
        gmail_message_id: Gmail message ID from import (or empty string if failed)
        status: 'completed' or 'failed'
        error_message: Error details if status is 'failed'
    """
    if not DYNAMODB_TABLE_NAME:
        logger.warning("DYNAMODB_TABLE_NAME not set, skipping canary completion write")
        return

    from datetime import datetime, timezone

    logger.info("Writing canary completion record",
        canary_id=canary_id,
        status=status,
        gmail_message_id=gmail_message_id,
        error=error_message
    )

    try:
        update_expression = 'SET #status = :status, completed_at = :completed_at'
        expression_values = {
            ':status': {'S': status},
            ':completed_at': {'S': datetime.now(timezone.utc).isoformat()}
        }

        # Add gmail_message_id only if we have one (successful import)
        if gmail_message_id:
            update_expression += ', gmail_message_id = :gmail_message_id'
            expression_values[':gmail_message_id'] = {'S': gmail_message_id}

        # Add error_message if we have one (failed status)
        if error_message:
            update_expression += ', error_message = :error_message'
            expression_values[':error_message'] = {'S': error_message}

        dynamodb_client.update_item(
            TableName=DYNAMODB_TABLE_NAME,
            Key={
                'PK': {'S': f'CANARY#{canary_id}'},
                'SK': {'S': 'TRACKING#v1'}
            },
            UpdateExpression=update_expression,
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues=expression_values
        )
        logger.info("Successfully wrote canary completion record",
            canary_id=canary_id,
            status=status
        )
    except Exception as e:
        logger.error("Failed to write canary completion record",
            canary_id=canary_id,
            error=str(e)
        )
        # Don't raise - canary tracking is best-effort


def lambda_handler(event, context):
    """
    Lambda handler for processing enriched email messages from SQS.
    Fetches emails from S3 and imports them into Gmail.

    Args:
        event: SQS event containing enriched email messages
        context: Lambda context object

    Returns:
        dict: Response with results for each processed message
    """
    _ = context  # Unused but required by Lambda handler signature
    logger.info("Received SQS event", extra={"messageCount": len(event.get('Records', []))})

    results = []
    service = None
    success_count = 0
    failure_count = 0

    try:
        # Build Gmail service with fresh access token
        service = build_gmail_service()

        # Process each SQS record
        records = event.get('Records', [])
        for record in records:
            result = process_sqs_record(record, service)
            results.append(result)

            # Track success/failure counts
            if result.get('status') == 'ok':
                success_count += 1
            else:
                failure_count += 1

        # Publish custom metrics
        publish_metrics(success_count, failure_count)

        logger.info("Processed messages", extra={
            "totalCount": len(results),
            "successCount": success_count,
            "failureCount": failure_count
        })

        return {
            'statusCode': HTTPStatus.OK,
            'batchItemFailures': [
                {'itemIdentifier': r['receiptHandle']}
                for r in results
                if r.get('status') == 'error'
            ]
        }

    except Exception as e:
        # Check if this is a token expiration error during service creation
        if is_token_expired_error(e):
            logger.warning("Token expired during service creation - queueing all records for retry", extra={
                "error": str(e),
                "recordCount": len(event.get('Records', []))
            })

            # Queue all SQS records for retry
            from datetime import datetime, timezone
            error_context = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'error_type': 'token_expired',
                'request_id': context.aws_request_id if context else 'unknown',
                'attempt_count': 1
            }

            batch_item_failures = []
            records = event.get('Records', [])
            for record in records:
                receipt_handle = record.get('receiptHandle')
                try:
                    queue_for_retry(record, error_context)
                    # Successfully queued - do NOT add to batch failures
                    # This lets SQS delete the message from the original queue
                    logger.info("Successfully queued message for retry - allowing SQS to delete from source queue", extra={
                        "receiptHandle": receipt_handle
                    })
                except Exception as queue_error:
                    logger.error("Failed to queue message for retry - adding to batch failures for SQS retry", extra={
                        "receiptHandle": receipt_handle,
                        "queueError": str(queue_error)
                    })
                    # Only add to failures if we FAILED to queue for retry
                    # This will cause SQS to retry, which will attempt to queue again
                    batch_item_failures.append({'itemIdentifier': receipt_handle})

            return {
                'statusCode': HTTPStatus.OK,
                'batchItemFailures': batch_item_failures
            }

        # Not a token expiration error - log and re-raise
        logger.exception("Error processing SQS messages", extra={"error": str(e)})
        raise


def is_token_expired_error(exception: Exception) -> bool:
    """
    Detect if an exception is caused by OAuth token expiration.

    Checks for various error types and messages that indicate the Gmail OAuth
    token has expired or is invalid:
    - HTTP 401 (Unauthorized) or 403 (Forbidden) errors
    - RefreshError from Google Auth library
    - Error messages containing token expiration keywords

    Args:
        exception: The exception to check

    Returns:
        bool: True if the error is a token expiration error, False otherwise
    """
    # Check for RefreshError from Google Auth library
    if isinstance(exception, RefreshError):
        logger.info("Detected RefreshError - token has expired")
        return True

    # Check for HTTP errors with 401/403 status codes
    if isinstance(exception, HttpError):
        status_code = exception.resp.status
        if status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            logger.info("Detected HTTP 401/403 error - token may be expired", extra={
                "status_code": status_code,
                "error": str(exception)
            })
            return True

    # Check error message for token expiration keywords
    error_message = str(exception).lower()
    expiration_keywords = [
        'invalid_grant',
        'token has been expired',
        'token expired',
        'invalid credentials',
        'credentials have expired',
        'unauthorized',
        'authentication failed'
    ]

    for keyword in expiration_keywords:
        if keyword in error_message:
            logger.info("Detected token expiration keyword in error message", extra={
                "keyword": keyword,
                "error": str(exception)
            })
            return True

    return False


def queue_for_retry(sqs_record: Dict[str, Any], error_context: Dict[str, Any]) -> None:
    """
    Queue a failed SQS record to the retry queue for later processing.

    This function is called when a token expiration error is detected.
    It stores the original SQS message body in SQS retry queue with metadata.

    Args:
        sqs_record: The original SQS record from the Lambda event
        error_context: Additional error information (error_type, timestamp, etc.)

    Raises:
        RuntimeError: If queueing fails
    """
    if not RETRY_QUEUE_URL:
        raise RuntimeError("RETRY_QUEUE_URL environment variable must be set")

    message_id = None

    try:
        # Parse the SQS message body to extract message ID for logging
        body = json.loads(sqs_record.get('body', '{}'))
        detail = body.get('detail', body)
        message_id = detail.get('originalMessageId', 'unknown')

        # Prepare SQS message with original SQS message body
        message_body = sqs_record.get('body', '{}')

        # Add message attributes for filtering and monitoring
        message_attributes = {
            'original_timestamp': {
                'StringValue': error_context.get('timestamp', ''),
                'DataType': 'String'
            },
            'error_type': {
                'StringValue': error_context.get('error_type', 'token_expired'),
                'DataType': 'String'
            },
            'original_lambda_request_id': {
                'StringValue': error_context.get('request_id', ''),
                'DataType': 'String'
            },
            'attempt_count': {
                'StringValue': str(error_context.get('attempt_count', 1)),
                'DataType': 'Number'
            }
        }

        # Send message to retry queue
        response = sqs_client.send_message(
            QueueUrl=RETRY_QUEUE_URL,
            MessageBody=message_body,
            MessageAttributes=message_attributes
        )

        logger.info("Queued message for retry", extra={
            "messageId": message_id,
            "sqsMessageId": response.get('MessageId'),
            "errorType": error_context.get('error_type'),
            "attemptCount": error_context.get('attempt_count', 1)
        })

    except ClientError as e:
        logger.error("Failed to queue message for retry", extra={
            "messageId": message_id if message_id is not None else 'unknown',
            "error": str(e),
            "errorCode": e.response.get('Error', {}).get('Code')
        })
        raise RuntimeError(f"Failed to queue message for retry: {e}")


def process_sqs_record(record, service):
    """
    Process a single SQS record containing an enriched email message.

    Args:
        record: SQS record from the event
        service: Authenticated Gmail API service

    Returns:
        dict: Result with messageId, gmail_id, and status
    """
    receipt_handle = record.get('receiptHandle')

    # Create X-Ray subsegment at the beginning so it's accessible in except block
    subsegment = xray_recorder.begin_subsegment('process_gmail_forward')  # type: ignore[attr-defined]

    # Initialize variables for error logging
    message_id = None
    source = None
    ses_message = None
    recipient = None
    destination = None
    subject = None

    try:

        # Parse SQS message body (enriched EventBridge message from EventBridge Event Bus)
        # The message is the EventBridge event detail
        body = json.loads(record.get('body', '{}'))

        # EventBridge wraps the router output in 'detail'
        detail = body.get('detail', body)  # Fallback to body if not wrapped

        # Extract message ID from detail
        message_id = detail.get('originalMessageId')

        # Extract actions and targets from new router structure
        actions = detail.get('actions', {})
        forward_to_gmail = actions.get('forward-to-gmail', {})
        targets = forward_to_gmail.get('targets', [])

        if not targets:
            raise ValueError("No forward-to-gmail targets found in enriched message")

        logger.info("Decision Info", extra=detail)
        ses_message = detail.get('ses')

        # Extract SES mail metadata
        ses_mail = ses_message.get('mail', {})
        source = ses_mail.get('source', 'unknown@unknown.com')
        subject = ses_mail.get('commonHeaders', {}).get('subject', '(no subject)')

        if not message_id:
            raise ValueError("Missing originalMessageId in enriched message")

        # Fetch raw email from S3 once before processing targets
        raw_eml = fetch_raw_email_from_s3(message_id)
        logger.info("Fetched email from S3", extra={
            "messageId": message_id,
            "byteCount": len(raw_eml)
        })

        # Process each target (typically one, but could be multiple)
        results = []
        for target_info in targets:
            recipient = target_info.get('target')  # Original recipient email
            destination = target_info.get('destination')  # Gmail destination address
            metadata = target_info.get('metadata', {})  # Routing metadata

            # Check if this is a canary email
            is_canary = metadata.get('canary', False)
            canary_id = extract_canary_id(ses_message) if is_canary else None

            # Add X-Ray annotations for searchability and correlation
            if subsegment:
                subsegment.put_annotation('messageId', message_id)
                subsegment.put_annotation('source', source)
                subsegment.put_annotation('action', 'forward-to-gmail')
                subsegment.put_annotation('recipient', recipient)
                subsegment.put_annotation('target', destination)
                subsegment.put_annotation('subject', subject[0:64])
                if is_canary:
                    subsegment.put_annotation('canary', True)
                    if canary_id:
                        subsegment.put_annotation('canary_id', canary_id)

            # Log email metadata
            logger.info("Processing email forward to Gmail",
                messageId=message_id,
                sender=source,
                recipient=recipient,
                subject=subject,
                targetGmail=destination,
                is_canary=is_canary,
                canary_id=canary_id if is_canary else None
            )

            # For canary emails, wrap everything in try/catch to detect failures
            canary_success = False
            canary_error = None
            gmail_response = None

            try:
                # Build label IDs
                label_ids = list(DEFAULT_LABEL_IDS)  # Start with ['INBOX', 'UNREAD']

                if is_canary:
                    # For canary emails, use label name from environment variable
                    # Gmail label ID is just the label name - pass it directly
                    if not CANARY_GMAIL_LABEL:
                        # Configuration error - canary label not set!
                        raise ValueError("CANARY_GMAIL_LABEL environment variable must be set for canary emails")

                    # Remove INBOX/UNREAD, use only the canary label
                    label_ids = [CANARY_GMAIL_LABEL]
                    logger.info("Canary email - using label from environment",
                        canary_id=canary_id,
                        label=CANARY_GMAIL_LABEL
                    )

                # Import into Gmail with appropriate labels
                gmail_response = gmail_import(service, raw_eml, label_ids)

                # If we got here without exception, canary succeeded
                if is_canary:
                    canary_success = True

                logger.info("Successfully imported to Gmail",
                    messageId=message_id,
                    gmailId=gmail_response.get('id'),
                    is_canary=is_canary,
                    labels=label_ids
                )

            except Exception as e:
                # Log the error
                logger.error("Failed to process email",
                    messageId=message_id,
                    recipient=recipient,
                    is_canary=is_canary,
                    error=str(e),
                    error_type=type(e).__name__
                )

                if is_canary and canary_id:
                    # Record canary failure
                    canary_error = f"{type(e).__name__}: {str(e)}"

                # Re-raise to trigger SQS retry
                raise

            finally:
                # Write canary completion record if this is a canary email
                if is_canary and canary_id:
                    if canary_success:
                        gmail_message_id = (gmail_response.get('id') if gmail_response else None) or ''
                        write_canary_completion_record(
                            canary_id,
                            gmail_message_id,
                            status='completed'
                        )
                    elif canary_error:
                        write_canary_completion_record(
                            canary_id,
                            '',
                            status='failed',
                            error_message=canary_error
                        )

            # Log action result for dashboard
            logger.info("Action result", extra={
                "messageId": message_id,
                "sender": source,
                "subject": extract_subject(ses_message, max_length=64),
                "recipient": recipient,
                "action": "forward-to-gmail",
                "result": "success",
                "target": destination,  # Gmail destination address
                "resultId": gmail_response.get('id')  # Gmail message ID
            })

            results.append({
                'recipient': recipient,
                'destination': destination,
                'gmail_id': gmail_response.get('id'),
                'threadId': gmail_response.get('threadId'),
                'labelIds': gmail_response.get('labelIds')
            })

        # Note: We intentionally do NOT delete the email from S3 here.
        # Multiple actions may need to process the same email (e.g., store + forward-to-gmail),
        # and some actions may be delayed (OAuth token refresh delays).
        # The S3 bucket lifecycle policy will automatically delete emails after 90 days.
        # See: terraform/modules/ses-mail/s3.tf lifecycle configuration

        return {
            'messageId': message_id,
            'results': results,
            'status': 'ok',
            'receiptHandle': receipt_handle
        }

    except (RuntimeError, ValueError, HttpError, ClientError, json.JSONDecodeError, RefreshError) as e:
        # Check if this is a token expiration error
        if is_token_expired_error(e):
            logger.warning("Token expired while processing message - queueing for retry", extra={
                "error": str(e)
            })

            # Queue the message for retry with error context
            from datetime import datetime, timezone
            error_context = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'error_type': 'token_expired',
                'request_id': 'unknown',  # Not available at this level
                'attempt_count': 1
            }

            try:
                queue_for_retry(record, error_context)

                # Add success details to X-Ray subsegment
                if subsegment:
                    subsegment.put_annotation('import_status', 'queued_for_retry')
                    subsegment.put_annotation('error_type', 'token_expired')

                # Successfully queued - return 'ok' status so SQS deletes from original queue
                # Do NOT return 'error' status as that adds to batchItemFailures and causes SQS to retry
                return {
                    'status': 'ok',
                    'action': 'queued_for_retry',
                    'reason': 'Token expired - successfully queued for retry after token refresh',
                    'receiptHandle': receipt_handle
                }
            except Exception as queue_error:
                logger.error("Failed to queue message for retry - will be added to batch failures", extra={
                    "originalError": str(e),
                    "queueError": str(queue_error)
                })

                # Add error details to X-Ray subsegment
                if subsegment:
                    subsegment.put_annotation('import_status', 'error')
                    subsegment.put_annotation('error_type', 'failed_to_queue')

                # Only return error if we FAILED to queue for retry
                # This will be added to batchItemFailures, causing SQS to retry
                return {
                    'error': f"Token expired and failed to queue: {queue_error}",
                    'status': 'error',
                    'receiptHandle': receipt_handle
                }

        # Not a token expiration error - log and return error
        logger.exception("Error processing SQS message", extra={"error": str(e)})

        # Log action result for dashboard (if we have enough context)
        try:
            logger.error("Action result", extra={
                "messageId": message_id if message_id is not None else 'unknown',
                "sender": source if source is not None else 'unknown',
                "subject": extract_subject(ses_message, max_length=64) if ses_message is not None else '(no subject)',
                "recipient": recipient if recipient is not None else 'unknown',
                "action": "forward-to-gmail",
                "result": "failure",
                "target": destination if destination is not None else 'unknown',  # Gmail destination
                "error": str(e)
            })
        except Exception:
            # If logging fails, don't let it crash the error handling
            pass

        # Add error details to X-Ray subsegment
        if subsegment:
            subsegment.put_annotation('import_status', 'error')
            subsegment.put_annotation('error_type', type(e).__name__)

        return {
            'error': str(e),
            'status': 'error',
            'receiptHandle': receipt_handle
        }
    finally:
        # Always end the subsegment
        xray_recorder.end_subsegment()


def load_refresh_token_from_ssm() -> str:
    """
    Load the Gmail OAuth refresh token from SSM Parameter Store.

    Returns:
        str: Refresh token string
    """
    if not GMAIL_REFRESH_TOKEN_PARAMETER:
        raise RuntimeError("GMAIL_REFRESH_TOKEN_PARAMETER environment variable must be set")

    try:
        response = ssm_client.get_parameter(
            Name=GMAIL_REFRESH_TOKEN_PARAMETER,
            WithDecryption=True
        )
        token_data = json.loads(response['Parameter']['Value'])
        return token_data['token']
    except ClientError as e:
        logger.error("Error retrieving refresh token from SSM", extra={"error": str(e)})
        raise RuntimeError(f"Failed to load refresh token from SSM: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        logger.error("Invalid refresh token format in SSM", extra={"error": str(e)})
        raise RuntimeError(f"Invalid refresh token format: {e}")


def load_client_credentials_from_ssm() -> Dict[str, str]:
    """
    Load OAuth client credentials from SSM Parameter Store.

    Returns:
        dict: Dictionary with client_id, client_secret, and token_uri
    """
    if not GMAIL_CLIENT_CREDENTIALS_PARAMETER:
        raise RuntimeError("GMAIL_CLIENT_CREDENTIALS_PARAMETER environment variable must be set")

    try:
        response = ssm_client.get_parameter(
            Name=GMAIL_CLIENT_CREDENTIALS_PARAMETER,
            WithDecryption=True
        )
        credentials_json = response['Parameter']['Value']
        credentials_data = json.loads(credentials_json)

        # Handle Google's OAuth JSON format (may have 'installed' or 'web' wrapper)
        if 'installed' in credentials_data:
            creds = credentials_data['installed']
        elif 'web' in credentials_data:
            creds = credentials_data['web']
        else:
            raise ValueError("OAuth credentials must contain 'installed' or 'web' key")

        return {
            'client_id': creds['client_id'],
            'client_secret': creds['client_secret'],
            'token_uri': creds['token_uri']
        }
    except ClientError as e:
        logger.error("Error retrieving client credentials from SSM", extra={"error": str(e)})
        raise RuntimeError(f"Failed to load client credentials from SSM: {e}")
    except (KeyError, json.JSONDecodeError, ValueError) as e:
        logger.error("Invalid client credentials format in SSM", extra={"error": str(e)})
        raise RuntimeError(f"Invalid client credentials format: {e}")


def generate_access_token() -> Credentials:
    """
    Generate a fresh access token from the refresh token stored in SSM.

    This function retrieves the refresh token and client credentials from SSM,
    then uses them to generate a new access token via Google's OAuth API.
    The refresh token is never modified - we simply use it to obtain a new
    short-lived access token for this session.

    Returns:
        Credentials: Google OAuth credentials with fresh access token

    Raises:
        RuntimeError: If token generation fails
    """
    try:
        # Load refresh token and client credentials from SSM
        refresh_token = load_refresh_token_from_ssm()
        client_creds = load_client_credentials_from_ssm()

        logger.info("Generating fresh access token from refresh token")

        # Create credentials object with refresh token and client info
        creds = Credentials(
            token=None,  # No access token yet
            refresh_token=refresh_token,
            token_uri=client_creds['token_uri'],
            client_id=client_creds['client_id'],
            client_secret=client_creds['client_secret']
        )

        # Refresh to obtain new access token
        creds.refresh(Request())

        logger.info("Successfully generated fresh access token", extra={
            "token_expiry": creds.expiry.isoformat() if creds.expiry else None
        })

        return creds

    except Exception as e:
        logger.error("Failed to generate access token", extra={"error": str(e)})
        raise RuntimeError(f"Failed to generate access token: {e}")


def build_gmail_service():
    """
    Create Gmail API service with a fresh access token.

    Generates a new access token from the refresh token and builds the Gmail API service.
    Each invocation creates a fresh access token, ensuring we always have valid credentials.

    Returns:
        Gmail API service object

    Raises:
        RuntimeError: If service creation fails
    """
    try:
        # Generate fresh access token
        creds = generate_access_token()

        # Build Gmail service with the fresh credentials
        service = build('gmail', 'v1', credentials=creds, cache_discovery=False)

        logger.info("Gmail service built successfully")
        return service

    except Exception as e:
        logger.error("Error building Gmail service", extra={"error": str(e)})
        raise RuntimeError(f"Failed to build Gmail service: {e}")


def fetch_raw_email_from_s3(message_id: str) -> bytes:
    """
    Download the raw email bytes from S3 for the given SES messageId.

    Args:
        message_id: SES message ID

    Returns:
        bytes: Raw email content in MIME format
    """
    if not EMAIL_BUCKET:
        raise RuntimeError("EMAIL_BUCKET environment variable must be set")

    # Construct S3 key: emails/{messageId}
    s3_key = f"{S3_PREFIX}/{message_id}"

    try:
        logger.info("Fetching email from S3", extra={
            "bucket": EMAIL_BUCKET,
            "key": s3_key
        })
        obj = s3_client.get_object(Bucket=EMAIL_BUCKET, Key=s3_key)
        return obj['Body'].read()
    except ClientError as e:
        raise RuntimeError(f"Failed to fetch email from S3: {e}")


def gmail_import(service, raw_bytes: bytes, label_ids: List[str]) -> Dict[str, Any]:
    """
    Import raw MIME email into Gmail and apply labels.

    Args:
        service: Gmail API service
        raw_bytes: Raw email content in MIME format
        label_ids: List of label IDs to apply (e.g., ['INBOX', 'UNREAD'])

    Returns:
        dict: Gmail API response with id, threadId, labelIds
    """
    # Create X-Ray subsegment for Gmail API HTTP call
    subsegment = xray_recorder.begin_subsegment('gmail.googleapis.com')

    try:
        # Mark as external HTTP service
        if subsegment:
            subsegment.namespace = 'remote'

        # Prepare request body
        encoded_email = base64.urlsafe_b64encode(raw_bytes).decode('utf-8')
        body = {
            'raw': encoded_email,
            'labelIds': label_ids or None
        }

        # Set HTTP request metadata
        if subsegment:
            api_url = f'https://gmail.googleapis.com/gmail/v1/users/{GMAIL_USER_ID}/messages/import'
            subsegment.put_http_meta(XRAY_HTTP_URL, api_url)
            subsegment.put_http_meta(XRAY_HTTP_METHOD, 'POST')

            # Add annotations for tracing
            subsegment.put_annotation('email_size_bytes', len(raw_bytes))
            subsegment.put_annotation('email_size_base64', len(encoded_email))
            subsegment.put_annotation('label_count', len(label_ids) if label_ids else 0)

        # Execute Gmail API call
        response = service.users().messages().import_(
            userId=GMAIL_USER_ID,
            body=body,
            internalDateSource='receivedTime',
        ).execute()

        # Set HTTP response metadata
        if subsegment:
            subsegment.put_http_meta(XRAY_HTTP_STATUS, HTTPStatus.OK)

            # Add response annotations
            subsegment.put_annotation('gmail_message_id', response.get('id', 'unknown'))
            subsegment.put_annotation('gmail_thread_id', response.get('threadId', 'unknown'))

        return response

    except HttpError as e:
        # Capture HTTP error details
        if subsegment:
            status_code = e.resp.status if hasattr(e, 'resp') else HTTPStatus.INTERNAL_SERVER_ERROR
            subsegment.put_http_meta(XRAY_HTTP_STATUS, status_code)
            subsegment.put_annotation('error', True)
            subsegment.put_annotation('error_message', str(e))
        raise RuntimeError(f"Gmail API error: {e}")
    finally:
        # Always end the subsegment
        xray_recorder.end_subsegment()


def get_message_details(service, message_id: str) -> Dict[str, Any]:
    """
    Get message details from Gmail to verify label assignment.

    Args:
        service: Gmail API service
        message_id: Gmail message ID

    Returns:
        dict: Gmail API response with id, labelIds, threadId
    """
    # Create X-Ray subsegment for Gmail API HTTP call
    subsegment = xray_recorder.begin_subsegment('gmail.googleapis.com/get')

    try:
        # Mark as external HTTP service
        if subsegment:
            subsegment.namespace = 'remote'

            # Set HTTP request metadata
            api_url = f'https://gmail.googleapis.com/gmail/v1/users/{GMAIL_USER_ID}/messages/{message_id}'
            subsegment.put_http_meta(XRAY_HTTP_URL, api_url)
            subsegment.put_http_meta(XRAY_HTTP_METHOD, 'GET')

            # Add annotations for tracing
            subsegment.put_annotation('gmail_message_id', message_id)

        # Execute Gmail API call
        response = service.users().messages().get(
            userId=GMAIL_USER_ID,
            id=message_id,
            format='minimal'
        ).execute()

        # Set HTTP response metadata
        if subsegment:
            subsegment.put_http_meta(XRAY_HTTP_STATUS, HTTPStatus.OK)

            # Add response annotations
            label_count = len(response.get('labelIds', []))
            subsegment.put_annotation('label_count', label_count)

        return response

    except HttpError as e:
        # Capture HTTP error details
        if subsegment:
            status_code = e.resp.status if hasattr(e, 'resp') else HTTPStatus.INTERNAL_SERVER_ERROR
            subsegment.put_http_meta(XRAY_HTTP_STATUS, status_code)
            subsegment.put_annotation('error', True)
            subsegment.put_annotation('error_message', str(e))
        raise RuntimeError(f"Gmail API get error: {e}")
    finally:
        # Always end the subsegment
        xray_recorder.end_subsegment()


def publish_metrics(success_count: int, failure_count: int) -> None:
    """
    Publish custom CloudWatch metrics for Gmail forwarding success/failure rates.

    Args:
        success_count: Number of successfully forwarded emails
        failure_count: Number of failed forwards
    """
    try:
        metric_data = []

        if success_count > 0:
            metric_data.append({
                'MetricName': 'GmailForwardSuccess',
                'Value': success_count,
                'Unit': 'Count',
                'StorageResolution': 60
            })

        if failure_count > 0:
            metric_data.append({
                'MetricName': 'GmailForwardFailure',
                'Value': failure_count,
                'Unit': 'Count',
                'StorageResolution': 60
            })

        if metric_data:
            cloudwatch.put_metric_data(
                Namespace=f'SESMail/{ENVIRONMENT}',
                MetricData=metric_data
            )
            logger.info("Published metrics", extra={
                "successCount": success_count,
                "failureCount": failure_count
            })

    except Exception as e:
        # Don't fail the lambda if metrics publishing fails
        logger.exception("Error publishing metrics", extra={"error": str(e)})
