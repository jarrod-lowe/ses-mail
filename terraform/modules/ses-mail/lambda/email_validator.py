"""
SES Email Validator Lambda Function

This Lambda function is invoked synchronously (RequestResponse) by SES
after an email is stored in S3, but before the email processor is triggered.
It validates incoming emails and returns a disposition to control further processing.

Currently bounces all incoming emails back to the sender.
Modify the validation logic as needed.
"""

import json
import os

import boto3
from botocore.exceptions import ClientError
from aws_lambda_powertools import Logger

# Configure structured JSON logging
logger = Logger(service="ses-mail-email-validator")

# Environment configuration
BOUNCE_SENDER = os.environ.get('BOUNCE_SENDER', 'mailer-daemon@example.com')

# Initialize AWS SES client
ses_client = boto3.client('ses')


def lambda_handler(event, context):
    """
    Lambda handler for validating SES emails.

    This function is invoked synchronously by SES using RequestResponse invocation.
    It must return a disposition to control further rule processing.

    Args:
        event: SES event containing email metadata
        context: Lambda context object

    Returns:
        dict: Response with disposition key
            - "CONTINUE": Continue processing (default)
            - "STOP_RULE": Stop processing further actions in this rule
            - "STOP_RULE_SET": Stop processing all further rules
    """
    logger.info("Received SES event", extra={"event": event})

    try:
        # Process each SES record
        records = event.get('Records', [])
        for record in records:
            if record.get('eventSource') == 'aws:ses':
                ses = record.get('ses', {})
                mail = ses.get('mail', {})
                receipt = ses.get('receipt', {})

                # Log email metadata
                message_id = mail.get('messageId')
                logger.info("Validating email", extra={
                    "messageId": message_id,
                    "from": mail.get('source'),
                    "to": mail.get('destination'),
                    "subject": mail.get('commonHeaders', {}).get('subject'),
                    "spamVerdict": receipt.get('spamVerdict', {}).get('status'),
                    "virusVerdict": receipt.get('virusVerdict', {}).get('status')
                })

                # TODO: Add validation logic here
                # For example:
                # - Check sender against blocklist/allowlist
                # - Validate email size limits
                # - Check for specific headers
                # - Implement custom spam/virus checks
                #
                # For now, bounce all emails
                destination = mail.get('destination', [])
                source = mail.get('source')

                logger.info("Bouncing email", extra={
                    "messageId": message_id,
                    "from": source,
                    "to": destination
                })

                try:
                    bounce_response = ses_client.send_bounce(
                        OriginalMessageId=message_id,
                        BounceSender=BOUNCE_SENDER,
                        BouncedRecipientInfoList=[
                            {
                                'Recipient': recipient,
                                'BounceType': 'ContentRejected'
                            }
                            for recipient in destination
                        ],
                        Explanation='This email has been rejected by the recipient server.'
                    )
                    logger.info("Bounce sent successfully", extra={
                        "bounceMessageId": bounce_response.get('MessageId')
                    })
                except ClientError as bounce_error:
                    logger.error("Failed to send bounce", extra={"error": str(bounce_error)})
                    # Continue even if bounce fails - we'll still stop processing

        # Stop all further processing (email will not be processed by email_processor)
        logger.info("Email bounced - returning STOP_RULE_SET")
        return {"disposition": "STOP_RULE_SET"}

    except Exception as e:
        logger.exception("Error during validation", extra={"error": str(e)})
        # On error, stop processing to be safe
        logger.warning("Error occurred - returning STOP_RULE_SET to prevent processing")
        return {"disposition": "STOP_RULE_SET"}
