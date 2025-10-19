"""
SES Email Router Enrichment Lambda Function

This Lambda function enriches SES email events with routing decisions by:
1. Performing hierarchical DynamoDB lookups for routing rules
2. Normalizing email addresses (removing +tag for plus addressing)
3. Analysing DMARC and security headers from SES receipt
4. Returning enriched messages for EventBridge Event Bus routing

Used by EventBridge Pipes as an enrichment function.
"""

from functools import lru_cache
import json
import logging
import os
from typing import Dict, Any, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment configuration
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'unknown')

# Initialize AWS clients
dynamodb = boto3.client('dynamodb')
cloudwatch = boto3.client('cloudwatch')

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
patch_all()


def lambda_handler(event, context):
    """
    Lambda handler for enriching SES email events with routing decisions.

    This function is invoked by EventBridge Pipes as an enrichment step.
    It receives SES events from SQS and returns enriched messages with
    routing decisions for EventBridge Event Bus.

    Args:
        event: List of SES records from EventBridge Pipes
        context: Lambda context object

    Returns:
        list: Enriched messages with routing decisions
    """
    logger.info(f"Received event for enrichment: {json.dumps(event)}")

    if not DYNAMODB_TABLE_NAME:
        raise ValueError("DYNAMODB_TABLE_NAME environment variable must be set")

    # EventBridge Pipes sends events as a list
    # Each item is a record from SQS
    enriched_results = []
    success_count = 0
    failure_count = 0

    for record in event:
        # EventBridge Pipes expects just the Detail payload
        # Pipes will add Source and DetailType based on target configuration
        enriched_result = {
            **record,
            **{
                "originalMessageId": record["messageId"],
                "actions": {
                    "store": {
                        "count": 0,
                        "targets": [],
                    },
                    "forward-to-gmail": {
                        "count": 0,
                        "targets": [],
                    },
                    "bounce": {
                        "count": 0,
                        "targets": [],
                    },
                },
            },
        }
        del(enriched_result["messageId"])
        action = "store"
        try:
            results = decide_action(record)
            for action, dest in results:
                enriched_result["actions"][action]["count"] += 1
                if dest:
                    enriched_result["actions"][action]["targets"].append(dest)
            success_count += 1
        except Exception as e:
            logger.error(f"Error enriching record: {str(e)}", exc_info=True)
            # On enrichment failure, create a fallback enrichment with store action
            enriched_result["actions"]["store"]["count"] += 1
            failure_count += 1
        enriched_results.append(enriched_result)

    # Publish custom metrics for success/failure rates
    publish_metrics(success_count, failure_count)

    logger.info(f"Returning {len(enriched_results)} enriched records (success: {success_count}, failures: {failure_count})")
    return enriched_results


def decide_action(record: Dict[str, Any]) -> List[Tuple[str, Optional[Any]]]:
    """
    Decide routing action for a single SES event record.

    Args:
        record: SES event record
    Returns:
        str: Routing action (e.g., 'deliver', 'store', 'bounce')
    """
    subsegment = xray_recorder.begin_subsegment('email_enrichment')
    if subsegment is None:
        raise RuntimeError("Failed to create X-Ray subsegment for enrichment")

    body = json.loads(record['body'])
    bounce = check_spam(body)
    results = []
    counts = {
        "forward-to-gmail": 0,
        "store": 0,
        "bounce": 0,
    }

    for target in body["receipt"]["recipients"]:
        if bounce:
            results.append(('bounce', {"target": target}))
        else:
            routing_decision, destination = get_routing_decision(target)
            results.append((routing_decision, {"target": target, "destination": destination}))
        counts[results[-1][0]] += 1

    subsegment.put_annotation('recipient_count', len(results))
    for key, value in counts.items():
        subsegment.put_annotation(key, value)
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
    logger.info(f"Looking up routing rule for recipient: {recipient}")

    # Generate lookup keys in hierarchical order
    lookup_keys = generate_lookup_keys(recipient)

    # Try each lookup key in order
    for lookup_key in lookup_keys:
        rule = lookup_routing_rule(lookup_key)
        if rule:
            logger.info(f"Found matching rule: {lookup_key}")

            # Check if rule is enabled
            if not rule.get('enabled', True):
                logger.info(f"Rule {lookup_key} is disabled, continuing search")
                continue

            logger.info(f"Routing decision: {rule.get('action', 'bounce')} (matched rule: {lookup_key})")
            return rule.get('action', 'bounce'), rule.get('target', None)

    # No rule found - default to store
    logger.warning(f"No routing rule found for {recipient}, defaulting to store")
    return 'store', None

def check_spam(body: Dict[str, Any]) -> bool:
    """
    Check if the email is marked as spam based on SES receipt verdicts.

    Args:
        body: SES event body
    Returns:
        bool: True if email is spam, False otherwise
    """
    receipt = body['receipt']
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
            logger.error(f"DynamoDB table {DYNAMODB_TABLE_NAME} not found")
            raise
        else:
            logger.error(f"DynamoDB error looking up {route_key}: {str(e)}")
            # On DynamoDB errors, return None to trigger fallback
            return None
    except Exception as e:
        logger.error(f"Unexpected error looking up {route_key}: {str(e)}")
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
            logger.info(f"Published metrics: success={success_count}, failure={failure_count}")

    except Exception as e:
        # Don't fail the lambda if metrics publishing fails
        logger.error(f"Error publishing metrics: {str(e)}", exc_info=True)
