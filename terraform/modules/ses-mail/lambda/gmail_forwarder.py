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

# X-Ray SDK for distributed tracing
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from aws_xray_sdk.core.models import http
patch_all()

# Configure structured JSON logging
from aws_lambda_powertools import Logger
logger = Logger(service="ses-mail-gmail-forwarder")

# Environment configuration
GMAIL_TOKEN_PARAMETER = os.environ.get('GMAIL_TOKEN_PARAMETER', '/ses-mail/gmail-token')
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
    token_info = None
    service = None
    creds = None
    success_count = 0
    failure_count = 0

    try:
        # Load Gmail token once for all records
        token_info = load_token_from_ssm()
        service, creds = build_gmail_service(token_info)

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

        # Update token if refreshed
        if creds and token_info:
            maybe_update_token(creds, token_info)

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


def load_token_from_ssm() -> Dict[str, Any]:
    """
    Load the Gmail OAuth token from SSM Parameter Store.

    Returns:
        dict: Gmail OAuth token as dictionary
    """
    try:
        response = ssm_client.get_parameter(
            Name=GMAIL_TOKEN_PARAMETER,
            WithDecryption=True
        )
        return json.loads(response['Parameter']['Value'])
    except ClientError as e:
        logger.error("Error retrieving Gmail token from SSM", extra={"error": str(e)})
        raise RuntimeError(f"Failed to load token from SSM: {e}")


def save_token_to_ssm(token_dict: Dict[str, Any]) -> None:
    """
    Persist updated token back to SSM Parameter Store.

    Args:
        token_dict: Gmail OAuth token dictionary
    """
    try:
        ssm_client.put_parameter(
            Name=GMAIL_TOKEN_PARAMETER,
            Type='SecureString',
            Value=json.dumps(token_dict),
            Overwrite=True
        )
        logger.info("Updated Gmail token in SSM")
    except ClientError as e:
        logger.error("Error saving token to SSM", extra={"error": str(e)})
        raise


def build_gmail_service(token_info: Dict[str, Any]):
    """
    Create Gmail API service from token info.

    Args:
        token_info: Gmail OAuth token dictionary

    Returns:
        tuple: (gmail_service, credentials)
    """
    try:
        creds = Credentials.from_authorized_user_info(token_info)
        service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
        return service, creds
    except Exception as e:
        logger.error("Error building Gmail service", extra={"error": str(e)})
        raise


def maybe_update_token(creds: Credentials, original: Dict[str, Any]) -> None:
    """
    If access token or expiry changed, write back to SSM.

    Args:
        creds: Current credentials
        original: Original token dictionary
    """
    try:
        updated = json.loads(creds.to_json())
        if (updated.get('token') != original.get('token') or
            updated.get('expiry') != original.get('expiry')):
            save_token_to_ssm(updated)
    except Exception as e:
        logger.error("Error checking/updating token", extra={"error": str(e)})


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
