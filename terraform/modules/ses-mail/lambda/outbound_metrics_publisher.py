"""
Outbound Email Metrics Publisher Lambda Function

This Lambda function processes SNS notifications from SES Configuration Set
event destinations and publishes custom CloudWatch metrics for account-level
outbound email tracking.

Events received:
- Send: Email submitted to SES
- Delivery: Email successfully delivered
- Bounce: Email bounced (permanent or transient)
- Complaint: Recipient marked email as spam
- Reject: SES rejected email (invalid sender, etc.)

Metrics published to CloudWatch namespace: SESMail/{environment}
- OutboundSend
- OutboundDelivery
- OutboundBounce
- OutboundBounceHard (permanent bounces)
- OutboundBounceSoft (transient bounces)
- OutboundComplaint
- OutboundReject
"""

import json
import os
from typing import Dict, Any, List

import boto3
from aws_lambda_powertools import Logger

# Configure structured JSON logging
logger = Logger(service="ses-mail-outbound-metrics")

# Environment configuration
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'unknown')

# Initialize AWS clients
cloudwatch = boto3.client('cloudwatch')

# Enable X-Ray tracing
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
patch_all()


def lambda_handler(event, context):
    """
    Process SNS notifications from SES Configuration Set and publish CloudWatch metrics.

    Args:
        event: SNS event containing SES notification
        context: Lambda context object

    Returns:
        dict: Response with processed record count
    """
    logger.info("Received SNS event", extra={"recordCount": len(event.get('Records', []))})

    # Initialize metric counters
    metrics = {
        'send': 0,
        'delivery': 0,
        'bounce': 0,
        'bounce_hard': 0,
        'bounce_soft': 0,
        'complaint': 0,
        'reject': 0
    }

    # Process each SNS record
    for record in event.get('Records', []):
        try:
            # Parse SNS message (SES event notification)
            sns_message = record.get('Sns', {}).get('Message', '{}')
            message = json.loads(sns_message)

            event_type = message.get('eventType', '').lower()

            logger.debug("Processing SES event", extra={
                "eventType": event_type,
                "messageId": message.get('mail', {}).get('messageId', 'unknown')
            })

            # Count event types
            if event_type == 'send':
                metrics['send'] += 1

            elif event_type == 'delivery':
                metrics['delivery'] += 1

            elif event_type == 'bounce':
                metrics['bounce'] += 1

                # Classify bounce type (hard/permanent vs soft/transient)
                bounce = message.get('bounce', {})
                bounce_type = bounce.get('bounceType', '').lower()

                if bounce_type == 'permanent':
                    metrics['bounce_hard'] += 1
                    logger.info("Permanent bounce detected", extra={
                        "messageId": message.get('mail', {}).get('messageId'),
                        "bouncedRecipients": bounce.get('bouncedRecipients', [])
                    })

                elif bounce_type == 'transient':
                    metrics['bounce_soft'] += 1
                    logger.debug("Transient bounce detected", extra={
                        "messageId": message.get('mail', {}).get('messageId'),
                        "bounceSubType": bounce.get('bounceSubType')
                    })

            elif event_type == 'complaint':
                metrics['complaint'] += 1
                logger.warning("Complaint received", extra={
                    "messageId": message.get('mail', {}).get('messageId'),
                    "complaintFeedbackType": message.get('complaint', {}).get('complaintFeedbackType')
                })

            elif event_type == 'reject':
                metrics['reject'] += 1
                logger.warning("Email rejected by SES", extra={
                    "messageId": message.get('mail', {}).get('messageId'),
                    "reason": message.get('reject', {}).get('reason')
                })

        except json.JSONDecodeError as e:
            logger.exception("Failed to parse SNS message", extra={
                "error": str(e),
                "message": sns_message[:200]
            })

        except Exception as e:
            logger.exception("Error processing SNS record", extra={
                "error": str(e),
                "recordId": record.get('Sns', {}).get('MessageId')
            })

    # Publish metrics to CloudWatch
    publish_metrics(metrics)

    logger.info("Processed SES events", extra={
        "metrics": metrics,
        "totalEvents": sum(metrics.values())
    })

    return {
        'statusCode': 200,
        'body': json.dumps({
            'processed': len(event.get('Records', [])),
            'metrics': metrics
        })
    }


def publish_metrics(metrics: Dict[str, int]) -> None:
    """
    Publish custom CloudWatch metrics for outbound email events.

    Args:
        metrics: Dictionary of metric counts
    """
    try:
        # Metric name mapping
        metric_mapping = {
            'send': 'OutboundSend',
            'delivery': 'OutboundDelivery',
            'bounce': 'OutboundBounce',
            'bounce_hard': 'OutboundBounceHard',
            'bounce_soft': 'OutboundBounceSoft',
            'complaint': 'OutboundComplaint',
            'reject': 'OutboundReject'
        }

        # Build metric data (only include metrics with non-zero values)
        metric_data = []
        for key, count in metrics.items():
            if count > 0:
                metric_data.append({
                    'MetricName': metric_mapping[key],
                    'Value': count,
                    'Unit': 'Count',
                    'StorageResolution': 60  # 1-minute resolution
                })

        # Publish metrics to CloudWatch (max 20 metrics per call)
        if metric_data:
            # CloudWatch has a limit of 20 metrics per PutMetricData call
            # Batch into chunks of 20
            for i in range(0, len(metric_data), 20):
                batch = metric_data[i:i+20]

                cloudwatch.put_metric_data(
                    Namespace=f'SESMail/{ENVIRONMENT}',
                    MetricData=batch
                )

            logger.info("Published CloudWatch metrics", extra={
                "namespace": f'SESMail/{ENVIRONMENT}',
                "metricCount": len(metric_data),
                "metrics": {k: v for k, v in metrics.items() if v > 0}
            })
        else:
            logger.debug("No metrics to publish (all values zero)")

    except Exception as e:
        logger.exception("Error publishing CloudWatch metrics", extra={
            "error": str(e),
            "metrics": metrics
        })
        # Re-raise to trigger Lambda retry
        raise
