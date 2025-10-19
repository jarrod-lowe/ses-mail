"""
SES Email Bouncer Lambda Function

This Lambda function processes bounce requests from the bouncer SQS queue
and sends bounce notifications via SES.

The function is triggered by SQS messages containing enriched email metadata
from the EventBridge Event Bus router.
"""

import json
import logging
import os
from typing import Dict, Any, List

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment configuration
BOUNCE_SENDER = os.environ.get('BOUNCE_SENDER', 'mailer-daemon@example.com')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'unknown')

# Initialize AWS clients
ses_client = boto3.client('ses')
cloudwatch = boto3.client('cloudwatch')

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
patch_all()


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
    logger.info(f"Received SQS event with {len(event.get('Records', []))} records")

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

            logger.info(f"Successfully processed bounce request: {message_id}")
            success_count += 1

        except Exception as e:
            logger.error(f"Error processing bounce request {message_id}: {str(e)}", exc_info=True)

            # Add to batch item failures for SQS retry
            batch_item_failures.append({
                'itemIdentifier': message_id
            })
            failure_count += 1

    # Publish custom metrics
    publish_metrics(success_count, failure_count)

    logger.info(f"Processed {len(event.get('Records', []))} bounce requests (success: {success_count}, failures: {failure_count})")

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
            logger.warning(f"No bounce targets found in message {sqs_message_id}")
            return

        # Parse the original SES event from the body field to get email metadata
        ses_event_body = json.loads(detail.get('body', '{}'))
        ses_message = ses_event_body.get('Message')
        if ses_message:
            ses_event = json.loads(ses_message)
        else:
            ses_event = ses_event_body

        # Extract SES mail metadata
        ses_mail = ses_event.get('mail', {})
        source = ses_mail.get('source', 'unknown@unknown.com')
        subject = ses_mail.get('commonHeaders', {}).get('subject', 'No Subject')
        timestamp = ses_mail.get('timestamp', '')

        logger.info(f"Processing bounce - Message ID: {message_id}, From: {source}")

        # Add X-Ray annotations for searchability
        subsegment.put_annotation('messageId', message_id)
        subsegment.put_annotation('source', source)
        subsegment.put_annotation('environment', ENVIRONMENT)
        subsegment.put_annotation('action', 'bounce')

        # Send bounce notification for each target recipient
        for target_info in targets:
            recipient = target_info.get('target')

            logger.info(f"Sending bounce for recipient: {recipient}")

            send_bounce_notification(
                recipient=recipient,
                original_sender=source,
                original_subject=subject,
                original_timestamp=timestamp,
                matched_rule='ROUTE#*',  # Rule info not in simplified structure
                rule_description='Email bounced by routing system'
            )

            logger.info(f"Bounce sent successfully for recipient: {recipient}")

        logger.info(f"Bounce processing complete for message: {message_id}")

    finally:
        # Always end the subsegment
        xray_recorder.end_subsegment()


def send_bounce_notification(
    recipient: str,
    original_sender: str,
    original_subject: str,
    original_timestamp: str,
    matched_rule: str,
    rule_description: str
):
    """
    Send a bounce notification email via SES.

    Args:
        recipient: Original recipient email address
        original_sender: Original sender email address
        original_subject: Original email subject
        original_timestamp: Original email timestamp
        matched_rule: Routing rule that triggered the bounce
        rule_description: Human-readable description of the rule
    """
    # Construct bounce message
    bounce_subject = f"Mail Delivery Failed: {original_subject}"

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
The recipient address ({recipient}) is not configured to receive mail.

Routing Rule: {matched_rule}
Rule Description: {rule_description}

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
    <p>The recipient address (<strong>{recipient}</strong>) is not configured to receive mail.</p>

    <p><strong>Routing Rule:</strong> {matched_rule}<br>
    <strong>Rule Description:</strong> {rule_description}</p>

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

        logger.info(f"Bounce notification sent - MessageId: {response['MessageId']}")

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code')
        error_message = e.response.get('Error', {}).get('Message')
        logger.error(f"SES error sending bounce: {error_code} - {error_message}")
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
            logger.info(f"Published metrics: success={success_count}, failure={failure_count}")

    except Exception as e:
        # Don't fail the lambda if metrics publishing fails
        logger.error(f"Error publishing metrics: {str(e)}", exc_info=True)
