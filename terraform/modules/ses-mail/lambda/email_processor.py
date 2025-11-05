"""
SES Email Processor Lambda Function

This Lambda function is triggered when SES receives an email.
It processes the email from S3 and inserts it into Gmail via the API.
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
from aws_lambda_powertools import Logger

# Configure structured JSON logging
logger = Logger(service="ses-mail-email-processor")

# Environment configuration
GMAIL_REFRESH_TOKEN_PARAMETER = os.environ.get('GMAIL_REFRESH_TOKEN_PARAMETER')
GMAIL_CLIENT_CREDENTIALS_PARAMETER = os.environ.get('GMAIL_CLIENT_CREDENTIALS_PARAMETER')
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
    logger.info("Received SES event", extra={"event": event})

    results = []
    service = None

    try:
        # Build Gmail service with fresh access token
        service = build_gmail_service()

        # Process each SES record
        records = event.get('Records', [])
        for record in records:
            if record.get('eventSource') == 'aws:ses':
                result = process_ses_record(record, service)
                results.append(result)

        return {'results': results}

    except Exception as e:
        logger.exception("Error processing emails", extra={"error": str(e)})
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
        logger.info("Processing email", extra={
            "messageId": message_id,
            "from": mail.get('source'),
            "to": mail.get('destination'),
            "subject": mail.get('commonHeaders', {}).get('subject'),
            "timestamp": mail.get('timestamp'),
            "spamVerdict": receipt.get('spamVerdict', {}).get('status'),
            "virusVerdict": receipt.get('virusVerdict', {}).get('status'),
            "dkimVerdict": receipt.get('dkimVerdict', {}).get('status'),
            "spfVerdict": receipt.get('spfVerdict', {}).get('status')
        })

        if not message_id:
            raise ValueError("Missing SES mail.messageId")

        # Fetch raw email from S3
        raw_eml = fetch_raw_email_from_s3(message_id)
        logger.info("Fetched email from S3", extra={
            "messageId": message_id,
            "byteCount": len(raw_eml)
        })

        # Import into Gmail with INBOX and UNREAD labels
        gmail_response = gmail_import(service, raw_eml, DEFAULT_LABEL_IDS)

        logger.info("Successfully imported to Gmail", extra={
            "messageId": message_id,
            "gmailId": gmail_response.get('id')
        })

        # Delete email from S3 after successful import
        delete_email_from_s3(message_id)
        logger.info("Deleted email from S3", extra={"messageId": message_id})

        return {
            'messageId': message_id,
            'gmail_id': gmail_response.get('id'),
            'threadId': gmail_response.get('threadId'),
            'labelIds': gmail_response.get('labelIds'),
            'status': 'ok'
        }

    except (RuntimeError, ValueError, HttpError, ClientError) as e:
        logger.error("Error processing message", extra={
            "messageId": message_id,
            "error": str(e)
        })
        return {
            'messageId': message_id,
            'error': str(e),
            'status': 'error'
        }


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
