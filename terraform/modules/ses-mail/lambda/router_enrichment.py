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
EVENT_BUS_NAME = f"ses-email-routing-{ENVIRONMENT}"

# Initialize AWS clients
dynamodb = boto3.client('dynamodb')
cloudwatch = boto3.client('cloudwatch')
eventbridge = boto3.client('events')
ssm = boto3.client('ssm')

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
patch_all()

# Cache for integration test token (loaded once at cold start in test environment)
_integration_test_token = None


def _load_integration_test_token() -> Optional[str]:
    """
    Load integration test bypass token from SSM Parameter Store.
    Only loads in test environment. Cached for Lambda lifetime.

    Returns:
        str: Token value or None if not in test environment
    """
    global _integration_test_token

    if _integration_test_token is not None:
        return _integration_test_token

    if ENVIRONMENT != 'test':
        return None

    try:
        parameter_name = f'/ses-mail/{ENVIRONMENT}/integration-test-token'
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        _integration_test_token = response['Parameter']['Value']
        logger.info("Loaded integration test token from SSM")
        return _integration_test_token
    except Exception as e:
        logger.error("Failed to load integration test token", extra={"error": str(e)})
        return None


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

    # Extract and annotate messageId and source for X-Ray indexing
    mail = ses_message.get('mail', {})
    message_id = mail.get('messageId', 'unknown')
    source = mail.get('source', 'unknown')

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
        else:
            routing_decision, destination = get_routing_decision(target)
            if routing_decision == 'bounce':
                results.append((routing_decision, {"target": target, "reason": "policy"}))
            else:
                results.append((routing_decision, {"target": target, "destination": destination}))
        counts[results[-1][0]] += 1

    subsegment.put_annotation('recipient_count', len(results))
    for key, value in counts.items():
        subsegment.put_annotation(key.replace('-', '_'), value)
    xray_recorder.end_subsegment()
    return results


def get_routing_decision(recipient: str) -> Tuple[str, Optional[str]]:
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
        tuple: (routing action, target destination)
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
            logger.info("Routing decision", extra={
                "action": action,
                "lookupKey": lookup_key,
                "target": target
            })
            return action, target

    # No rule found - default to store
    logger.warning("No routing rule found, defaulting to store", extra={"recipient": recipient})
    return 'store', None

def check_spam(ses_message: Dict[str, Any]) -> bool:
    """
    Check if the email is marked as spam based on SES receipt verdicts.

    Args:
        ses_message: SES event message
    Returns:
        bool: True if email is spam, False otherwise
    """
    # Skip spam checks for integration test emails only in test environment
    # Requires secret token in X-Integration-Test-Token header that matches SSM parameter
    if ENVIRONMENT == 'test':
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
