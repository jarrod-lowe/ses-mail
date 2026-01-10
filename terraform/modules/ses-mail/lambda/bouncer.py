"""
SES Email Bouncer Lambda Function

This Lambda function processes bounce requests from the bouncer SQS queue
and sends bounce notifications via SES.

The function is triggered by SQS messages containing enriched email metadata
from the EventBridge Event Bus router.
"""

import json
import os
from typing import Dict, Any, List

import boto3
from botocore.exceptions import ClientError
from aws_lambda_powertools import Logger

# Configure structured JSON logging
logger = Logger(service="ses-mail-bouncer")

# Environment configuration
BOUNCE_SENDER = os.environ.get('BOUNCE_SENDER', 'mailer-daemon@example.com')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'unknown')

# Initialize AWS clients
ses_client = boto3.client('ses')
cloudwatch = boto3.client('cloudwatch')

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
patch_all()


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


def lambda_handler(event, context):
    """
    Lambda handler for sending bounce notifications via SES.

    This function is triggered by SQS messages from the bouncer queue.
    Each message contains enriched email metadata with routing decisions
    from the EventBridge Event Bus.

    Args:
        event: SQS event containing bounce requests
        context: Lambda context object

    Returns:
        dict: Response with batch item failures for SQS partial batch processing
    """
    logger.info("Received SQS event", extra={"recordCount": len(event.get('Records', []))})

    # Track failed messages for SQS partial batch response
    batch_item_failures = []
    success_count = 0
    failure_count = 0

    # Process each SQS record
    for record in event.get('Records', []):
        message_id = record.get('messageId')

        try:
            # Parse SQS message body
            body = json.loads(record.get('body', '{}'))

            # Process the bounce request
            process_bounce_request(body, message_id)

            logger.info("Successfully processed bounce request", extra={"messageId": message_id})
            success_count += 1

        except Exception as e:
            logger.exception("Error processing bounce request", extra={
                "messageId": message_id,
                "error": str(e)
            })

            # Add to batch item failures for SQS retry
            batch_item_failures.append({
                'itemIdentifier': message_id
            })
            failure_count += 1

    # Publish custom metrics
    publish_metrics(success_count, failure_count)

    logger.info("Processed bounce requests", extra={
        "totalCount": len(event.get('Records', [])),
        "successCount": success_count,
        "failureCount": failure_count
    })

    # Return partial batch response for SQS
    # Messages not in failures list will be deleted from queue
    # Failed messages will be retried or sent to DLQ
    return {
        'batchItemFailures': batch_item_failures
    }


def process_bounce_request(message: Dict[str, Any], sqs_message_id: str):
    """
    Process a single bounce request by sending a bounce notification via SES.

    Args:
        message: Enriched email message from EventBridge Event Bus
        sqs_message_id: SQS message ID for logging
    """
    # Create a subsegment for the bounce process
    subsegment = xray_recorder.begin_subsegment('send_bounce')

    try:
        # EventBridge wraps the router output in 'detail'
        detail = message.get('detail', message)  # Fallback to message if not wrapped

        # Extract message ID
        message_id = detail.get('originalMessageId', 'unknown')

        # Extract actions and targets from new router structure
        actions = detail.get('actions', {})
        bounce_action = actions.get('bounce', {})
        targets = bounce_action.get('targets', [])

        if not targets:
            logger.warning("No bounce targets found in message", extra={"sqsMessageId": sqs_message_id})
            return

        # Extract SES mail metadata from the router-enriched EventBridge message
        # Structure: EventBridge message → detail → ses → mail
        ses_event = detail.get('ses', {})
        ses_mail = ses_event.get('mail', {})

        # Get the actual sender from the From header (not the Return-Path/source)
        # The 'source' field contains SES's internal Return-Path
        # The 'commonHeaders.from' contains the actual From header we should bounce to
        common_headers = ses_mail.get('commonHeaders', {})
        from_addresses = common_headers.get('from', [])

        # Extract sender address with validation
        source = None
        if from_addresses and len(from_addresses) > 0:
            source = from_addresses[0]
        else:
            # Fallback to envelope sender (Return-Path) if From header is missing
            source = ses_mail.get('source')

        # Validate that we successfully extracted a sender address
        if not source or '@' not in source:
            logger.error("Failed to extract valid sender address", extra={
                "sqsMessageId": sqs_message_id,
                "sesMailStructure": json.dumps(ses_mail, default=str),
                "fromHeaders": from_addresses,
                "envelopeSender": ses_mail.get('source')
            })
            raise ValueError(
                f"Cannot process bounce: unable to extract valid sender address. "
                f"From headers: {from_addresses}, source: {ses_mail.get('source')}"
            )

        subject = common_headers.get('subject', 'No Subject')
        timestamp = ses_mail.get('timestamp', '')

        logger.info("Processing bounce", extra={
            "messageId": message_id,
            "from": source,
            "subject": subject
        })

        # Add X-Ray annotations for searchability
        if subsegment:
            subsegment.put_annotation('messageId', message_id)
            subsegment.put_annotation('source', source)
            subsegment.put_annotation('environment', ENVIRONMENT)
            subsegment.put_annotation('action', 'bounce')

            # Count bounce reasons for X-Ray analytics
            bounce_reasons = {'auth-fail': 0, 'policy': 0}
            for target_info in targets:
                reason = target_info.get('reason', 'policy')
                bounce_reasons[reason] += 1

            subsegment.put_annotation('bounce_auth_fail_count', bounce_reasons['auth-fail'])
            subsegment.put_annotation('bounce_policy_count', bounce_reasons['policy'])

        # Send bounce notification for each target recipient
        for target_info in targets:
            recipient = target_info.get('target')
            bounce_reason = target_info.get('reason', 'policy')  # Default to policy if not specified

            logger.info("Sending bounce for recipient", extra={
                "recipient": recipient,
                "bounceReason": bounce_reason
            })

            try:
                bounce_message_id = send_bounce_notification(
                    recipient=recipient,
                    original_sender=source,
                    original_subject=subject,
                    original_timestamp=timestamp,
                    bounce_reason=bounce_reason
                )

                logger.info("Bounce sent successfully", extra={"recipient": recipient})

                # Log action result for dashboard (success)
                logger.info("Action result", extra={
                    "messageId": message_id,
                    "sender": source,
                    "subject": extract_subject(ses_event, max_length=64),
                    "recipient": recipient,
                    "action": "bounce",
                    "result": "success",
                    "resultId": bounce_message_id  # SES bounce message ID
                })

            except Exception as e:
                # Log action result for dashboard (failure)
                logger.error("Action result", extra={
                    "messageId": message_id,
                    "sender": source,
                    "subject": extract_subject(ses_event, max_length=64),
                    "recipient": recipient,
                    "action": "bounce",
                    "result": "failure",
                    "error": str(e)
                })
                # Re-raise so the outer handler can catch it
                raise

        logger.info("Bounce processing complete", extra={"messageId": message_id})

    finally:
        # Always end the subsegment
        xray_recorder.end_subsegment()


def send_bounce_notification(
    recipient: str,
    original_sender: str,
    original_subject: str,
    original_timestamp: str,
    bounce_reason: str = 'policy'
) -> str:
    """
    Send a bounce notification email via SES.

    Args:
        recipient: Original recipient email address
        original_sender: Original sender email address
        original_subject: Original email subject
        original_timestamp: Original email timestamp
        bounce_reason: Reason for bounce ('auth-fail' or 'policy')

    Returns:
        str: SES bounce message ID
    """
    # Construct bounce message
    bounce_subject = f"Mail Delivery Failed: {original_subject}"

    # Determine bounce reason text without revealing internal system details
    if bounce_reason == 'auth-fail':
        reason_text = "Your message failed email authentication checks (SPF/DKIM). This typically indicates a mail server configuration issue. Please verify your email server's SPF and DKIM settings."
        reason_html = "Your message failed email authentication checks (SPF/DKIM). This typically indicates a mail server configuration issue. Please verify your email server's SPF and DKIM settings."
    else:  # policy
        reason_text = f"The recipient address ({recipient}) is not configured to receive mail."
        reason_html = f"The recipient address (<strong>{recipient}</strong>) is not configured to receive mail."

    bounce_body_text = f"""
This is an automatically generated Delivery Status Notification.

YOUR MESSAGE COULD NOT BE DELIVERED

Your message to {recipient} could not be delivered.

Original Message Details:
- From: {original_sender}
- To: {recipient}
- Subject: {original_subject}
- Timestamp: {original_timestamp}

Reason:
{reason_text}

If you believe this is an error, please contact the system administrator.

---
This is an automated message. Please do not reply to this email.
"""

    bounce_body_html = f"""
<html>
<head></head>
<body>
    <h2>Mail Delivery Failed</h2>
    <p>This is an automatically generated Delivery Status Notification.</p>

    <h3>YOUR MESSAGE COULD NOT BE DELIVERED</h3>
    <p>Your message to <strong>{recipient}</strong> could not be delivered.</p>

    <h3>Original Message Details:</h3>
    <ul>
        <li><strong>From:</strong> {original_sender}</li>
        <li><strong>To:</strong> {recipient}</li>
        <li><strong>Subject:</strong> {original_subject}</li>
        <li><strong>Timestamp:</strong> {original_timestamp}</li>
    </ul>

    <h3>Reason:</h3>
    <p>{reason_html}</p>

    <p>If you believe this is an error, please contact the system administrator.</p>

    <hr>
    <p><em>This is an automated message. Please do not reply to this email.</em></p>
</body>
</html>
"""

    try:
        # Send bounce notification via SES
        response = ses_client.send_email(
            Source=BOUNCE_SENDER,
            Destination={
                'ToAddresses': [original_sender]
            },
            Message={
                'Subject': {
                    'Data': bounce_subject,
                    'Charset': 'UTF-8'
                },
                'Body': {
                    'Text': {
                        'Data': bounce_body_text,
                        'Charset': 'UTF-8'
                    },
                    'Html': {
                        'Data': bounce_body_html,
                        'Charset': 'UTF-8'
                    }
                }
            }
        )

        bounce_message_id = response['MessageId']
        logger.info("Bounce notification sent", extra={"bounceMessageId": bounce_message_id})

        # Add bounce message ID to X-Ray for traceability
        subsegment = xray_recorder.current_subsegment()
        if subsegment:
            subsegment.put_annotation('bounce_message_id', bounce_message_id)

        return bounce_message_id

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code')
        error_message = e.response.get('Error', {}).get('Message')
        logger.error("SES error sending bounce", extra={
            "errorCode": error_code,
            "errorMessage": error_message
        })
        raise RuntimeError(f"Failed to send bounce notification: {error_message}")


def publish_metrics(success_count: int, failure_count: int) -> None:
    """
    Publish custom CloudWatch metrics for bounce processing success/failure rates.

    Args:
        success_count: Number of successfully sent bounce notifications
        failure_count: Number of failed bounce notifications
    """
    try:
        metric_data = []

        if success_count > 0:
            metric_data.append({
                'MetricName': 'BounceSendSuccess',
                'Value': success_count,
                'Unit': 'Count',
                'StorageResolution': 60
            })

        if failure_count > 0:
            metric_data.append({
                'MetricName': 'BounceSendFailure',
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
