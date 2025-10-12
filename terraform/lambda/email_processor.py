"""
SES Email Processor Lambda Function

This Lambda function is triggered when SES receives an email.
It will eventually process the email from S3 and insert it into Gmail via the API.

Current implementation is a stub that logs the event.
"""

import json
import logging
import os
import boto3

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
ssm_client = boto3.client('ssm')


def lambda_handler(event, context):
    """
    Lambda handler for processing SES emails.

    Args:
        event: SES event containing email metadata
        context: Lambda context object

    Returns:
        dict: Response with status code and message
    """
    logger.info(f"Received SES event: {json.dumps(event)}")

    try:
        # Extract SES record information
        if 'Records' in event:
            for record in event['Records']:
                if record.get('eventSource') == 'aws:ses':
                    process_ses_record(record)

        return {
            'statusCode': 200,
            'body': json.dumps('Email processed successfully (stub)')
        }

    except Exception as e:
        logger.error(f"Error processing email: {str(e)}", exc_info=True)
        raise


def process_ses_record(record):
    """
    Process a single SES record.

    Args:
        record: SES record from the event
    """
    ses = record.get('ses', {})
    mail = ses.get('mail', {})
    receipt = ses.get('receipt', {})

    # Log email metadata
    logger.info(f"Processing email:")
    logger.info(f"  Message ID: {mail.get('messageId')}")
    logger.info(f"  From: {mail.get('source')}")
    logger.info(f"  To: {mail.get('destination')}")
    logger.info(f"  Subject: {mail.get('commonHeaders', {}).get('subject')}")
    logger.info(f"  Timestamp: {mail.get('timestamp')}")
    logger.info(f"  Spam verdict: {receipt.get('spamVerdict', {}).get('status')}")
    logger.info(f"  Virus verdict: {receipt.get('virusVerdict', {}).get('status')}")
    logger.info(f"  DKIM verdict: {receipt.get('dkimVerdict', {}).get('status')}")
    logger.info(f"  SPF verdict: {receipt.get('spfVerdict', {}).get('status')}")

    # Get S3 bucket and key if available
    if 'action' in receipt:
        action = receipt['action']
        if action.get('type') == 'S3':
            bucket = action.get('bucketName')
            object_key = action.get('objectKey')
            logger.info(f"  S3 Location: s3://{bucket}/{object_key}")

            # TODO: Retrieve email from S3
            # TODO: Parse email content
            # TODO: Get Gmail token from SSM Parameter Store
            # TODO: Insert email into Gmail via API

    logger.info("Email processing stub completed")


def get_gmail_token():
    """
    Retrieve Gmail OAuth token from SSM Parameter Store.

    Returns:
        dict: Gmail OAuth token
    """
    parameter_name = os.environ.get('GMAIL_TOKEN_PARAMETER', '/ses-mail/gmail-token')

    try:
        response = ssm_client.get_parameter(
            Name=parameter_name,
            WithDecryption=True
        )
        token_json = response['Parameter']['Value']
        return json.loads(token_json)
    except Exception as e:
        logger.error(f"Error retrieving Gmail token from SSM: {str(e)}")
        raise


def get_email_from_s3(bucket, key):
    """
    Retrieve email content from S3.

    Args:
        bucket: S3 bucket name
        key: S3 object key

    Returns:
        str: Raw email content
    """
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return response['Body'].read().decode('utf-8')
    except Exception as e:
        logger.error(f"Error retrieving email from S3: {str(e)}")
        raise
