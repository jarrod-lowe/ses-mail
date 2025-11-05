"""
Gmail Forwarder Lambda Function

This Lambda function is triggered by SQS messages from the gmail-forwarder queue.
It processes enriched email events from EventBridge and imports emails into Gmail.
"""

import base64
import json
import os
from typing import Dict, Any, List

import boto3
from botocore.exceptions import ClientError

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# X-Ray SDK for distributed tracing
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from aws_xray_sdk.core.models import http
patch_all()

# Configure structured JSON logging
from aws_lambda_powertools import Logger
logger = Logger(service="ses-mail-gmail-forwarder")

# Environment configuration
GMAIL_REFRESH_TOKEN_PARAMETER = os.environ.get('GMAIL_REFRESH_TOKEN_PARAMETER')
GMAIL_CLIENT_CREDENTIALS_PARAMETER = os.environ.get('GMAIL_CLIENT_CREDENTIALS_PARAMETER')
EMAIL_BUCKET = os.environ.get('EMAIL_BUCKET')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'unknown')
S3_PREFIX = 'emails'  # Hardcoded to match ses.tf configuration
GMAIL_USER_ID = 'me'
DEFAULT_LABEL_IDS = ['INBOX', 'UNREAD']

# Initialize AWS clients
s3_client = boto3.client('s3')
ssm_client = boto3.client('ssm')
cloudwatch = boto3.client('cloudwatch')


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
            'statusCode': 200,
            'batchItemFailures': [
                {'itemIdentifier': r['receiptHandle']}
                for r in results
                if r.get('status') == 'error'
            ]
        }

    except Exception as e:
        logger.exception("Error processing SQS messages", extra={"error": str(e)})
        raise


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

            # Add X-Ray annotations for searchability and correlation
            if subsegment:
                subsegment.put_annotation('messageId', message_id)
                subsegment.put_annotation('source', source)
                subsegment.put_annotation('action', 'forward-to-gmail')
                subsegment.put_annotation('recipient', recipient)
                subsegment.put_annotation('target', destination)
                subsegment.put_annotation('subject', subject[0:64])

            # Log email metadata
            logger.info("Processing email forward to Gmail", extra={
                "messageId": message_id,
                "from": source,
                "to": recipient,
                "subject": subject,
                "targetGmail": destination
            })

            # Import into Gmail with INBOX and UNREAD labels
            gmail_response = gmail_import(service, raw_eml, DEFAULT_LABEL_IDS)

            logger.info("Successfully imported to Gmail", extra={
                "messageId": message_id,
                "gmailId": gmail_response.get('id')
            })

            results.append({
                'recipient': recipient,
                'destination': destination,
                'gmail_id': gmail_response.get('id'),
                'threadId': gmail_response.get('threadId'),
                'labelIds': gmail_response.get('labelIds')
            })

        # Delete email from S3 after successful import of all targets
        delete_email_from_s3(message_id)
        logger.info("Deleted email from S3", extra={"messageId": message_id})

        return {
            'messageId': message_id,
            'results': results,
            'status': 'ok',
            'receiptHandle': receipt_handle
        }

    except (RuntimeError, ValueError, HttpError, ClientError, json.JSONDecodeError) as e:
        logger.exception("Error processing SQS message", extra={"error": str(e)})

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


def delete_email_from_s3(message_id: str) -> None:
    """
    Delete the email from S3 after successful import to Gmail.

    Args:
        message_id: SES message ID
    """
    if not EMAIL_BUCKET:
        raise RuntimeError("EMAIL_BUCKET environment variable must be set")

    # Construct S3 key: emails/{messageId}
    s3_key = f"{S3_PREFIX}/{message_id}"

    try:
        s3_client.delete_object(Bucket=EMAIL_BUCKET, Key=s3_key)
        logger.info("Deleted email from S3", extra={
            "bucket": EMAIL_BUCKET,
            "key": s3_key
        })
    except ClientError as e:
        # Log error but don't fail the whole operation
        # Email is already in Gmail, so S3 cleanup failure is not critical
        logger.error("Failed to delete email from S3", extra={
            "bucket": EMAIL_BUCKET,
            "key": s3_key,
            "error": str(e)
        })


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
        subsegment.namespace = 'remote'

        # Prepare request body
        encoded_email = base64.urlsafe_b64encode(raw_bytes).decode('utf-8')
        body = {
            'raw': encoded_email,
            'labelIds': label_ids or None
        }

        # Set HTTP request metadata
        api_url = f'https://gmail.googleapis.com/gmail/v1/users/{GMAIL_USER_ID}/messages/import'
        subsegment.put_http_meta(http.URL, api_url)
        subsegment.put_http_meta(http.METHOD, 'POST')

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
        subsegment.put_http_meta(http.STATUS, 200)

        # Add response annotations
        subsegment.put_annotation('gmail_message_id', response.get('id', 'unknown'))
        subsegment.put_annotation('gmail_thread_id', response.get('threadId', 'unknown'))

        return response

    except HttpError as e:
        # Capture HTTP error details
        status_code = e.resp.status if hasattr(e, 'resp') else 500
        subsegment.put_http_meta(http.STATUS, status_code)
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
        subsegment.namespace = 'remote'

        # Set HTTP request metadata
        api_url = f'https://gmail.googleapis.com/gmail/v1/users/{GMAIL_USER_ID}/messages/{message_id}'
        subsegment.put_http_meta(http.URL, api_url)
        subsegment.put_http_meta(http.METHOD, 'GET')

        # Add annotations for tracing
        subsegment.put_annotation('gmail_message_id', message_id)

        # Execute Gmail API call
        response = service.users().messages().get(
            userId=GMAIL_USER_ID,
            id=message_id,
            format='minimal'
        ).execute()

        # Set HTTP response metadata
        subsegment.put_http_meta(http.STATUS, 200)

        # Add response annotations
        label_count = len(response.get('labelIds', []))
        subsegment.put_annotation('label_count', label_count)

        return response

    except HttpError as e:
        # Capture HTTP error details
        status_code = e.resp.status if hasattr(e, 'resp') else 500
        subsegment.put_http_meta(http.STATUS, status_code)
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
