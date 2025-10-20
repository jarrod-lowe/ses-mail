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

import base64
import hashlib
import hmac
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
kms = boto3.client('kms')
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

        # Attach SES policy to restrict email sending
        policy_result = attach_ses_policy_to_user(
            iam_user_name=iam_user_name,
            allowed_from_addresses=allowed_from_addresses,
            username=username,
            correlation_id=correlation_id
        )

        if not policy_result['success']:
            logger.error(json.dumps({
                "message": "Failed to attach SES policy, rolling back",
                "correlation_id": correlation_id,
                "iam_user_name": iam_user_name,
                "error": policy_result.get('error')
            }))

            # Clean up: delete access key and IAM user
            try:
                iam.delete_access_key(UserName=iam_user_name, AccessKeyId=access_key_id)
                iam.delete_user(UserName=iam_user_name)
                logger.info(json.dumps({
                    "message": "Cleaned up IAM user and access key after policy attachment failure",
                    "correlation_id": correlation_id,
                    "iam_user_name": iam_user_name
                }))
            except Exception as cleanup_error:
                logger.error(json.dumps({
                    "message": "Failed to clean up IAM user after policy attachment failure",
                    "correlation_id": correlation_id,
                    "iam_user_name": iam_user_name,
                    "error": str(cleanup_error)
                }))

            subsegment.put_annotation('error', True)
            xray_recorder.end_subsegment()
            return {
                "success": False,
                "error": f"Policy attachment failed: {policy_result.get('error')}",
                "correlation_id": correlation_id
            }

        subsegment.put_annotation('policy_attached', True)

        # Convert IAM secret access key to SES SMTP password
        logger.info(json.dumps({
            "message": "Converting secret access key to SES SMTP password",
            "correlation_id": correlation_id,
            "region": AWS_REGION
        }))

        smtp_password = convert_secret_to_smtp_password(secret_access_key, AWS_REGION)

        logger.info(json.dumps({
            "message": "Successfully converted to SMTP password",
            "correlation_id": correlation_id
        }))

        # Encrypt credentials with KMS
        encryption_result = encrypt_credentials_with_kms(
            access_key_id=access_key_id,
            smtp_password=smtp_password,
            correlation_id=correlation_id
        )

        if not encryption_result['success']:
            logger.error(json.dumps({
                "message": "Failed to encrypt credentials, rolling back",
                "correlation_id": correlation_id,
                "error": encryption_result.get('error')
            }))

            # Clean up: delete policy, access key, and IAM user
            try:
                iam.delete_user_policy(UserName=iam_user_name, PolicyName=policy_result.get('policy_name'))
                iam.delete_access_key(UserName=iam_user_name, AccessKeyId=access_key_id)
                iam.delete_user(UserName=iam_user_name)
                logger.info(json.dumps({
                    "message": "Cleaned up IAM resources after encryption failure",
                    "correlation_id": correlation_id,
                    "iam_user_name": iam_user_name
                }))
            except Exception as cleanup_error:
                logger.error(json.dumps({
                    "message": "Failed to clean up IAM resources after encryption failure",
                    "correlation_id": correlation_id,
                    "iam_user_name": iam_user_name,
                    "error": str(cleanup_error)
                }))

            subsegment.put_annotation('error', True)
            xray_recorder.end_subsegment()
            return {
                "success": False,
                "error": f"Credential encryption failed: {encryption_result.get('error')}",
                "correlation_id": correlation_id
            }

        # Store encrypted credentials in DynamoDB
        storage_result = store_credentials_in_dynamodb(
            username=username,
            encrypted_credentials=encryption_result['encrypted_credentials'],
            iam_user_arn=iam_user_arn,
            correlation_id=correlation_id
        )

        if not storage_result['success']:
            logger.error(json.dumps({
                "message": "Failed to store credentials in DynamoDB, rolling back",
                "correlation_id": correlation_id,
                "error": storage_result.get('error')
            }))

            # Clean up: delete policy, access key, and IAM user
            try:
                iam.delete_user_policy(UserName=iam_user_name, PolicyName=policy_result.get('policy_name'))
                iam.delete_access_key(UserName=iam_user_name, AccessKeyId=access_key_id)
                iam.delete_user(UserName=iam_user_name)
                logger.info(json.dumps({
                    "message": "Cleaned up IAM resources after storage failure",
                    "correlation_id": correlation_id,
                    "iam_user_name": iam_user_name
                }))
            except Exception as cleanup_error:
                logger.error(json.dumps({
                    "message": "Failed to clean up IAM resources after storage failure",
                    "correlation_id": correlation_id,
                    "iam_user_name": iam_user_name,
                    "error": str(cleanup_error)
                }))

            subsegment.put_annotation('error', True)
            xray_recorder.end_subsegment()
            return {
                "success": False,
                "error": f"Credential storage failed: {storage_result.get('error')}",
                "correlation_id": correlation_id
            }

        subsegment.put_annotation('credentials_stored', True)
        xray_recorder.end_subsegment()

        return {
            "success": True,
            "correlation_id": correlation_id,
            "username": username,
            "iam_user_name": iam_user_name,
            "iam_user_arn": iam_user_arn,
            "access_key_id": access_key_id,
            "allowed_from_addresses": allowed_from_addresses,
            "policy_name": policy_result.get('policy_name'),
            "policy_attached": True,
            "credentials_encrypted": True,
            "credentials_stored": True,
            "status": "active"
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


def generate_ses_policy(allowed_from_addresses: List[str], username: str) -> Dict[str, Any]:
    """
    Generate an IAM policy document that restricts SES sending to specific email addresses.

    Args:
        allowed_from_addresses: List of email address patterns (supports wildcards like *@domain.com)
        username: Username for logging purposes

    Returns:
        dict: IAM policy document with SES restrictions
    """
    # Handle empty or missing allowed_from_addresses
    if not allowed_from_addresses:
        logger.warning(f"No allowed_from_addresses specified for user {username}, policy will deny all")
        # Create a policy that denies all SES sending
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Deny",
                    "Action": [
                        "ses:SendEmail",
                        "ses:SendRawEmail"
                    ],
                    "Resource": "*"
                }
            ]
        }

    # Create policy with StringLike condition for allowed addresses
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "ses:SendEmail",
                    "ses:SendRawEmail"
                ],
                "Resource": "*",
                "Condition": {
                    "StringLike": {
                        "ses:FromAddress": allowed_from_addresses
                    }
                }
            }
        ]
    }

    return policy


def attach_ses_policy_to_user(
    iam_user_name: str,
    allowed_from_addresses: List[str],
    username: str,
    correlation_id: str
) -> Dict[str, Any]:
    """
    Generate and attach an inline SES policy to an IAM user.

    Args:
        iam_user_name: IAM user name to attach policy to
        allowed_from_addresses: List of email addresses this user can send from
        username: Original username for policy naming
        correlation_id: Correlation ID for tracing

    Returns:
        dict: Result containing success status and policy details
    """
    subsegment = xray_recorder.begin_subsegment('attach_ses_policy')
    if subsegment is None:
        raise RuntimeError("Failed to create X-Ray subsegment for policy attachment")

    try:
        subsegment.put_annotation('iam_user_name', iam_user_name)
        subsegment.put_annotation('correlation_id', correlation_id)

        # Generate policy document
        policy_document = generate_ses_policy(allowed_from_addresses, username)
        policy_name = f"ses-smtp-policy-{username}"

        logger.info(json.dumps({
            "message": "Generated SES policy",
            "correlation_id": correlation_id,
            "iam_user_name": iam_user_name,
            "policy_name": policy_name,
            "allowed_from_addresses": allowed_from_addresses,
            "policy_statement_count": len(policy_document.get('Statement', []))
        }))

        # Attach inline policy to user
        try:
            iam.put_user_policy(
                UserName=iam_user_name,
                PolicyName=policy_name,
                PolicyDocument=json.dumps(policy_document)
            )

            logger.info(json.dumps({
                "message": "Successfully attached SES policy to IAM user",
                "correlation_id": correlation_id,
                "iam_user_name": iam_user_name,
                "policy_name": policy_name
            }))

            subsegment.put_annotation('policy_attached', True)
            xray_recorder.end_subsegment()

            return {
                "success": True,
                "policy_name": policy_name,
                "policy_document": policy_document,
                "correlation_id": correlation_id
            }

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            logger.error(json.dumps({
                "message": "Failed to attach policy to IAM user",
                "correlation_id": correlation_id,
                "iam_user_name": iam_user_name,
                "policy_name": policy_name,
                "error": str(e),
                "error_code": error_code
            }))

            subsegment.put_annotation('error', True)
            xray_recorder.end_subsegment()

            return {
                "success": False,
                "error": f"Policy attachment failed: {error_code} - {str(e)}",
                "correlation_id": correlation_id
            }

    except Exception as e:
        subsegment.put_annotation('error', True)
        logger.error(json.dumps({
            "message": "Unexpected error in attach_ses_policy_to_user",
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


def convert_secret_to_smtp_password(secret_access_key: str, region: str) -> str:
    """
    Convert IAM secret access key to SES SMTP password using AWS algorithm (Version 4).

    This implements the official AWS SES SMTP password conversion algorithm which
    uses a chain of HMAC-SHA256 operations to derive the SMTP password from the
    IAM secret access key.

    Args:
        secret_access_key: IAM secret access key
        region: AWS region (e.g., 'us-east-1', 'ap-southeast-2')

    Returns:
        str: Base64-encoded SES SMTP password

    References:
        https://docs.aws.amazon.com/ses/latest/dg/smtp-credentials.html
    """
    # Constants for SES SMTP password derivation (Version 4)
    date = "11111111"
    service = "ses"
    terminal = "aws4_request"
    message = "SendRawEmail"
    version = bytes([0x04])

    # Chain of HMAC-SHA256 operations
    # Step 1: Sign date with "AWS4" + secret key
    signature = hmac.new(
        ("AWS4" + secret_access_key).encode('utf-8'),
        date.encode('utf-8'),
        hashlib.sha256
    ).digest()

    # Step 2: Sign region with previous signature
    signature = hmac.new(
        signature,
        region.encode('utf-8'),
        hashlib.sha256
    ).digest()

    # Step 3: Sign service name with previous signature
    signature = hmac.new(
        signature,
        service.encode('utf-8'),
        hashlib.sha256
    ).digest()

    # Step 4: Sign terminal string with previous signature
    signature = hmac.new(
        signature,
        terminal.encode('utf-8'),
        hashlib.sha256
    ).digest()

    # Step 5: Sign message with previous signature
    signature = hmac.new(
        signature,
        message.encode('utf-8'),
        hashlib.sha256
    ).digest()

    # Prepend version byte and base64 encode
    signature_and_version = version + signature
    smtp_password = base64.b64encode(signature_and_version).decode('utf-8')

    return smtp_password


def encrypt_credentials_with_kms(
    access_key_id: str,
    smtp_password: str,
    correlation_id: str
) -> Dict[str, Any]:
    """
    Encrypt SMTP credentials using AWS KMS.

    Args:
        access_key_id: IAM access key ID (SMTP username)
        smtp_password: SES SMTP password
        correlation_id: Correlation ID for tracing

    Returns:
        dict: Result containing success status and encrypted credentials
    """
    subsegment = xray_recorder.begin_subsegment('encrypt_credentials')
    if subsegment is None:
        raise RuntimeError("Failed to create X-Ray subsegment for KMS encryption")

    try:
        subsegment.put_annotation('correlation_id', correlation_id)

        # Create credentials payload
        credentials_payload = {
            "access_key_id": access_key_id,
            "smtp_password": smtp_password
        }

        logger.info(json.dumps({
            "message": "Encrypting SMTP credentials with KMS",
            "correlation_id": correlation_id,
            "access_key_id": access_key_id
        }))

        # Encrypt using customer managed KMS key for SMTP credentials
        kms_key_alias = f'alias/ses-mail-smtp-credentials-{ENVIRONMENT}'

        try:
            response = kms.encrypt(
                KeyId=kms_key_alias,
                Plaintext=json.dumps(credentials_payload).encode('utf-8')
            )

            encrypted_blob = base64.b64encode(response['CiphertextBlob']).decode('utf-8')

            logger.info(json.dumps({
                "message": "Successfully encrypted credentials with KMS",
                "correlation_id": correlation_id,
                "key_id": response.get('KeyId'),
                "encrypted_blob_length": len(encrypted_blob)
            }))

            subsegment.put_annotation('encrypted', True)
            xray_recorder.end_subsegment()

            return {
                "success": True,
                "encrypted_credentials": encrypted_blob,
                "key_id": response.get('KeyId'),
                "correlation_id": correlation_id
            }

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            logger.error(json.dumps({
                "message": "Failed to encrypt credentials with KMS",
                "correlation_id": correlation_id,
                "error": str(e),
                "error_code": error_code
            }))

            subsegment.put_annotation('error', True)
            xray_recorder.end_subsegment()

            return {
                "success": False,
                "error": f"KMS encryption failed: {error_code} - {str(e)}",
                "correlation_id": correlation_id
            }

    except Exception as e:
        subsegment.put_annotation('error', True)
        logger.error(json.dumps({
            "message": "Unexpected error in encrypt_credentials_with_kms",
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


def store_credentials_in_dynamodb(
    username: str,
    encrypted_credentials: str,
    iam_user_arn: str,
    correlation_id: str
) -> Dict[str, Any]:
    """
    Store encrypted SMTP credentials in DynamoDB and update record status to active.

    Args:
        username: Username from DynamoDB SK (USER#{username})
        encrypted_credentials: KMS-encrypted credentials blob (base64)
        iam_user_arn: ARN of the created IAM user
        correlation_id: Correlation ID for tracing

    Returns:
        dict: Result containing success status and update details
    """
    subsegment = xray_recorder.begin_subsegment('store_credentials')
    if subsegment is None:
        raise RuntimeError("Failed to create X-Ray subsegment for DynamoDB storage")

    try:
        subsegment.put_annotation('correlation_id', correlation_id)
        subsegment.put_annotation('username', username)

        # Generate ISO 8601 timestamp
        updated_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

        logger.info(json.dumps({
            "message": "Storing encrypted credentials in DynamoDB",
            "correlation_id": correlation_id,
            "username": username,
            "table_name": DYNAMODB_TABLE_NAME
        }))

        # Update DynamoDB record
        try:
            response = dynamodb.update_item(
                TableName=DYNAMODB_TABLE_NAME,
                Key={
                    'PK': {'S': 'SMTP_USER'},
                    'SK': {'S': f'USER#{username}'}
                },
                UpdateExpression='SET #status = :status, encrypted_credentials = :creds, iam_user_arn = :arn, updated_at = :updated',
                ExpressionAttributeNames={
                    '#status': 'status'
                },
                ExpressionAttributeValues={
                    ':status': {'S': 'active'},
                    ':creds': {'S': encrypted_credentials},
                    ':arn': {'S': iam_user_arn},
                    ':updated': {'S': updated_at}
                },
                ReturnValues='ALL_NEW'
            )

            logger.info(json.dumps({
                "message": "Successfully stored credentials in DynamoDB",
                "correlation_id": correlation_id,
                "username": username,
                "status": "active",
                "updated_at": updated_at
            }))

            subsegment.put_annotation('stored', True)
            xray_recorder.end_subsegment()

            return {
                "success": True,
                "username": username,
                "status": "active",
                "updated_at": updated_at,
                "correlation_id": correlation_id
            }

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            logger.error(json.dumps({
                "message": "Failed to store credentials in DynamoDB",
                "correlation_id": correlation_id,
                "username": username,
                "error": str(e),
                "error_code": error_code
            }))

            subsegment.put_annotation('error', True)
            xray_recorder.end_subsegment()

            return {
                "success": False,
                "error": f"DynamoDB storage failed: {error_code} - {str(e)}",
                "correlation_id": correlation_id
            }

    except Exception as e:
        subsegment.put_annotation('error', True)
        logger.error(json.dumps({
            "message": "Unexpected error in store_credentials_in_dynamodb",
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
