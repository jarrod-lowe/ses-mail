"""
SMTP Credential Manager Lambda Function

This Lambda function automatically creates IAM users and SMTP credentials when new
records are added to the DynamoDB table with status="pending".

Triggered by DynamoDB Streams, this function:
1. Detects new SMTP_USER records with status="pending"
2. Creates programmatic-only IAM users with unique names
3. Generates IAM access keys for SMTP authentication
4. Logs all operations with correlation IDs for traceability

Used as part of the SMTP credential management system.
"""

import json
import logging
import os
import time
from typing import Dict, Any, List
import uuid

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment configuration
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'unknown')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')  # AWS_REGION is automatically set by Lambda

# Initialize AWS clients
dynamodb = boto3.client('dynamodb')
iam = boto3.client('iam')
cloudwatch = boto3.client('cloudwatch')

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
patch_all()


def lambda_handler(event, context):  # noqa: ARG001
    """
    Lambda handler for processing DynamoDB Stream events to create SMTP credentials.

    This function is triggered by DynamoDB Streams when new SMTP credential records
    are inserted or modified. It creates IAM users and access keys for records with
    status="pending".

    Args:
        event: DynamoDB Stream event containing Records array
        context: Lambda context object

    Returns:
        dict: Response with statusCode and processing results
    """
    # Generate correlation ID for this invocation
    correlation_id = str(uuid.uuid4())

    logger.info(json.dumps({
        "message": "Processing DynamoDB Stream event",
        "correlation_id": correlation_id,
        "record_count": len(event.get('Records', [])),
        "environment": ENVIRONMENT
    }))

    if not DYNAMODB_TABLE_NAME:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable must be set")

    success_count = 0
    failure_count = 0
    processed_records = []

    for record in event.get('Records', []):
        record_correlation_id = f"{correlation_id}-{uuid.uuid4().hex[:8]}"

        try:
            result = process_stream_record(record, record_correlation_id)
            if result['success']:
                success_count += 1
            else:
                failure_count += 1
            processed_records.append(result)

        except Exception as e:
            logger.error(json.dumps({
                "message": "Unexpected error processing stream record",
                "correlation_id": record_correlation_id,
                "error": str(e),
                "error_type": type(e).__name__
            }), exc_info=True)
            failure_count += 1
            processed_records.append({
                "success": False,
                "error": str(e),
                "correlation_id": record_correlation_id
            })

    # Publish metrics
    publish_metrics(success_count, failure_count)

    logger.info(json.dumps({
        "message": "Completed processing DynamoDB Stream event",
        "correlation_id": correlation_id,
        "success_count": success_count,
        "failure_count": failure_count,
        "total_records": len(event.get('Records', []))
    }))

    return {
        'statusCode': 200,
        'body': json.dumps({
            'success_count': success_count,
            'failure_count': failure_count,
            'processed_records': processed_records
        })
    }


def process_stream_record(record: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
    """
    Process a single DynamoDB Stream record.

    Args:
        record: DynamoDB Stream record
        correlation_id: Correlation ID for tracing

    Returns:
        dict: Processing result with success status and details
    """
    subsegment = xray_recorder.begin_subsegment('process_smtp_credential')
    if subsegment is None:
        raise RuntimeError("Failed to create X-Ray subsegment for SMTP credential processing")

    try:
        subsegment.put_annotation('correlation_id', correlation_id)

        event_name = record.get('eventName', 'UNKNOWN')
        subsegment.put_annotation('event_name', event_name)

        logger.info(json.dumps({
            "message": "Processing stream record",
            "correlation_id": correlation_id,
            "event_name": event_name,
            "event_id": record.get('eventID')
        }))

        # Only process INSERT and MODIFY events
        if event_name not in ['INSERT', 'MODIFY']:
            logger.info(json.dumps({
                "message": "Skipping non-INSERT/MODIFY event",
                "correlation_id": correlation_id,
                "event_name": event_name
            }))
            subsegment.put_annotation('skipped', True)
            xray_recorder.end_subsegment()
            return {
                "success": True,
                "skipped": True,
                "reason": f"Event type {event_name} not processed",
                "correlation_id": correlation_id
            }

        # Get the new image from the stream record
        new_image = record.get('dynamodb', {}).get('NewImage')
        if not new_image:
            logger.warning(json.dumps({
                "message": "No NewImage in stream record",
                "correlation_id": correlation_id
            }))
            subsegment.put_annotation('skipped', True)
            xray_recorder.end_subsegment()
            return {
                "success": True,
                "skipped": True,
                "reason": "No NewImage in record",
                "correlation_id": correlation_id
            }

        # Check if this is an SMTP_USER record
        pk = new_image.get('PK', {}).get('S', '')
        sk = new_image.get('SK', {}).get('S', '')

        if pk != 'SMTP_USER':
            logger.debug(json.dumps({
                "message": "Skipping non-SMTP_USER record",
                "correlation_id": correlation_id,
                "pk": pk
            }))
            subsegment.put_annotation('skipped', True)
            xray_recorder.end_subsegment()
            return {
                "success": True,
                "skipped": True,
                "reason": f"Not an SMTP_USER record (PK={pk})",
                "correlation_id": correlation_id
            }

        # Check if status is "pending"
        status = new_image.get('status', {}).get('S', '')
        if status != 'pending':
            logger.debug(json.dumps({
                "message": "Skipping non-pending record",
                "correlation_id": correlation_id,
                "status": status,
                "sk": sk
            }))
            subsegment.put_annotation('skipped', True)
            xray_recorder.end_subsegment()
            return {
                "success": True,
                "skipped": True,
                "reason": f"Status is {status}, not pending",
                "correlation_id": correlation_id
            }

        # Extract username from SK (format: USER#{username})
        if not sk.startswith('USER#'):
            logger.error(json.dumps({
                "message": "Invalid SK format for SMTP_USER record",
                "correlation_id": correlation_id,
                "sk": sk
            }))
            subsegment.put_annotation('error', True)
            xray_recorder.end_subsegment()
            return {
                "success": False,
                "error": f"Invalid SK format: {sk}",
                "correlation_id": correlation_id
            }

        username = sk.replace('USER#', '', 1)
        subsegment.put_annotation('username', username)

        logger.info(json.dumps({
            "message": "Found pending SMTP credential request",
            "correlation_id": correlation_id,
            "username": username,
            "pk": pk,
            "sk": sk
        }))

        # Extract allowed_from_addresses for later use
        allowed_from_addresses = []
        if 'allowed_from_addresses' in new_image:
            addresses_list = new_image['allowed_from_addresses'].get('L', [])
            allowed_from_addresses = [addr.get('S', '') for addr in addresses_list]

        logger.info(json.dumps({
            "message": "Extracted allowed_from_addresses",
            "correlation_id": correlation_id,
            "username": username,
            "allowed_from_addresses": allowed_from_addresses
        }))

        # Create IAM user and access key
        result = create_iam_user_and_credentials(
            username=username,
            allowed_from_addresses=allowed_from_addresses,
            correlation_id=correlation_id
        )

        if result['success']:
            subsegment.put_annotation('iam_user_created', True)
            logger.info(json.dumps({
                "message": "Successfully created IAM user and credentials",
                "correlation_id": correlation_id,
                "username": username,
                "iam_user_name": result.get('iam_user_name'),
                "iam_user_arn": result.get('iam_user_arn')
            }))
        else:
            subsegment.put_annotation('error', True)
            logger.error(json.dumps({
                "message": "Failed to create IAM user and credentials",
                "correlation_id": correlation_id,
                "username": username,
                "error": result.get('error')
            }))

        xray_recorder.end_subsegment()
        return result

    except Exception as e:
        subsegment.put_annotation('error', True)
        logger.error(json.dumps({
            "message": "Error in process_stream_record",
            "correlation_id": correlation_id,
            "error": str(e),
            "error_type": type(e).__name__
        }), exc_info=True)
        xray_recorder.end_subsegment()
        raise


def create_iam_user_and_credentials(
    username: str,
    allowed_from_addresses: List[str],
    correlation_id: str
) -> Dict[str, Any]:
    """
    Create a programmatic-only IAM user and generate access keys.

    Args:
        username: Username for the SMTP credential (from DynamoDB SK)
        allowed_from_addresses: List of email addresses this user can send from
        correlation_id: Correlation ID for tracing

    Returns:
        dict: Result containing success status, IAM user details, and credentials
    """
    subsegment = xray_recorder.begin_subsegment('create_iam_user')
    if subsegment is None:
        raise RuntimeError("Failed to create X-Ray subsegment for IAM user creation")

    try:
        # Generate unique IAM user name with timestamp
        timestamp = int(time.time())
        iam_user_name = f"ses-smtp-user-{username}-{timestamp}"

        subsegment.put_annotation('iam_user_name', iam_user_name)
        subsegment.put_annotation('correlation_id', correlation_id)

        logger.info(json.dumps({
            "message": "Creating IAM user",
            "correlation_id": correlation_id,
            "username": username,
            "iam_user_name": iam_user_name
        }))

        # Create programmatic-only IAM user (no console access)
        try:
            create_user_response = iam.create_user(
                UserName=iam_user_name,
                Tags=[
                    {'Key': 'Environment', 'Value': ENVIRONMENT},
                    {'Key': 'ManagedBy', 'Value': 'ses-mail-smtp-credential-manager'},
                    {'Key': 'Purpose', 'Value': 'SMTP Authentication'},
                    {'Key': 'SMTPUsername', 'Value': username},
                    {'Key': 'CorrelationId', 'Value': correlation_id}
                ]
            )
            iam_user_arn = create_user_response['User']['Arn']

            logger.info(json.dumps({
                "message": "IAM user created successfully",
                "correlation_id": correlation_id,
                "iam_user_name": iam_user_name,
                "iam_user_arn": iam_user_arn
            }))

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == 'EntityAlreadyExists':
                logger.warning(json.dumps({
                    "message": "IAM user already exists",
                    "correlation_id": correlation_id,
                    "iam_user_name": iam_user_name
                }))
                # Get existing user ARN
                get_user_response = iam.get_user(UserName=iam_user_name)
                iam_user_arn = get_user_response['User']['Arn']
            else:
                logger.error(json.dumps({
                    "message": "Failed to create IAM user",
                    "correlation_id": correlation_id,
                    "iam_user_name": iam_user_name,
                    "error": str(e),
                    "error_code": error_code
                }))
                subsegment.put_annotation('error', True)
                xray_recorder.end_subsegment()
                return {
                    "success": False,
                    "error": f"IAM user creation failed: {error_code} - {str(e)}",
                    "correlation_id": correlation_id
                }

        # Create access key for SMTP authentication
        try:
            create_key_response = iam.create_access_key(UserName=iam_user_name)
            access_key_id = create_key_response['AccessKey']['AccessKeyId']
            secret_access_key = create_key_response['AccessKey']['SecretAccessKey']

            logger.info(json.dumps({
                "message": "Access key created successfully",
                "correlation_id": correlation_id,
                "iam_user_name": iam_user_name,
                "access_key_id": access_key_id
            }))

            subsegment.put_annotation('access_key_created', True)

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            logger.error(json.dumps({
                "message": "Failed to create access key",
                "correlation_id": correlation_id,
                "iam_user_name": iam_user_name,
                "error": str(e),
                "error_code": error_code
            }))

            # Clean up: delete the IAM user we just created
            try:
                iam.delete_user(UserName=iam_user_name)
                logger.info(json.dumps({
                    "message": "Cleaned up IAM user after access key creation failure",
                    "correlation_id": correlation_id,
                    "iam_user_name": iam_user_name
                }))
            except Exception as cleanup_error:
                logger.error(json.dumps({
                    "message": "Failed to clean up IAM user",
                    "correlation_id": correlation_id,
                    "iam_user_name": iam_user_name,
                    "error": str(cleanup_error)
                }))

            subsegment.put_annotation('error', True)
            xray_recorder.end_subsegment()
            return {
                "success": False,
                "error": f"Access key creation failed: {error_code} - {str(e)}",
                "correlation_id": correlation_id
            }

        xray_recorder.end_subsegment()

        return {
            "success": True,
            "correlation_id": correlation_id,
            "username": username,
            "iam_user_name": iam_user_name,
            "iam_user_arn": iam_user_arn,
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,  # Will be encrypted in task 3.3
            "allowed_from_addresses": allowed_from_addresses
        }

    except Exception as e:
        subsegment.put_annotation('error', True)
        logger.error(json.dumps({
            "message": "Unexpected error in create_iam_user_and_credentials",
            "correlation_id": correlation_id,
            "error": str(e),
            "error_type": type(e).__name__
        }), exc_info=True)
        xray_recorder.end_subsegment()
        return {
            "success": False,
            "error": str(e),
            "correlation_id": correlation_id
        }


def publish_metrics(success_count: int, failure_count: int) -> None:
    """
    Publish custom CloudWatch metrics for SMTP credential creation success/failure rates.

    Args:
        success_count: Number of successfully created credentials
        failure_count: Number of failed credential creations
    """
    try:
        metric_data = []

        if success_count > 0:
            metric_data.append({
                'MetricName': 'SMTPCredentialCreationSuccess',
                'Value': success_count,
                'Unit': 'Count',
                'StorageResolution': 60
            })

        if failure_count > 0:
            metric_data.append({
                'MetricName': 'SMTPCredentialCreationFailure',
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
