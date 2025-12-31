"""
SES Email Router Enrichment Lambda Function

This Lambda function enriches SES email events with routing decisions by:
1. Receiving SES events from SNS topic
2. Performing hierarchical DynamoDB lookups for routing rules
3. Normalizing email addresses (removing +tag for plus addressing)
4. Analysing DMARC and security headers from SES receipt
5. Publishing enriched events to EventBridge Event Bus for handler dispatch

Invoked by SNS topic subscription (preserves X-Ray tracing context).
"""

from functools import lru_cache
import json
import os
from typing import Dict, Any, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError
from aws_lambda_powertools import Logger

# Configure structured JSON logging
logger = Logger(service="ses-mail-router-enrichment")

# Environment configuration
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'unknown')
EVENT_BUS_NAME = f"ses-mail-email-routing-{ENVIRONMENT}"

# Initialize AWS clients
dynamodb = boto3.client('dynamodb')
cloudwatch = boto3.client('cloudwatch')
eventbridge = boto3.client('events')
ssm = boto3.client('ssm')
s3 = boto3.client('s3')

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
patch_all()

# Cache for integration test token (loaded once at cold start in test environment)
_integration_test_token = None


def _load_integration_test_token() -> Optional[str]:
    """
    Load integration test bypass token from SSM Parameter Store.
    Cached for Lambda lifetime.

    Returns:
        str: Token value or None if not available
    """
    global _integration_test_token

    if _integration_test_token is not None:
        return _integration_test_token

    try:
        parameter_name = f'/ses-mail/{ENVIRONMENT}/integration-test-token'
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        _integration_test_token = response['Parameter']['Value']
        logger.info("Loaded integration test token from SSM")
        return _integration_test_token
    except Exception as e:
        logger.error("Failed to load integration test token", extra={"error": str(e)})
        return None


def extract_s3_info(ses_message: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract S3 bucket and object key from SES receipt action.

    Args:
        ses_message: SES event message

    Returns:
        Tuple of (bucket_name, object_key) or (None, None) if not found
    """
    try:
        action = ses_message.get('receipt', {}).get('action', {})
        if action.get('type') == 'S3':
            bucket = action.get('bucketName')
            key = action.get('objectKey')
            if bucket and key:
                return bucket, key
    except Exception as e:
        logger.warning("Failed to extract S3 info", extra={"error": str(e)})
    return None, None


def sanitize_tag_value(value: Any, max_length: int = 256) -> str:
    """
    Sanitize value for AWS S3 tag compliance.
    Allowed characters: a-z, A-Z, 0-9, space, + - = . _ : / @

    Args:
        value: Value to sanitize (converted to string)
        max_length: Maximum allowed length (AWS limit: 256 chars)

    Returns:
        Sanitized string value safe for S3 tags
    """
    if value is None:
        return "(empty)"

    # Convert to string
    value_str = str(value)
    if not value_str:
        return "(empty)"

    # Define allowed characters
    allowed_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 +-=._:/@')

    # Filter to only allowed characters, replace invalid with underscore
    sanitized = ''.join(c if c in allowed_chars else '_' for c in value_str)

    # Collapse multiple consecutive underscores/spaces
    import re
    sanitized = re.sub(r'[_\s]+', '_', sanitized)
    sanitized = sanitized.strip('_').strip()

    # Truncate to max_length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length-3] + "..."

    return sanitized or "(empty)"


def tag_s3_object(bucket: str, key: str, routing_tags: Dict[str, str]) -> None:
    """
    Apply routing metadata tags to S3 object.

    Note: S3 objects created by SES have no tags by default, so we don't need
    to merge with existing tags - just set our tags directly.

    Args:
        bucket: S3 bucket name
        key: S3 object key
        routing_tags: Dictionary of routing metadata tags (already sanitized)

    Raises:
        ClientError: If S3 API call fails (except NoSuchKey which is handled)
    """
    try:
        # Convert to S3 TagSet format
        tag_set = [{'Key': k, 'Value': v} for k, v in routing_tags.items()]

        # Apply tags directly (no need to get existing tags first)
        s3.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={'TagSet': tag_set}
        )

        logger.info("Successfully tagged S3 object", extra={
            "bucket": bucket,
            "key": key,
            "tagCount": len(tag_set)
        })

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code')

        if error_code == 'NoSuchKey':
            # Object was deleted (likely by Gmail forwarder) - this is normal, not an error
            logger.info("S3 object already deleted, skipping tagging", extra={
                "bucket": bucket,
                "key": key
            })
            return

        # For any other error, log and re-raise
        logger.error("S3 tagging failed", extra={
            "bucket": bucket,
            "key": key,
            "errorCode": error_code,
            "error": str(e)
        })
        raise


def publish_s3_tagging_failure_metric() -> None:
    """Publish CloudWatch metric when S3 tagging fails."""
    try:
        cloudwatch.put_metric_data(
            Namespace=f'SESMail/{ENVIRONMENT}',
            MetricData=[{
                'MetricName': 'S3TaggingFailures',
                'Value': 1,
                'Unit': 'Count',
                'StorageResolution': 60
            }]
        )
    except Exception as e:
        logger.exception("Error publishing S3 tagging failure metric", extra={"error": str(e)})


def lambda_handler(event, context):
    """
    Lambda handler for enriching SES email events with routing decisions.

    This function is invoked by SNS when SES receives an email.
    It processes the SES event, determines routing actions, and publishes
    enriched events to EventBridge Event Bus for handler dispatch.

    Args:
        event: SNS event containing SES message
        context: Lambda context object

    Returns:
        dict: Success response
    """
    logger.info("Received SNS event for enrichment", extra={"event": event})

    if not DYNAMODB_TABLE_NAME:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable must be set")

    success_count = 0
    failure_count = 0
    events_to_publish = []

    # SNS sends events as Records array
    for record in event.get('Records', []):
        if record.get('EventSource') != 'aws:sns':
            logger.warning("Skipping non-SNS record", extra={"eventSource": record.get('EventSource')})
            continue

        try:
            # Extract SES message from SNS
            sns_message = json.loads(record['Sns']['Message'])
            ses_message_id = sns_message.get('mail', {}).get('messageId', 'unknown')

            logger.info("Processing SES message", extra={"messageId": ses_message_id})

            # Determine routing actions for all recipients
            routing_results = decide_action(sns_message)

            # Aggregate actions by type
            actions = {
                "store": {"count": 0, "targets": []},
                "forward-to-gmail": {"count": 0, "targets": []},
                "bounce": {"count": 0, "targets": []},
            }

            for action_type, dest in routing_results:
                actions[action_type]["count"] += 1
                if dest:
                    actions[action_type]["targets"].append(dest)

            # Create enriched event for EventBridge
            enriched_event = {
                'Source': 'ses.email.router',
                'DetailType': 'Email Routing Decision',
                'Detail': json.dumps({
                    'originalMessageId': ses_message_id,
                    'ses': sns_message,
                    'actions': actions,
                }),
                'EventBusName': EVENT_BUS_NAME
            }

            events_to_publish.append(enriched_event)
            success_count += 1

        except Exception as e:
            logger.exception("Error enriching SNS record", extra={"error": str(e)})
            failure_count += 1

    # Publish enriched events to EventBridge Event Bus
    if events_to_publish:
        # Log details of each event being published (shows fields EventBridge rules filter on)
        for event in events_to_publish:
            detail = json.loads(event['Detail'])
            actions = detail.get('actions', {})
            logger.info("Publishing event to EventBridge", extra={
                "messageId": detail.get('originalMessageId', 'unknown'),
                "forward_to_gmail_count": actions.get('forward-to-gmail', {}).get('count', 0),
                "bounce_count": actions.get('bounce', {}).get('count', 0),
                "store_count": actions.get('store', {}).get('count', 0)
            })

        try:
            response = eventbridge.put_events(Entries=events_to_publish)

            # Check for failed entries
            if response.get('FailedEntryCount', 0) > 0:
                logger.error("Failed to publish events to EventBridge", extra={
                    "failedEntryCount": response['FailedEntryCount']
                })
                for entry in response.get('Entries', []):
                    if 'ErrorCode' in entry:
                        logger.error("EventBridge entry error", extra={
                            "errorCode": entry['ErrorCode'],
                            "errorMessage": entry.get('ErrorMessage', 'No message')
                        })
                failure_count += response['FailedEntryCount']
            else:
                logger.info("Successfully published events to EventBridge", extra={
                    "eventCount": len(events_to_publish)
                })

        except Exception as e:
            logger.exception("Error publishing events to EventBridge", extra={
                "error": str(e),
                "eventCount": len(events_to_publish)
            })
            failure_count += len(events_to_publish)
            raise

    # Publish custom metrics for success/failure rates
    publish_metrics(success_count, failure_count)

    logger.info("Completed enrichment", extra={
        "successCount": success_count,
        "failureCount": failure_count
    })

    return {
        'statusCode': 200,
        'body': json.dumps({
            'success': success_count,
            'failures': failure_count
        })
    }


def decide_action(ses_message: Dict[str, Any]) -> List[Tuple[str, Optional[Any]]]:
    """
    Decide routing action for a single SES event.

    Args:
        ses_message: SES event message
    Returns:
        List of tuples: [(action, destination), ...]
    """
    subsegment = xray_recorder.begin_subsegment('email_enrichment')
    if subsegment is None:
        raise RuntimeError("Failed to create X-Ray subsegment for enrichment")

    # Extract email metadata for logging
    mail = ses_message.get('mail', {})
    message_id = mail.get('messageId', 'unknown')
    source = mail.get('source', 'unknown')
    subject = extract_subject(ses_message, max_length=64)

    # Extract S3 info for tagging (do this once per message)
    ses_message_s3_bucket, ses_message_s3_key = extract_s3_info(ses_message)

    subsegment.put_annotation('messageId', message_id)
    subsegment.put_annotation('source', source)

    bounce = check_spam(ses_message)
    results = []
    counts = {
        "forward-to-gmail": 0,
        "store": 0,
        "bounce": 0,
    }

    for target in ses_message["receipt"]["recipients"]:
        if bounce:
            results.append(('bounce', {"target": target, "reason": "security"}))
            # Log routing decision for bounced emails
            logger.info("Routing decision", extra={
                "messageId": message_id,
                "sender": source,
                "subject": subject,
                "recipient": target,
                "action": "bounce",
                "lookupKey": None,
                "target": target,
                "reason": "security"
            })
        else:
            routing_decision, destination, lookup_key = get_routing_decision(target)
            if routing_decision == 'bounce':
                results.append((routing_decision, {"target": target, "reason": "policy"}))
            else:
                results.append((routing_decision, {"target": target, "destination": destination}))

            # Log routing decision with full context
            logger.info("Routing decision", extra={
                "messageId": message_id,
                "sender": source,
                "subject": subject,
                "recipient": target,
                "action": routing_decision,
                "lookupKey": lookup_key,
                "target": destination if routing_decision == 'forward-to-gmail' else target
            })
        counts[results[-1][0]] += 1

    # Tag S3 object with routing metadata (non-blocking, best-effort)
    # This happens AFTER routing decisions are made and logged
    if ses_message_s3_bucket and ses_message_s3_key:
        # Collect all recipient addresses (space-separated)
        recipients = ses_message["receipt"]["recipients"]
        all_recipients = ' '.join(recipients)

        # Collect all actions and targets (space-separated, same order as recipients)
        all_actions = []
        all_targets = []
        for action_type, destination_info in results:
            all_actions.append(action_type)
            target_value = destination_info.get('destination') or destination_info.get('target', '')
            all_targets.append(target_value)

        actions_str = ' '.join(all_actions)
        targets_str = ' '.join(all_targets)

        # Create routing tags (all values pre-sanitized)
        # AWS S3 limit: maximum 10 tags per object
        # Current: 6 routing tags + 4 terraform default tags = 10 tags (at limit)
        routing_tags = {
            'messageId': sanitize_tag_value(message_id),
            'sender': sanitize_tag_value(source),
            'subject': sanitize_tag_value(subject),
            'recipient': sanitize_tag_value(all_recipients),
            'action': sanitize_tag_value(actions_str),
            'target': sanitize_tag_value(targets_str),
            # Terraform default tags (for cost accounting)
            'Project': 'ses-mail',
            'ManagedBy': 'terraform',
            'Environment': ENVIRONMENT,
            'Application': f'ses-mail-{ENVIRONMENT}'
        }

        # Tag the S3 object (non-blocking)
        try:
            tag_s3_object(ses_message_s3_bucket, ses_message_s3_key, routing_tags)
        except Exception as tag_error:
            logger.error("Failed to tag S3 object", extra={
                "error": str(tag_error),
                "bucket": ses_message_s3_bucket,
                "key": ses_message_s3_key
            })
            # Don't fail the lambda - tagging is best-effort
            publish_s3_tagging_failure_metric()

    subsegment.put_annotation('recipient_count', len(results))
    for key, value in counts.items():
        subsegment.put_annotation(key.replace('-', '_'), value)
    xray_recorder.end_subsegment()
    return results


def get_routing_decision(recipient: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Determine routing decision for a recipient using hierarchical DynamoDB lookup.

    Lookup order:
    1. Exact match (e.g., user+tag@example.com)
    2. Normalized match (e.g., user@example.com)
    3. Domain wildcard (e.g., *@example.com)
    4. Global wildcard (e.g., *)
    Args:
        recipient: Email address to look up
    Returns:
        tuple: (routing action, target destination, lookup key)
    """
    logger.info("Looking up routing rule for recipient", extra={"recipient": recipient})

    # Generate lookup keys in hierarchical order
    lookup_keys = generate_lookup_keys(recipient)

    # Try each lookup key in order
    for lookup_key in lookup_keys:
        rule = lookup_routing_rule(lookup_key)
        if rule:
            logger.info("Found matching rule", extra={"lookupKey": lookup_key})

            # Check if rule is enabled
            if not rule.get('enabled', True):
                logger.info("Rule is disabled, continuing search", extra={"lookupKey": lookup_key})
                continue

            action = rule.get('action', 'bounce')
            target = rule.get('target', None)
            # Return action, target, and lookupKey (removed "Routing decision" log from here)
            return action, target, lookup_key

    # No rule found - default to store
    logger.warning("No routing rule found, defaulting to store", extra={"recipient": recipient})
    return 'store', None, None

def check_spam(ses_message: Dict[str, Any]) -> bool:
    """
    Check if the email is marked as spam based on SES receipt verdicts.

    Args:
        ses_message: SES event message
    Returns:
        bool: True if email is spam, False otherwise
    """
    # Skip spam checks for integration test emails
    # Requires secret token in X-Integration-Test-Token header that matches SSM parameter
    expected_token = _load_integration_test_token()
    if expected_token:
        # Look for X-Integration-Test-Token header in email
        headers = ses_message.get('mail', {}).get('headers', [])
        for header in headers:
            if header.get('name', '').lower() == 'x-integration-test-token':
                provided_token = header.get('value', '')
                if provided_token == expected_token:
                    logger.info("Valid integration test token found - skipping spam checks")
                    return False
                else:
                    logger.warning("Invalid integration test token provided")
                    break

    receipt = ses_message['receipt']
    if receipt["spamVerdict"]["status"].lower() == "fail":
        return True
    if receipt["virusVerdict"]["status"].lower() == "fail":
        return True
    if receipt["dkimVerdict"]["status"].lower() == "fail":
        return True
    if receipt["spfVerdict"]["status"].lower() == "fail":
        return True
    if receipt["dmarcVerdict"]["status"].lower() == "fail" and receipt["dmarcPolicy"] == "reject":
        return True

    return False



def generate_lookup_keys(email_address: str) -> List[str]:
    """
    Generate DynamoDB lookup keys in hierarchical order.

    Args:
        email_address: Email address to generate keys for

    Returns:
        list: Lookup keys in order of specificity (most to least specific)
    """
    keys = []

    # 1. Exact match
    keys.append(f"ROUTE#{email_address}")

    # 2. Normalized match (remove +tag)
    normalized = normalize_email_address(email_address)
    if normalized != email_address:
        keys.append(f"ROUTE#{normalized}")

    # 3. Domain wildcard
    if '@' in email_address:
        domain = email_address.split('@')[1]
        keys.append(f"ROUTE#*@{domain}")

    # 4. Global wildcard
    keys.append("ROUTE#*")

    return keys


def normalize_email_address(email_address: str) -> str:
    """
    Normalize email address by removing +tag (plus addressing).

    Example: user+tag@example.com -> user@example.com

    Args:
        email_address: Email address to normalize

    Returns:
        str: Normalized email address
    """
    if '@' not in email_address:
        return email_address

    local_part, domain = email_address.split('@', 1)

    # Remove +tag from local part
    if '+' in local_part:
        local_part = local_part.split('+')[0]

    return f"{local_part}@{domain}"


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


# Cached result
@lru_cache(maxsize=32)
def lookup_routing_rule(route_key: str) -> Optional[Dict[str, Any]]:
    """
    Look up a routing rule in DynamoDB.

    Args:
        route_key: DynamoDB partition key (e.g., "ROUTE#user@example.com")

    Returns:
        dict: Routing rule or None if not found
    """
    try:
        response = dynamodb.get_item(
            TableName=DYNAMODB_TABLE_NAME,
            Key={
                'PK': {'S': route_key},
                'SK': {'S': 'RULE#v1'}
            },
            ConsistentRead=False  # Eventually consistent reads are cheaper
        )

        if 'Item' not in response:
            return None

        # Convert DynamoDB item to Python dict
        item = response['Item']
        rule = {
            'recipient': item.get('recipient', {}).get('S', ''),
            'action': item.get('action', {}).get('S', 'bounce'),
            'target': item.get('target', {}).get('S', ''),
            'enabled': item.get('enabled', {}).get('BOOL', True),
            'description': item.get('description', {}).get('S', ''),
            'created_at': item.get('created_at', {}).get('S', ''),
            'updated_at': item.get('updated_at', {}).get('S', '')
        }

        return rule

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code')
        if error_code == 'ResourceNotFoundException':
            logger.error("DynamoDB table not found", extra={
                "tableName": DYNAMODB_TABLE_NAME
            })
            raise
        else:
            logger.error("DynamoDB error looking up route", extra={
                "routeKey": route_key,
                "error": str(e)
            })
            # On DynamoDB errors, return None to trigger fallback
            return None
    except Exception as e:
        logger.error("Unexpected error looking up route", extra={
            "routeKey": route_key,
            "error": str(e)
        })
        return None


def publish_metrics(success_count: int, failure_count: int) -> None:
    """
    Publish custom CloudWatch metrics for router enrichment success/failure rates.

    Args:
        success_count: Number of successfully enriched messages
        failure_count: Number of failed enrichments (using fallback)
    """
    try:
        metric_data = []

        if success_count > 0:
            metric_data.append({
                'MetricName': 'RouterEnrichmentSuccess',
                'Value': success_count,
                'Unit': 'Count',
                'StorageResolution': 60
            })

        if failure_count > 0:
            metric_data.append({
                'MetricName': 'RouterEnrichmentFailure',
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
