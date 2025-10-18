"""
SES Email Router Enrichment Lambda Function

This Lambda function enriches SES email events with routing decisions by:
1. Performing hierarchical DynamoDB lookups for routing rules
2. Normalizing email addresses (removing +tag for plus addressing)
3. Analysing DMARC and security headers from SES receipt
4. Returning enriched messages for EventBridge Event Bus routing

Used by EventBridge Pipes as an enrichment function.
"""

import json
import logging
import os
from typing import Dict, Any, List, Optional

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
        try:
            enriched = enrich_ses_event(record)
            enriched_results.append(enriched)
            success_count += 1
        except Exception as e:
            logger.error(f"Error enriching record: {str(e)}", exc_info=True)
            # On enrichment failure, create a fallback enrichment with bounce action
            fallback = create_fallback_enrichment(record, str(e))
            enriched_results.append(fallback)
            failure_count += 1

    # Publish custom metrics for success/failure rates
    publish_metrics(success_count, failure_count)

    logger.info(f"Returning {len(enriched_results)} enriched records (success: {success_count}, failures: {failure_count})")
    return enriched_results


def enrich_ses_event(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrich a single SES event record with routing decisions.

    Args:
        record: SES event record

    Returns:
        dict: Enriched message with routing decisions
    """
    # Create a subsegment for the enrichment process
    # This allows us to add annotations without the "FacadeSegments cannot be mutated" error
    subsegment = xray_recorder.begin_subsegment('email_enrichment')

    try:
        # Extract SES event from record
        # The record structure depends on how EventBridge Pipes passes it
        # It could be wrapped in Records array or be the direct SES event
        ses_event = record

        # If this is wrapped in a Records array, unwrap it
        if 'Records' in record and isinstance(record['Records'], list) and len(record['Records']) > 0:
            ses_event = record['Records'][0]

        # Extract SES mail and receipt data
        ses = ses_event.get('ses', {})
        mail = ses.get('mail', {})
        receipt = ses.get('receipt', {})

        message_id = mail.get('messageId')
        source = mail.get('source')
        destinations = mail.get('destination', [])
        common_headers = mail.get('commonHeaders', {})
        timestamp = mail.get('timestamp')

        logger.info(f"Enriching email - Message ID: {message_id}, From: {source}, To: {destinations}")

        # Add X-Ray annotations to subsegment (not facade segment)
        subsegment.put_annotation('messageId', message_id)
        subsegment.put_annotation('source', source)
        subsegment.put_annotation('environment', ENVIRONMENT)

        # Extract security verdicts
        security_verdict = {
            'spam': receipt.get('spamVerdict', {}).get('status', 'UNKNOWN'),
            'virus': receipt.get('virusVerdict', {}).get('status', 'UNKNOWN'),
            'dkim': receipt.get('dkimVerdict', {}).get('status', 'UNKNOWN'),
            'spf': receipt.get('spfVerdict', {}).get('status', 'UNKNOWN'),
            'dmarc': receipt.get('dmarcVerdict', {}).get('status', 'UNKNOWN')
        }

        logger.info(f"Security verdicts: {security_verdict}")

        # Perform routing decision for each destination
        routing_decisions = []
        for recipient in destinations:
            routing_decision = get_routing_decision(recipient, security_verdict)
            routing_decisions.append(routing_decision)

        # Add X-Ray annotations for searchability (use generic keys, not email addresses)
        if routing_decisions:
            subsegment.put_annotation('routing_action', routing_decisions[0]['action'])
            subsegment.put_annotation('recipient_count', len(routing_decisions))

        # Build enriched message
        enriched = {
            'originalEvent': record,
            'routingDecisions': routing_decisions,
            'emailMetadata': {
                'messageId': message_id,
                'source': source,
                'subject': common_headers.get('subject', ''),
                'timestamp': timestamp,
                'securityVerdict': security_verdict
            }
        }

        logger.info(f"Enrichment complete - Routing decisions: {json.dumps(routing_decisions)}")

        return enriched

    finally:
        # Always end the subsegment, even if an exception occurs
        xray_recorder.end_subsegment()


def get_routing_decision(recipient: str, security_verdict: Dict[str, str]) -> Dict[str, Any]:
    """
    Determine routing decision for a recipient using hierarchical DynamoDB lookup.

    Lookup order:
    1. Exact match (e.g., user+tag@example.com)
    2. Normalized match (e.g., user@example.com without +tag)
    3. Domain wildcard (e.g., *@example.com)
    4. Global wildcard (e.g., *)

    Args:
        recipient: Email address to look up
        security_verdict: Security analysis results

    Returns:
        dict: Routing decision with action, target, and metadata
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

            # Build routing decision from rule
            decision = {
                'recipient': recipient,
                'normalizedRecipient': normalize_email_address(recipient),
                'action': rule.get('action', 'bounce'),
                'target': rule.get('target', ''),
                'matchedRule': lookup_key,
                'ruleDescription': rule.get('description', ''),
                'securityVerdict': security_verdict
            }

            logger.info(f"Routing decision: {decision['action']} (matched rule: {lookup_key})")
            return decision

    # No rule found - default to bounce
    logger.warning(f"No routing rule found for {recipient}, defaulting to bounce")
    return {
        'recipient': recipient,
        'normalizedRecipient': normalize_email_address(recipient),
        'action': 'bounce',
        'target': '',
        'matchedRule': 'DEFAULT',
        'ruleDescription': 'No matching rule found - default bounce',
        'securityVerdict': security_verdict
    }


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


def create_fallback_enrichment(record: Dict[str, Any], error_message: str) -> Dict[str, Any]:
    """
    Create a fallback enrichment when normal enrichment fails.

    This ensures EventBridge Pipes always receives a valid enriched message,
    even when DynamoDB is unavailable or other errors occur.

    Args:
        record: Original SES record
        error_message: Error that caused enrichment to fail

    Returns:
        dict: Fallback enriched message with bounce action
    """
    logger.warning(f"Creating fallback enrichment due to error: {error_message}")

    # Try to extract basic email metadata from record
    try:
        ses_event = record
        if 'Records' in record and isinstance(record['Records'], list) and len(record['Records']) > 0:
            ses_event = record['Records'][0]

        ses = ses_event.get('ses', {})
        mail = ses.get('mail', {})
        destinations = mail.get('destination', ['unknown@unknown.com'])

        routing_decisions = [
            {
                'recipient': dest,
                'normalizedRecipient': normalize_email_address(dest),
                'action': 'bounce',
                'target': '',
                'matchedRule': 'FALLBACK',
                'ruleDescription': f'Fallback due to enrichment error: {error_message}',
                'securityVerdict': {}
            }
            for dest in destinations
        ]

        return {
            'originalEvent': record,
            'routingDecisions': routing_decisions,
            'emailMetadata': {
                'messageId': mail.get('messageId', 'unknown'),
                'source': mail.get('source', 'unknown'),
                'subject': mail.get('commonHeaders', {}).get('subject', ''),
                'timestamp': mail.get('timestamp', ''),
                'securityVerdict': {},
                'enrichmentError': error_message
            }
        }
    except Exception as e:
        logger.error(f"Error creating fallback enrichment: {str(e)}")
        # Return minimal fallback
        return {
            'originalEvent': record,
            'routingDecisions': [{
                'recipient': 'unknown@unknown.com',
                'normalizedRecipient': 'unknown@unknown.com',
                'action': 'bounce',
                'target': '',
                'matchedRule': 'FALLBACK',
                'ruleDescription': f'Critical error: {error_message}',
                'securityVerdict': {}
            }],
            'emailMetadata': {
                'messageId': 'unknown',
                'source': 'unknown',
                'subject': '',
                'timestamp': '',
                'securityVerdict': {},
                'enrichmentError': error_message
            }
        }


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
