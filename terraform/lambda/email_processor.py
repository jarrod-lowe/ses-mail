"""
SES Email Processor Lambda Function

This Lambda function is triggered when SES receives an email.
It processes the email from S3 and inserts it into Gmail via the API.
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
    Lambda handler for processing SES emails.
    Fetches emails from S3 and imports them into Gmail.

    Args:
        event: SES event containing email metadata
        context: Lambda context object

    Returns:
        dict: Response with results for each processed email
    """
    logger.info(f"Received SES event: {json.dumps(event)}")

    results = []
    token_info = None
    service = None
    creds = None

    try:
        # Load Gmail token once for all records
        token_info = load_token_from_ssm()
        service, creds = build_gmail_service(token_info)

        # Process each SES record
        records = event.get('Records', [])
        for record in records:
            if record.get('eventSource') == 'aws:ses':
                result = process_ses_record(record, service)
                results.append(result)

        # Update token if refreshed
        if creds and token_info:
            maybe_update_token(creds, token_info)

        return {'results': results}

    except Exception as e:
        logger.error(f"Error processing emails: {str(e)}", exc_info=True)
        raise


def process_ses_record(record, service):
    """
    Process a single SES record: fetch from S3 and import to Gmail.

    Args:
        record: SES record from the event
        service: Authenticated Gmail API service

    Returns:
        dict: Result with messageId, gmail_id, and status
    """
    ses = record.get('ses', {})
    mail = ses.get('mail', {})
    receipt = ses.get('receipt', {})
    message_id = mail.get('messageId')

    try:
        # Log email metadata
        logger.info(f"Processing email:")
        logger.info(f"  Message ID: {message_id}")
        logger.info(f"  From: {mail.get('source')}")
        logger.info(f"  To: {mail.get('destination')}")
        logger.info(f"  Subject: {mail.get('commonHeaders', {}).get('subject')}")
        logger.info(f"  Timestamp: {mail.get('timestamp')}")
        logger.info(f"  Spam verdict: {receipt.get('spamVerdict', {}).get('status')}")
        logger.info(f"  Virus verdict: {receipt.get('virusVerdict', {}).get('status')}")
        logger.info(f"  DKIM verdict: {receipt.get('dkimVerdict', {}).get('status')}")
        logger.info(f"  SPF verdict: {receipt.get('spfVerdict', {}).get('status')}")

        if not message_id:
            raise ValueError("Missing SES mail.messageId")

        # Fetch raw email from S3
        raw_eml = fetch_raw_email_from_s3(message_id)
        logger.info(f"  Fetched {len(raw_eml)} bytes from S3")

        # Import into Gmail with INBOX and UNREAD labels
        gmail_response = gmail_import(service, raw_eml, DEFAULT_LABEL_IDS)

        logger.info(f"  Successfully imported to Gmail: {gmail_response.get('id')}")

        return {
            'messageId': message_id,
            'gmail_id': gmail_response.get('id'),
            'threadId': gmail_response.get('threadId'),
            'labelIds': gmail_response.get('labelIds'),
            'status': 'ok'
        }

    except (RuntimeError, ValueError, HttpError, ClientError) as e:
        logger.error(f"  Error processing message {message_id}: {str(e)}")
        return {
            'messageId': message_id,
            'error': str(e),
            'status': 'error'
        }


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
