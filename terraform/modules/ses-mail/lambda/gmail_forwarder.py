"""
Gmail Forwarder Lambda Function

This Lambda function is triggered by SQS messages from the gmail-forwarder queue.
It processes enriched email events from EventBridge and imports emails into Gmail.
"""

import base64
import json
import logging
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
patch_all()

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment configuration
GMAIL_TOKEN_PARAMETER = os.environ.get('GMAIL_TOKEN_PARAMETER', '/ses-mail/gmail-token')
EMAIL_BUCKET = os.environ.get('EMAIL_BUCKET')
S3_PREFIX = 'emails'  # Hardcoded to match ses.tf configuration
GMAIL_USER_ID = 'me'
DEFAULT_LABEL_IDS = ['INBOX', 'UNREAD']

# Initialize AWS clients
s3_client = boto3.client('s3')
ssm_client = boto3.client('ssm')


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
    logger.info(f"Received SQS event with {len(event.get('Records', []))} messages")

    results = []
    token_info = None
    service = None
    creds = None

    try:
        # Load Gmail token once for all records
        token_info = load_token_from_ssm()
        service, creds = build_gmail_service(token_info)

        # Process each SQS record
        records = event.get('Records', [])
        for record in records:
            result = process_sqs_record(record, service)
            results.append(result)

        # Update token if refreshed
        if creds and token_info:
            maybe_update_token(creds, token_info)

        return {
            'statusCode': 200,
            'batchItemFailures': [
                {'itemIdentifier': r['receiptHandle']}
                for r in results
                if r.get('status') == 'error'
            ]
        }

    except Exception as e:
        logger.error(f"Error processing SQS messages: {str(e)}", exc_info=True)
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
        # Parse SQS message body (enriched EventBridge message)
        body = json.loads(record.get('body', '{}'))

        # Extract enriched message data
        routing_decisions = body.get('routingDecisions', [])
        email_metadata = body.get('emailMetadata', {})

        message_id = email_metadata.get('messageId')
        source = email_metadata.get('source')
        subject = email_metadata.get('subject', '(no subject)')

        # Get the first routing decision (should be forward-to-gmail)
        if not routing_decisions:
            raise ValueError("No routing decisions found in enriched message")

        routing_decision = routing_decisions[0]
        action = routing_decision.get('action')
        target = routing_decision.get('target')
        recipient = routing_decision.get('recipient')

        # Add X-Ray annotations using subsegment to avoid "FacadeSegments cannot be mutated" error
        if subsegment:
            subsegment.put_annotation('action', action)
            subsegment.put_annotation('recipient', recipient)
            subsegment.put_annotation('target', target)

        # Validate action type
        if action != 'forward-to-gmail':
            raise ValueError(f"Unexpected action type: {action}")

        # Log email metadata
        logger.info(f"Processing email forward to Gmail:")
        logger.info(f"  Message ID: {message_id}")
        logger.info(f"  From: {source}")
        logger.info(f"  To: {recipient}")
        logger.info(f"  Subject: {subject}")
        logger.info(f"  Target Gmail: {target}")
        logger.info(f"  Security verdict: {email_metadata.get('securityVerdict', {})}")

        if not message_id:
            raise ValueError("Missing messageId in enriched message")

        # Fetch raw email from S3
        raw_eml = fetch_raw_email_from_s3(message_id)
        logger.info(f"  Fetched {len(raw_eml)} bytes from S3")

        # Import into Gmail with INBOX and UNREAD labels
        gmail_response = gmail_import(service, raw_eml, DEFAULT_LABEL_IDS)

        logger.info(f"  Successfully imported to Gmail: {gmail_response.get('id')}")

        # Add Gmail response details to X-Ray subsegment
        if subsegment:
            subsegment.put_annotation('gmail_message_id', gmail_response.get('id', 'unknown'))
            subsegment.put_annotation('gmail_thread_id', gmail_response.get('threadId', 'unknown'))
            subsegment.put_annotation('import_status', 'success')

        # Delete email from S3 after successful import
        delete_email_from_s3(message_id)
        logger.info(f"  Deleted email from S3")

        return {
            'messageId': message_id,
            'gmail_id': gmail_response.get('id'),
            'threadId': gmail_response.get('threadId'),
            'labelIds': gmail_response.get('labelIds'),
            'target': target,
            'status': 'ok',
            'receiptHandle': receipt_handle
        }

    except (RuntimeError, ValueError, HttpError, ClientError, json.JSONDecodeError) as e:
        logger.error(f"  Error processing SQS message: {str(e)}", exc_info=True)

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
        logger.error(f"Error retrieving Gmail token from SSM: {str(e)}")
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
        logger.error(f"Error saving token to SSM: {str(e)}")
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
        logger.error(f"Error building Gmail service: {str(e)}")
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
        logger.error(f"Error checking/updating token: {str(e)}")


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
        logger.info(f"Fetching from s3://{EMAIL_BUCKET}/{s3_key}")
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
        logger.info(f"Deleted s3://{EMAIL_BUCKET}/{s3_key}")
    except ClientError as e:
        # Log error but don't fail the whole operation
        # Email is already in Gmail, so S3 cleanup failure is not critical
        logger.error(f"Failed to delete email from S3: {e}")


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
    try:
        body = {
            'raw': base64.urlsafe_b64encode(raw_bytes).decode('utf-8'),
            'labelIds': label_ids or None
        }
        return service.users().messages().import_(
            userId=GMAIL_USER_ID,
            body=body
        ).execute()
    except HttpError as e:
        raise RuntimeError(f"Gmail API error: {e}")
