"""
JMAP Deliverer Lambda Function

This Lambda function is triggered by SQS messages from the jmap-deliverer queue.
It processes enriched email events from EventBridge and delivers emails to a JMAP
service using the Blob/allocate and Email/import JMAP methods.

The JMAP API uses IAM authentication (SigV4 signing) for authorization.
"""

import json
import os
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError

# X-Ray SDK for distributed tracing
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
patch_all()

# Configure structured JSON logging
from aws_lambda_powertools import Logger
logger = Logger(service="ses-mail-jmap-deliverer")

# Environment configuration
JMAP_API_URL_PARAMETER = os.environ.get('JMAP_API_URL_PARAMETER')
EMAIL_BUCKET = os.environ.get('EMAIL_BUCKET')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'unknown')
S3_PREFIX = 'emails'  # Hardcoded to match ses.tf configuration

# X-Ray HTTP metadata keys
XRAY_HTTP_URL = "url"
XRAY_HTTP_METHOD = "method"
XRAY_HTTP_STATUS = "status"

# Initialize AWS clients
s3_client = boto3.client('s3')
ssm_client = boto3.client('ssm')
cloudwatch = boto3.client('cloudwatch')

# Cached JMAP API URL
_jmap_api_url = None


def get_jmap_api_url() -> str:
    """
    Load the JMAP API URL from SSM Parameter Store.
    Cached for Lambda lifetime.

    Returns:
        str: JMAP API Gateway URL
    """
    global _jmap_api_url

    if _jmap_api_url is not None:
        return _jmap_api_url

    if not JMAP_API_URL_PARAMETER:
        raise RuntimeError("JMAP_API_URL_PARAMETER environment variable must be set")

    try:
        response = ssm_client.get_parameter(Name=JMAP_API_URL_PARAMETER)
        _jmap_api_url = response['Parameter']['Value']
        logger.info("Loaded JMAP API URL from SSM", extra={"url": _jmap_api_url})
        return _jmap_api_url
    except ClientError as e:
        logger.error("Error retrieving JMAP API URL from SSM", extra={"error": str(e)})
        raise RuntimeError(f"Failed to load JMAP API URL from SSM: {e}")


def sign_request(method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> dict:
    """
    Sign an HTTP request using AWS SigV4.

    Args:
        method: HTTP method (GET, POST, PUT)
        url: Full URL to sign
        body: Request body bytes (optional)
        headers: Additional headers to include

    Returns:
        dict: Headers with SigV4 signature
    """
    session = boto3.Session()
    credentials = session.get_credentials()
    region = session.region_name or 'ap-southeast-2'

    # Create AWS request for signing
    aws_request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers=headers or {}
    )

    # Sign the request
    SigV4Auth(credentials, 'execute-api', region).add_auth(aws_request)

    return dict(aws_request.headers)


def make_jmap_request(account_id: str, method_calls: list, using: list) -> dict:
    """
    Make a JMAP API request with SigV4 authentication.

    Args:
        account_id: JMAP account ID to operate on
        method_calls: List of JMAP method calls
        using: List of JMAP capabilities to use

    Returns:
        dict: JMAP response

    Raises:
        RuntimeError: If the request fails
    """
    api_url = get_jmap_api_url()
    url = f"{api_url}/jmap-iam/{account_id}"

    request_body = {
        "using": using,
        "methodCalls": method_calls
    }
    body_bytes = json.dumps(request_body).encode('utf-8')

    headers = {
        'Content-Type': 'application/json',
    }

    # Sign the request
    signed_headers = sign_request('POST', url, body_bytes, headers)

    # Create X-Ray subsegment for JMAP API call
    subsegment = xray_recorder.begin_subsegment('jmap-api')

    try:
        if subsegment:
            subsegment.namespace = 'remote'
            subsegment.put_http_meta(XRAY_HTTP_URL, url)
            subsegment.put_http_meta(XRAY_HTTP_METHOD, 'POST')

        request = Request(url, data=body_bytes, headers=signed_headers, method='POST')
        with urlopen(request, timeout=30) as response:
            response_body = response.read().decode('utf-8')
            result = json.loads(response_body)

            if subsegment:
                subsegment.put_http_meta(XRAY_HTTP_STATUS, response.status)

            return result

    except HTTPError as e:
        status_code = e.code
        error_body = e.read().decode('utf-8') if e.fp else ''

        if subsegment:
            subsegment.put_http_meta(XRAY_HTTP_STATUS, status_code)
            subsegment.put_annotation('error', True)

        logger.error("JMAP API HTTP error", extra={
            "status_code": status_code,
            "error_body": error_body[:500]
        })

        raise RuntimeError(f"JMAP API error {status_code}: {error_body[:200]}")

    except URLError as e:
        if subsegment:
            subsegment.put_annotation('error', True)
            subsegment.put_annotation('error_type', 'URLError')

        logger.error("JMAP API connection error", extra={"error": str(e)})
        raise RuntimeError(f"JMAP API connection error: {e}")

    finally:
        xray_recorder.end_subsegment()


def upload_blob_stream(presigned_url: str, email_stream, size: int) -> None:
    """
    Upload email stream to the presigned S3 URL from Blob/allocate.
    Streams directly from S3 without loading entire email into memory.

    Args:
        presigned_url: Presigned URL for uploading
        email_stream: File-like object (S3 StreamingBody) containing email bytes
        size: Size of the email in bytes (for Content-Length header)

    Raises:
        RuntimeError: If upload fails
    """
    subsegment = xray_recorder.begin_subsegment('blob-upload')

    try:
        if subsegment:
            subsegment.namespace = 'remote'
            subsegment.put_http_meta(XRAY_HTTP_URL, presigned_url.split('?')[0])  # Strip query params for logging
            subsegment.put_http_meta(XRAY_HTTP_METHOD, 'PUT')
            subsegment.put_annotation('blob_size', size)

        headers = {
            'Content-Type': 'message/rfc822',
            'Content-Length': str(size)
        }

        # urllib.request supports file-like objects for streaming upload
        request = Request(presigned_url, data=email_stream, headers=headers, method='PUT')
        with urlopen(request, timeout=60) as response:
            if subsegment:
                subsegment.put_http_meta(XRAY_HTTP_STATUS, response.status)

            logger.info("Blob uploaded successfully", extra={
                "status": response.status,
                "size": size
            })

    except HTTPError as e:
        if subsegment:
            subsegment.put_http_meta(XRAY_HTTP_STATUS, e.code)
            subsegment.put_annotation('error', True)

        error_body = e.read().decode('utf-8') if e.fp else ''
        logger.error("Blob upload HTTP error", extra={
            "status_code": e.code,
            "error_body": error_body[:500]
        })
        raise RuntimeError(f"Blob upload error {e.code}: {error_body[:200]}")

    except URLError as e:
        if subsegment:
            subsegment.put_annotation('error', True)
            subsegment.put_annotation('error_type', 'URLError')

        logger.error("Blob upload connection error", extra={"error": str(e)})
        raise RuntimeError(f"Blob upload connection error: {e}")

    finally:
        xray_recorder.end_subsegment()


def deliver_to_jmap(account_id: str, email_size: int, email_stream, mailbox_ids: list[str]) -> dict:
    """
    Deliver an email to a JMAP account using Blob/allocate and Email/import.
    Streams the email directly from S3 to JMAP without loading into memory.

    Args:
        account_id: JMAP account ID
        email_size: Size of the email in bytes
        email_stream: File-like object (S3 StreamingBody) containing email bytes
        mailbox_ids: List of mailbox IDs or names to deliver to

    Returns:
        dict: Email/import result with email ID

    Raises:
        RuntimeError: If delivery fails
    """
    # Step 1: Allocate a blob
    logger.info("Allocating blob for email", extra={
        "account_id": account_id,
        "size": email_size
    })

    allocate_response = make_jmap_request(
        account_id=account_id,
        using=["urn:ietf:params:jmap:core", "https://jmap.rrod.net/extensions/upload-put"],
        method_calls=[
            ["Blob/allocate", {
                "accountId": account_id,
                "create": {
                    "blob0": {
                        "type": "message/rfc822",
                        "size": email_size
                    }
                }
            }, "c0"]
        ]
    )

    # Extract blob info from response
    method_responses = allocate_response.get('methodResponses', [])
    if not method_responses:
        raise RuntimeError("Empty response from Blob/allocate")

    allocate_result = method_responses[0]
    if allocate_result[0] == 'error':
        raise RuntimeError(f"Blob/allocate error: {allocate_result[1]}")

    # Response has created map: {"created": {"blob0": {"blobId": ..., "url": ...}}}
    result_data = allocate_result[1]
    created = result_data.get('created') or {}
    not_created = result_data.get('notCreated') or {}

    if 'blob0' in not_created:
        raise RuntimeError(f"Blob/allocate failed: {not_created['blob0']}")

    blob_info = created.get('blob0', {})
    blob_id = blob_info.get('id')  # Response uses 'id' not 'blobId'
    upload_url = blob_info.get('url')

    if not blob_id or not upload_url:
        raise RuntimeError(f"Invalid Blob/allocate response: {result_data}")

    logger.info("Blob allocated", extra={
        "blob_id": blob_id,
        "has_upload_url": bool(upload_url)
    })

    # Step 2: Upload email stream to presigned URL (no memory copy)
    upload_blob_stream(upload_url, email_stream, email_size)

    # Step 3: Import email using the blob
    # Convert mailbox_ids list to the JMAP format {mailboxId: true, ...}
    mailbox_ids_map = {mid: True for mid in mailbox_ids}

    received_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    logger.info("Importing email", extra={
        "account_id": account_id,
        "blob_id": blob_id,
        "mailbox_ids": mailbox_ids
    })

    import_response = make_jmap_request(
        account_id=account_id,
        using=["urn:ietf:params:jmap:mail"],
        method_calls=[
            ["Email/import", {
                "accountId": account_id,
                "emails": {
                    "e0": {
                        "blobId": blob_id,
                        "mailboxIds": mailbox_ids_map,
                        "receivedAt": received_at
                    }
                }
            }, "c0"]
        ]
    )

    # Extract import result
    method_responses = import_response.get('methodResponses', [])
    if not method_responses:
        raise RuntimeError("Empty response from Email/import")

    import_result = method_responses[0]
    if import_result[0] == 'error':
        raise RuntimeError(f"Email/import error: {import_result[1]}")

    # Check for created email
    created = import_result[1].get('created', {})
    not_created = import_result[1].get('notCreated', {})

    if 'e0' in not_created:
        error_info = not_created['e0']
        raise RuntimeError(f"Email/import failed: {error_info}")

    if 'e0' not in created:
        raise RuntimeError(f"Email/import returned unexpected result: {import_result[1]}")

    email_info = created['e0']
    email_id = email_info.get('id')

    logger.info("Email imported successfully", extra={
        "email_id": email_id,
        "blob_id": blob_id
    })

    return {
        'email_id': email_id,
        'blob_id': blob_id
    }


def get_email_stream_from_s3(message_id: str) -> tuple[int, Any]:
    """
    Get email size and streaming body from S3 for the given SES messageId.
    Returns a stream that can be directly uploaded without loading into memory.

    Args:
        message_id: SES message ID

    Returns:
        tuple: (content_length, streaming_body)
            - content_length: Size of the email in bytes
            - streaming_body: File-like S3 StreamingBody object
    """
    if not EMAIL_BUCKET:
        raise RuntimeError("EMAIL_BUCKET environment variable must be set")

    s3_key = f"{S3_PREFIX}/{message_id}"

    try:
        logger.info("Getting email stream from S3", extra={
            "bucket": EMAIL_BUCKET,
            "key": s3_key
        })
        obj = s3_client.get_object(Bucket=EMAIL_BUCKET, Key=s3_key)
        return obj['ContentLength'], obj['Body']
    except ClientError as e:
        raise RuntimeError(f"Failed to get email from S3: {e}")


def extract_subject(ses_message: dict, max_length: int = 64) -> str:
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
                return subject[:max_length] if len(subject) > max_length else subject
    return '(no subject)'


def lambda_handler(event, context):
    """
    Lambda handler for processing enriched email messages from SQS.
    Fetches emails from S3 and delivers them to JMAP accounts.

    Args:
        event: SQS event containing enriched email messages
        context: Lambda context object

    Returns:
        dict: Response with results for each processed message
    """
    _ = context  # Unused but required by Lambda handler signature
    logger.info("Received SQS event", extra={"messageCount": len(event.get('Records', []))})

    results = []
    success_count = 0
    failure_count = 0

    # Process each SQS record
    records = event.get('Records', [])
    for record in records:
        result = process_sqs_record(record)
        results.append(result)

        if result.get('status') == 'ok':
            success_count += 1
        else:
            failure_count += 1

    # Publish custom metrics
    publish_metrics(success_count, failure_count)

    logger.info("Processed messages", extra={
        "totalCount": len(results),
        "successCount": success_count,
        "failureCount": failure_count
    })

    return {
        'statusCode': HTTPStatus.OK,
        'batchItemFailures': [
            {'itemIdentifier': r['receiptHandle']}
            for r in results
            if r.get('status') == 'error'
        ]
    }


def process_sqs_record(record) -> dict:
    """
    Process a single SQS record containing an enriched email message.

    Args:
        record: SQS record from the event

    Returns:
        dict: Result with messageId, jmap results, and status
    """
    receipt_handle = record.get('receiptHandle')
    subsegment = xray_recorder.begin_subsegment('process_jmap_delivery')

    message_id = None
    source = None
    ses_message = None
    subject = None

    try:
        # Parse SQS message body (enriched EventBridge message)
        body = json.loads(record.get('body', '{}'))
        detail = body.get('detail', body)

        message_id = detail.get('originalMessageId')
        actions = detail.get('actions', {})
        jmap_action = actions.get('deliver-to-jmap', {})
        targets = jmap_action.get('targets', [])

        if not targets:
            raise ValueError("No deliver-to-jmap targets found in enriched message")

        logger.info("Decision Info", extra=detail)
        ses_message = detail.get('ses')

        ses_mail = ses_message.get('mail', {})
        source = ses_mail.get('source', 'unknown@unknown.com')
        subject = ses_mail.get('commonHeaders', {}).get('subject', '(no subject)')

        if not message_id:
            raise ValueError("Missing originalMessageId in enriched message")

        # Process each target
        # Note: We fetch from S3 for each target because the stream can only be
        # consumed once. This maintains memory efficiency while supporting multiple targets.
        delivery_results = []
        for target_info in targets:
            recipient = target_info.get('target')  # Original recipient email
            account_id = target_info.get('destination')  # JMAP account ID
            mailbox_ids = target_info.get('mailboxIds', ['inbox'])

            if subsegment:
                subsegment.put_annotation('messageId', message_id)
                subsegment.put_annotation('source', source)
                subsegment.put_annotation('action', 'deliver-to-jmap')
                subsegment.put_annotation('recipient', recipient)
                subsegment.put_annotation('account_id', account_id)

            logger.info("Processing email delivery to JMAP",
                messageId=message_id,
                sender=source,
                recipient=recipient,
                subject=subject[:64] if subject else '(no subject)',
                targetAccountId=account_id,
                mailboxIds=mailbox_ids
            )

            # Get email stream from S3 (fetched per-target since streams are single-use)
            email_size, email_stream = get_email_stream_from_s3(message_id)
            logger.info("Got email stream from S3", extra={
                "messageId": message_id,
                "size": email_size
            })

            # Deliver to JMAP - streams directly without loading into memory
            jmap_result = deliver_to_jmap(account_id, email_size, email_stream, mailbox_ids)

            logger.info("Successfully delivered to JMAP",
                messageId=message_id,
                jmapEmailId=jmap_result.get('email_id')
            )

            # Log action result for dashboard
            logger.info("Action result", extra={
                "messageId": message_id,
                "sender": source,
                "subject": extract_subject(ses_message, max_length=64),
                "recipient": recipient,
                "action": "deliver-to-jmap",
                "result": "success",
                "target": account_id,
                "resultId": jmap_result.get('email_id')
            })

            delivery_results.append({
                'recipient': recipient,
                'account_id': account_id,
                'email_id': jmap_result.get('email_id'),
                'blob_id': jmap_result.get('blob_id')
            })

        if subsegment:
            subsegment.put_annotation('delivery_status', 'success')

        return {
            'messageId': message_id,
            'results': delivery_results,
            'status': 'ok',
            'receiptHandle': receipt_handle
        }

    except (RuntimeError, ValueError, ClientError, json.JSONDecodeError) as e:
        logger.exception("Error processing SQS message", extra={"error": str(e)})

        # Log action result for dashboard (if we have enough context)
        try:
            logger.error("Action result", extra={
                "messageId": message_id if message_id else 'unknown',
                "sender": source if source else 'unknown',
                "subject": extract_subject(ses_message, max_length=64) if ses_message else '(no subject)',
                "recipient": 'unknown',
                "action": "deliver-to-jmap",
                "result": "failure",
                "target": 'unknown',
                "error": str(e)
            })
        except Exception:
            pass

        if subsegment:
            subsegment.put_annotation('delivery_status', 'error')
            subsegment.put_annotation('error_type', type(e).__name__)

        return {
            'error': str(e),
            'status': 'error',
            'receiptHandle': receipt_handle
        }

    finally:
        xray_recorder.end_subsegment()


def publish_metrics(success_count: int, failure_count: int) -> None:
    """
    Publish custom CloudWatch metrics for JMAP delivery success/failure rates.

    Args:
        success_count: Number of successfully delivered emails
        failure_count: Number of failed deliveries
    """
    try:
        metric_data = []

        if success_count > 0:
            metric_data.append({
                'MetricName': 'JmapDeliverSuccess',
                'Value': success_count,
                'Unit': 'Count',
                'StorageResolution': 60
            })

        if failure_count > 0:
            metric_data.append({
                'MetricName': 'JmapDeliverFailure',
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
