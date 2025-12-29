"""
Canary Monitor Lambda Function

This Lambda function processes canary test emails from the canary-monitor queue.
It records completion in DynamoDB with TTL for automatic cleanup and deletes the email from S3.
"""

import json
import os
import time
from typing import Dict, Any

import boto3
from botocore.exceptions import ClientError
from aws_lambda_powertools import Logger

# Configure structured JSON logging
logger = Logger(service="ses-mail-canary-monitor")

# Environment configuration
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME')
EMAIL_BUCKET = os.environ.get('EMAIL_BUCKET')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'unknown')
S3_PREFIX = 'emails'  # Match other handlers

# Initialize AWS clients
dynamodb = boto3.client('dynamodb')
s3_client = boto3.client('s3')
cloudwatch = boto3.client('cloudwatch')

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
patch_all()


def lambda_handler(event, context):
    """
    Lambda handler for processing canary test emails from SQS.
    Records completion in DynamoDB and deletes email from S3.

    Args:
        event: SQS event containing canary test messages
        context: Lambda context object

    Returns:
        dict: Response with batch item failures for SQS partial batch processing
    """
    logger.info("Received SQS event", extra={"recordCount": len(event.get('Records', []))})

    batch_item_failures = []
    success_count = 0
    failure_count = 0

    # Process each SQS record
    for record in event.get('Records', []):
        receipt_handle = record.get('receiptHandle')

        try:
            # Parse SQS message body
            body = json.loads(record.get('body', '{}'))

            # Process the canary message
            process_canary_message(body)

            logger.info("Successfully processed canary message", extra={"receiptHandle": receipt_handle})
            success_count += 1

        except Exception as e:
            logger.exception("Error processing canary message", extra={
                "receiptHandle": receipt_handle,
                "error": str(e)
            })

            # Add to batch item failures for SQS retry
            batch_item_failures.append({
                'itemIdentifier': receipt_handle
            })
            failure_count += 1

    # Publish custom metrics
    publish_metrics(success_count, failure_count)

    logger.info("Processed canary messages", extra={
        "totalCount": len(event.get('Records', [])),
        "successCount": success_count,
        "failureCount": failure_count
    })

    return {
        'batchItemFailures': batch_item_failures
    }


def process_canary_message(message: Dict[str, Any]):
    """
    Process a single canary message by recording completion in DynamoDB.

    Args:
        message: Enriched email message from EventBridge Event Bus
    """
    subsegment = xray_recorder.begin_subsegment('process_canary')

    try:
        # EventBridge wraps the router output in 'detail'
        detail = message.get('detail', message)

        # Extract message ID
        message_id = detail.get('originalMessageId', 'unknown')

        # Extract SES metadata
        ses_event = detail.get('ses', {})
        ses_mail = ses_event.get('mail', {})
        source = ses_mail.get('source', 'unknown')

        # Add X-Ray annotations
        if subsegment:
            subsegment.put_annotation('messageId', message_id)
            subsegment.put_annotation('action', 'canary-monitor')
            subsegment.put_annotation('source', source)

        logger.info("Processing canary test email", extra={
            "messageId": message_id,
            "source": source
        })

        # Write completion record to DynamoDB
        completion_timestamp = write_completion_record(message_id)

        # Delete email from S3
        delete_email_from_s3(message_id)

        # Log "Action result" for dashboard panel
        logger.info("Action result", extra={
            "messageId": message_id,
            "sender": source,
            "subject": extract_subject(ses_event, max_length=64),
            "recipient": "canary",
            "action": "canary-monitor",
            "result": "success",
            "timestamp": completion_timestamp
        })

        logger.info("Canary processing complete", extra={
            "messageId": message_id,
            "completionTimestamp": completion_timestamp
        })

    finally:
        xray_recorder.end_subsegment()


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


def write_completion_record(message_id: str) -> int:
    """
    Write canary completion record to DynamoDB with TTL.

    Args:
        message_id: SES message ID (used as PK)

    Returns:
        int: Completion timestamp in milliseconds since epoch
    """
    if not DYNAMODB_TABLE_NAME:
        raise RuntimeError("DYNAMODB_TABLE_NAME environment variable must be set")

    current_time_ms = int(time.time() * 1000)
    ttl_seconds = int(time.time()) + 610  # TTL = current time + 610 seconds

    try:
        dynamodb.put_item(
            TableName=DYNAMODB_TABLE_NAME,
            Item={
                'PK': {'S': f'CANARY#{message_id}'},
                'SK': {'S': 'COMPLETION#v1'},
                'entity_type': {'S': 'CANARY_COMPLETION'},
                'message_id': {'S': message_id},
                'timestamp': {'N': str(current_time_ms)},
                'ttl': {'N': str(ttl_seconds)},
                'environment': {'S': ENVIRONMENT}
            }
        )

        logger.info("Wrote completion record to DynamoDB", extra={
            "messageId": message_id,
            "timestamp": current_time_ms,
            "ttl": ttl_seconds
        })

        return current_time_ms

    except ClientError as e:
        logger.error("Failed to write completion record", extra={
            "messageId": message_id,
            "error": str(e)
        })
        raise RuntimeError(f"Failed to write completion record: {e}")


def delete_email_from_s3(message_id: str) -> None:
    """
    Delete the canary email from S3 after processing.

    Args:
        message_id: SES message ID
    """
    if not EMAIL_BUCKET:
        raise RuntimeError("EMAIL_BUCKET environment variable must be set")

    s3_key = f"{S3_PREFIX}/{message_id}"

    try:
        s3_client.delete_object(Bucket=EMAIL_BUCKET, Key=s3_key)
        logger.info("Deleted canary email from S3", extra={
            "bucket": EMAIL_BUCKET,
            "key": s3_key
        })
    except ClientError as e:
        # Log error but don't fail - cleanup is not critical
        logger.error("Failed to delete canary email from S3", extra={
            "bucket": EMAIL_BUCKET,
            "key": s3_key,
            "error": str(e)
        })


def publish_metrics(success_count: int, failure_count: int) -> None:
    """
    Publish custom CloudWatch metrics for canary processing.

    Args:
        success_count: Number of successfully processed canary messages
        failure_count: Number of failed canary messages
    """
    try:
        metric_data = []

        if success_count > 0:
            metric_data.append({
                'MetricName': 'CanaryMonitorSuccess',
                'Value': success_count,
                'Unit': 'Count',
                'StorageResolution': 60
            })

        if failure_count > 0:
            metric_data.append({
                'MetricName': 'CanaryMonitorFailure',
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
