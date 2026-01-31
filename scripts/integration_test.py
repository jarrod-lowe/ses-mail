#!/usr/bin/env python3
"""
Integration Test Script for SES Mail Pipeline

This script tests the complete email processing pipeline end-to-end:
1. Sends test emails through SES
2. Monitors pipeline progression (SNS → SQS → EventBridge Pipes → Event Bus → Handlers)
3. Verifies X-Ray trace spans across all components
4. Tests different routing scenarios (forward-to-gmail, bounce, wildcards)
5. Validates handler processing and error scenarios
6. Generates detailed test report

Usage:
    ./scripts/integration_test.py --env test
    ./scripts/integration_test.py --env test --test-type forward
    ./scripts/integration_test.py --env test --skip-cleanup
"""

import argparse
import json
import logging
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Dict, Any, List, Optional

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError as e:
    print("="*80, file=sys.stderr)
    print("INTEGRATION TEST DID NOT RUN - Missing Python dependencies", file=sys.stderr)
    print("="*80, file=sys.stderr)
    print("", file=sys.stderr)
    print(f"Missing package: {e.name}", file=sys.stderr)
    print("", file=sys.stderr)
    print("You MUST activate the virtual environment before running tests:", file=sys.stderr)
    print("", file=sys.stderr)
    print("  source .venv/bin/activate", file=sys.stderr)
    print("  python3 scripts/integration_test.py --env test", file=sys.stderr)
    print("", file=sys.stderr)
    print("If .venv doesn't exist, create it first:", file=sys.stderr)
    print("  python3 -m venv .venv", file=sys.stderr)
    print("  source .venv/bin/activate", file=sys.stderr)
    print("  pip install boto3", file=sys.stderr)
    print("", file=sys.stderr)
    print("DO NOT install packages globally - always use the venv!", file=sys.stderr)
    print("="*80, file=sys.stderr)
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class IntegrationTest:
    """Integration test runner for SES email processing pipeline."""

    def __init__(self, environment: str):
        """
        Initialize integration test.

        Args:
            environment: Environment name (test or prod)
        """
        self.environment = environment
        self.region = 'ap-southeast-2'

        # Initialize AWS clients
        self.ses = boto3.client('ses', region_name=self.region)
        self.s3 = boto3.client('s3', region_name=self.region)
        self.sqs = boto3.client('sqs', region_name=self.region)
        self.dynamodb = boto3.client('dynamodb', region_name=self.region)
        self.logs = boto3.client('logs', region_name=self.region)
        self.xray = boto3.client('xray', region_name=self.region)
        self.events = boto3.client('events', region_name=self.region)
        self.pipes = boto3.client('pipes', region_name=self.region)
        self.ssm = boto3.client('ssm', region_name=self.region)

        # Get AWS account ID
        sts = boto3.client('sts')
        self.account_id = sts.get_caller_identity()['Account']

        # Resource names
        self.table_name = f'ses-mail-email-routing-{environment}'
        # Note: No input queue - SNS invokes router lambda directly
        self.gmail_queue_name = f'ses-mail-gmail-forwarder-{environment}'
        self.bouncer_queue_name = f'ses-mail-bouncer-{environment}'
        self.gmail_dlq_name = f'ses-mail-gmail-forwarder-dlq-{environment}'
        self.bouncer_dlq_name = f'ses-mail-bouncer-dlq-{environment}'
        self.event_bus_name = f'ses-mail-email-routing-{environment}'
        self.pipe_name = f'ses-email-router-{environment}'

        # Load integration test bypass token (test environment only)
        self.integration_test_token = self._load_integration_test_token()

        # Test results
        self.results = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'environment': environment,
            'tests': []
        }

    def _load_integration_test_token(self) -> Optional[str]:
        """
        Load integration test bypass token from SSM Parameter Store.

        Returns:
            str: Token value or None if not available
        """
        try:
            parameter_name = f'/ses-mail/{self.environment}/integration-test-token'
            response = self.ssm.get_parameter(Name=parameter_name, WithDecryption=True)
            token = response['Parameter']['Value']
            logger.info("Loaded integration test bypass token from SSM")
            return token
        except ClientError as e:
            logger.error(f"Failed to load integration test token: {e}")
            logger.warning("Tests may fail due to DMARC checks without bypass token")
            return None

    def get_queue_url(self, queue_name: str) -> str:
        """Get SQS queue URL from queue name."""
        try:
            response = self.sqs.get_queue_url(QueueName=queue_name)
            return response['QueueUrl']
        except ClientError as e:
            logger.error(f"Failed to get queue URL for {queue_name}: {e}")
            raise

    def create_test_routing_rule(
        self,
        recipient: str,
        action: str,
        target: str = '',
        description: str = ''
    ) -> None:
        """
        Create a test routing rule in DynamoDB (single action).

        Args:
            recipient: Email pattern (e.g., test@example.com, *@example.com)
            action: Routing action (forward-to-gmail, bounce, store)
            target: Target email for forwarding
            description: Human-readable description
        """
        # Convert single action to actions list format
        actions = [{'type': action}]
        if target:
            actions[0]['target'] = target

        self.create_test_routing_rule_multi(recipient, actions, description)

    def create_test_routing_rule_multi(
        self,
        recipient: str,
        actions: List[Dict[str, str]],
        description: str = ''
    ) -> None:
        """
        Create a test routing rule in DynamoDB with multiple actions.

        Args:
            recipient: Email pattern (e.g., test@example.com, *@example.com)
            actions: List of action dicts, e.g., [{'type': 'forward-to-gmail', 'target': 'me@gmail.com'}, {'type': 'store'}]
            description: Human-readable description
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        # Convert actions to DynamoDB format
        actions_dynamo = []
        for action in actions:
            action_item = {'type': {'S': action['type']}}
            if 'target' in action:
                action_item['target'] = {'S': action['target']}
            actions_dynamo.append({'M': action_item})

        # Build description from actions if not provided
        if not description:
            action_str = ', '.join(a['type'] for a in actions)
            description = f'Test rule: {action_str} for {recipient}'

        item = {
            'PK': {'S': f'ROUTE#{recipient}'},
            'SK': {'S': 'RULE#v1'},
            'entity_type': {'S': 'ROUTE'},
            'recipient': {'S': recipient},
            'actions': {'L': actions_dynamo},
            'enabled': {'BOOL': True},
            'created_at': {'S': timestamp},
            'updated_at': {'S': timestamp},
            'description': {'S': description}
        }

        try:
            self.dynamodb.put_item(TableName=self.table_name, Item=item)
            action_str = ', '.join(a['type'] for a in actions)
            logger.info(f"Created test routing rule: {recipient} → {action_str}")
        except ClientError as e:
            logger.error(f"Failed to create routing rule for {recipient}: {e}")
            raise

    def delete_test_routing_rule(self, recipient: str) -> None:
        """Delete a test routing rule from DynamoDB."""
        try:
            self.dynamodb.delete_item(
                TableName=self.table_name,
                Key={
                    'PK': {'S': f'ROUTE#{recipient}'},
                    'SK': {'S': 'RULE#v1'}
                }
            )
            logger.info(f"Deleted test routing rule: {recipient}")
        except ClientError as e:
            logger.warning(f"Failed to delete routing rule for {recipient}: {e}")

    def generate_test_email(
        self,
        from_addr: str,
        to_addr: str,
        subject: str,
        body: str
    ) -> bytes:
        """
        Generate a test email in MIME format.

        Args:
            from_addr: Sender email address
            to_addr: Recipient email address
            subject: Email subject
            body: Email body text

        Returns:
            bytes: Raw email in MIME format
        """
        msg = EmailMessage()
        msg['From'] = from_addr
        msg['To'] = to_addr
        msg['Subject'] = subject
        msg['Date'] = formatdate(localtime=True)
        msg['Message-ID'] = make_msgid(domain=from_addr.split('@')[1])
        msg.set_content(body)

        return msg.as_bytes()

    def send_test_email(
        self,
        from_addr: str,
        to_addr: str,
        subject: str,
        body: str
    ) -> str:
        """
        Send a test email via SMTP to SES MX endpoint to trigger receipt rules.

        This sends email TO the SES domain (triggering receipt rules) rather than
        FROM SES (which would just send outbound email).

        Args:
            from_addr: Sender email address
            to_addr: Recipient email address (must be on verified SES domain)
            subject: Email subject
            body: Email body text

        Returns:
            str: SES-generated message ID (found in S3)
        """
        # Generate email with proper headers
        msg = EmailMessage()
        msg['From'] = from_addr
        msg['To'] = to_addr
        msg['Subject'] = subject
        msg['Date'] = formatdate(localtime=True)

        # Add integration test bypass token header (test environment only)
        if self.integration_test_token:
            msg['X-Integration-Test-Token'] = self.integration_test_token
            logger.debug("Added integration test bypass token to email header")

        # Create unique message ID that we can track
        domain = to_addr.split('@')[1]
        msg['Message-ID'] = make_msgid(domain=domain)
        msg.set_content(body)

        # Extract message ID (without angle brackets)
        header_message_id = msg['Message-ID'].strip('<>')

        # Get MX endpoint for the region
        mx_endpoint = f"inbound-smtp.{self.region}.amazonaws.com"

        # Get S3 bucket name
        s3 = boto3.client('s3', region_name=self.region)
        bucket_name = f"ses-mail-storage-{self.account_id}-{self.environment}"

        try:
            logger.info(f"Connecting to SMTP endpoint: {mx_endpoint}:25")

            # List S3 objects before sending to establish baseline
            response_before = s3.list_objects_v2(
                Bucket=bucket_name,
                Prefix='emails/',
                MaxKeys=1000
            )
            keys_before = set(obj['Key'] for obj in response_before.get('Contents', []))

            # Connect to SES MX endpoint on port 25
            with smtplib.SMTP(mx_endpoint, 25, timeout=30) as smtp:
                smtp.set_debuglevel(0)  # Set to 1 for verbose SMTP debugging
                smtp.ehlo()

                logger.info(f"Sending email: {from_addr} → {to_addr}")
                logger.info(f"Message-ID header: {header_message_id}")

                # Send the email
                smtp.sendmail(from_addr, [to_addr], msg.as_string())

            logger.info(f"Successfully sent email via SMTP")

            # Wait a moment for SES to process and store in S3
            time.sleep(3)

            # List S3 objects again to find the new email
            response_after = s3.list_objects_v2(
                Bucket=bucket_name,
                Prefix='emails/',
                MaxKeys=1000
            )
            keys_after = set(obj['Key'] for obj in response_after.get('Contents', []))

            # Find new S3 object
            new_keys = keys_after - keys_before
            if new_keys:
                # Get SES message ID from S3 key (format: emails/{messageId})
                ses_message_id = list(new_keys)[0].replace('emails/', '')
                logger.info(f"SES Message ID: {ses_message_id}")
                return ses_message_id
            else:
                logger.warning("No new S3 object found, returning header message ID")
                return header_message_id

        except smtplib.SMTPException as e:
            logger.error(f"SMTP error sending test email: {e}")
            raise RuntimeError(f"Failed to send email via SMTP: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending test email: {e}")
            raise

    def wait_for_queue_message(
        self,
        queue_url: str,
        timeout_seconds: int = 60,
        expected_message_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Wait for a message to appear in an SQS queue.

        Args:
            queue_url: SQS queue URL
            timeout_seconds: Maximum time to wait
            expected_message_id: Optional SES message ID to match

        Returns:
            dict: Message body or None if timeout
        """
        logger.info(f"Waiting for message in queue (timeout: {timeout_seconds}s)...")
        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            try:
                response = self.sqs.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=5,
                    AttributeNames=['All'],
                    MessageAttributeNames=['All']
                )

                messages = response.get('Messages', [])
                if messages:
                    for message in messages:
                        body = json.loads(message['Body'])

                        # If checking for specific message ID, verify it
                        if expected_message_id:
                            # Try to extract message ID from different message formats
                            msg_id = self._extract_message_id(body)
                            if msg_id == expected_message_id:
                                logger.info(f"Found matching message: {expected_message_id}")
                                return message
                        else:
                            # Return first message if not looking for specific one
                            logger.info(f"Found message in queue")
                            return message

                time.sleep(2)
            except Exception as e:
                logger.error(f"Error receiving message: {e}")
                time.sleep(2)

        logger.warning(f"Timeout waiting for message in queue")
        return None

    def _extract_message_id(self, message_body: Dict[str, Any]) -> Optional[str]:
        """Extract SES message ID from various message body formats."""
        # New EventBridge format (simplified router structure)
        # EventBridge wraps router output in 'detail'
        if 'detail' in message_body:
            detail = message_body['detail']
            # Try originalMessageId from new router structure
            if 'originalMessageId' in detail:
                return detail.get('originalMessageId')
            # Try old emailMetadata format for backward compatibility
            if 'emailMetadata' in detail:
                return detail['emailMetadata'].get('messageId')

        # Old EventBridge format (enriched message) - for backward compatibility
        if 'emailMetadata' in message_body:
            return message_body['emailMetadata'].get('messageId')

        # Try originalMessageId at top level (new router output before EventBridge wrapping)
        if 'originalMessageId' in message_body:
            return message_body.get('originalMessageId')

        # SNS format
        if 'Message' in message_body:
            try:
                msg = json.loads(message_body['Message'])
                if 'mail' in msg:
                    return msg['mail'].get('messageId')
            except:
                pass

        # Direct SES format
        if 'mail' in message_body:
            return message_body['mail'].get('messageId')

        # Records array format
        if 'Records' in message_body:
            records = message_body['Records']
            if records and 'ses' in records[0]:
                return records[0]['ses']['mail'].get('messageId')

        return None

    def check_dlq_messages(self, queue_url: str) -> int:
        """
        Check if dead letter queue has any messages.

        Args:
            queue_url: DLQ URL

        Returns:
            int: Number of messages in DLQ
        """
        try:
            response = self.sqs.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=['ApproximateNumberOfMessages']
            )
            count = int(response['Attributes']['ApproximateNumberOfMessages'])
            if count > 0:
                logger.warning(f"DLQ has {count} messages")
            return count
        except Exception as e:
            logger.error(f"Error checking DLQ: {e}")
            return 0

    def verify_s3_tags(
        self,
        bucket: str,
        key: str,
        expected_values: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Verify S3 object tags match expected values.

        Args:
            bucket: S3 bucket name
            key: S3 object key
            expected_values: Expected tag key-value pairs to verify

        Returns:
            dict: Verification results with status and details
        """
        try:
            response = self.s3.get_object_tagging(Bucket=bucket, Key=key)
            actual_tags = {tag['Key']: tag['Value'] for tag in response.get('TagSet', [])}

            verification = {
                'found': True,
                'tag_count': len(actual_tags),
                'expected_tags': {},
                'unexpected_tags': {},
                'missing_tags': [],
                'all_match': True
            }

            # Check each expected tag
            for tag_key, expected_value in expected_values.items():
                if tag_key in actual_tags:
                    matches = actual_tags[tag_key] == expected_value
                    verification['expected_tags'][tag_key] = {
                        'expected': expected_value,
                        'actual': actual_tags[tag_key],
                        'matches': matches
                    }
                    if not matches:
                        verification['all_match'] = False
                else:
                    verification['missing_tags'].append(tag_key)
                    verification['all_match'] = False

            # Check for unexpected tags
            for tag_key in actual_tags:
                if tag_key not in expected_values:
                    verification['unexpected_tags'][tag_key] = actual_tags[tag_key]

            return verification

        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == 'NoSuchKey':
                return {
                    'found': False,
                    'error': 'S3 object not found',
                    'all_match': False
                }
            return {
                'found': False,
                'error': str(e),
                'all_match': False
            }

    def get_router_logs(
        self,
        message_id: str,
        since_minutes: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Get router enrichment lambda logs for a specific message.

        Args:
            message_id: SES message ID to search for
            since_minutes: How far back to search

        Returns:
            list: Log events containing the message ID
        """
        log_group = f'/aws/lambda/ses-mail-router-enrichment-{self.environment}'

        try:
            start_time = int((time.time() - (since_minutes * 60)) * 1000)
            end_time = int(time.time() * 1000)

            response = self.logs.filter_log_events(
                logGroupName=log_group,
                startTime=start_time,
                endTime=end_time,
                filterPattern=f'"{message_id}"'
            )

            events = response.get('events', [])
            logger.info(f"Found {len(events)} router log events for message {message_id}")
            return events
        except Exception as e:
            logger.error(f"Error getting router logs: {e}")
            return []

    def get_handler_logs(
        self,
        handler_name: str,
        message_id: str,
        since_minutes: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Get handler lambda logs for a specific message.

        Args:
            handler_name: Handler name ('gmail-forwarder' or 'bouncer')
            message_id: SES message ID to search for
            since_minutes: How far back to search

        Returns:
            list: Log events containing the message ID
        """
        log_group = f'/aws/lambda/ses-mail-{handler_name}-{self.environment}'

        try:
            start_time = int((time.time() - (since_minutes * 60)) * 1000)
            end_time = int(time.time() * 1000)

            # Don't use filterPattern - it doesn't reliably match across all log content
            # Instead, fetch all logs in time range and filter in Python
            response = self.logs.filter_log_events(
                logGroupName=log_group,
                startTime=start_time,
                endTime=end_time
            )

            # Filter events that contain the message ID
            all_events = response.get('events', [])
            events = [e for e in all_events if message_id in e.get('message', '')]

            logger.info(f"Found {len(events)} {handler_name} log events for message {message_id}")
            return events
        except Exception as e:
            logger.error(f"Error getting {handler_name} logs: {e}")
            return []

    def wait_for_handler_success(
        self,
        handler_name: str,
        message_id: str,
        success_pattern: str,
        timeout_seconds: int = 60
    ) -> bool:
        """
        Wait for handler lambda to successfully process a message.

        Args:
            handler_name: Handler name ('gmail-forwarder' or 'bouncer')
            message_id: SES message ID to search for
            success_pattern: Log pattern indicating success (e.g., 'Successfully imported')
            timeout_seconds: Maximum time to wait

        Returns:
            bool: True if handler succeeded, False otherwise
        """
        logger.info(f"Waiting for {handler_name} to process message (timeout: {timeout_seconds}s)...")

        start_time = time.time()
        log_group = f'/aws/lambda/ses-mail-{handler_name}-{self.environment}'

        while time.time() - start_time < timeout_seconds:
            # Get logs containing the message ID
            logs_with_msg_id = self.get_handler_logs(handler_name, message_id, since_minutes=2)

            if logs_with_msg_id:
                # Extract Request ID from first log that contains message ID
                # Log stream name format: YYYY/MM/DD/[$LATEST]request-id
                # Or we can extract from log message which contains RequestId
                first_log = logs_with_msg_id[0]
                log_stream_name = first_log.get('logStreamName', '')

                # Get ALL logs from the same log stream (same lambda invocation)
                try:
                    stream_start = int((time.time() - (2 * 60)) * 1000)
                    stream_end = int(time.time() * 1000)

                    response = self.logs.filter_log_events(
                        logGroupName=log_group,
                        logStreamNames=[log_stream_name],
                        startTime=stream_start,
                        endTime=stream_end
                    )

                    all_invocation_logs = response.get('events', [])

                    # Check if ANY log in this invocation contains the success pattern
                    for log_event in all_invocation_logs:
                        message = log_event.get('message', '')
                        if success_pattern.lower() in message.lower():
                            logger.info(f"{handler_name} successfully processed message: {message.strip()}")
                            return True

                except Exception as e:
                    logger.error(f"Error fetching logs from stream {log_stream_name}: {e}")

            logger.debug(f"Handler not finished yet, retrying... ({int(time.time() - start_time)}s elapsed)")
            time.sleep(5)  # Check every 5 seconds

        logger.warning(f"Timeout waiting for {handler_name} to process message")
        return False

    def wait_for_xray_trace(
        self,
        message_id: str,
        timeout_seconds: int = 120
    ) -> Optional[Dict[str, Any]]:
        """
        Wait for X-Ray trace to become available.

        X-Ray traces can take 30-60 seconds to appear after processing.

        Args:
            message_id: SES message ID to find in trace annotations
            timeout_seconds: Maximum time to wait

        Returns:
            dict: Trace data or None if not found
        """
        logger.info(f"Waiting for X-Ray trace (timeout: {timeout_seconds}s)...")
        logger.info("Note: X-Ray traces can take 30-60 seconds to become available")

        start_time = time.time()

        # Start searching from when the email was sent
        filter_start = datetime.now(timezone.utc).timestamp() - 300  # 5 minutes ago

        while time.time() - start_time < timeout_seconds:
            try:
                # Search for traces using filter expression
                # Note: X-Ray annotation queries can be case-sensitive
                response = self.xray.get_trace_summaries(
                    StartTime=datetime.fromtimestamp(filter_start, tz=timezone.utc),
                    EndTime=datetime.now(timezone.utc),
                    FilterExpression=f'annotation.messageId = "{message_id}"'
                )

                summaries = response.get('TraceSummaries', [])
                if summaries:
                    # Get detailed trace
                    trace_id = summaries[0]['Id']
                    logger.info(f"Found X-Ray trace: {trace_id}")

                    trace_response = self.xray.batch_get_traces(
                        TraceIds=[trace_id]
                    )

                    if trace_response.get('Traces'):
                        return trace_response['Traces'][0]

                logger.debug(f"No trace found yet, retrying... ({int(time.time() - start_time)}s elapsed)")
                time.sleep(10)  # Check every 10 seconds
            except Exception as e:
                logger.error(f"Error searching for X-Ray trace: {e}")
                time.sleep(10)

        logger.warning(f"Timeout waiting for X-Ray trace")
        return None

    def verify_trace_segments(self, trace: Dict[str, Any]) -> Dict[str, bool]:
        """
        Verify X-Ray trace contains expected segments.

        Args:
            trace: X-Ray trace data

        Returns:
            dict: Verification results for each expected segment
        """
        segments = trace.get('Segments', [])

        expected_segments = {
            'SNS': False,
            'SQS': False,
            'EventBridge Pipes': False,
            'Router Lambda': False,
            'Event Bus': False,
            'Handler Queue': False,
            'Handler Lambda': False
        }

        for segment_data in segments:
            try:
                segment = json.loads(segment_data['Document'])
                name = segment.get('name', '')

                # Check for expected service names
                if 'sns' in name.lower() or segment.get('origin') == 'AWS::SNS':
                    expected_segments['SNS'] = True
                elif 'sqs' in name.lower() or segment.get('origin') == 'AWS::SQS':
                    expected_segments['SQS'] = True
                elif 'pipes' in name.lower():
                    expected_segments['EventBridge Pipes'] = True
                elif 'router' in name.lower() or 'enrichment' in name.lower():
                    expected_segments['Router Lambda'] = True
                elif 'eventbridge' in name.lower() or 'events' in name.lower():
                    expected_segments['Event Bus'] = True
                elif 'gmail' in name.lower() or 'bouncer' in name.lower():
                    expected_segments['Handler Lambda'] = True
            except Exception as e:
                logger.warning(f"Error parsing segment: {e}")

        return expected_segments

    def test_forward_to_gmail(
        self,
        from_addr: str,
        to_addr: str,
        gmail_target: str
    ) -> Dict[str, Any]:
        """
        Test the forward-to-gmail routing action.

        Args:
            from_addr: Sender email address
            to_addr: Recipient email address
            gmail_target: Gmail address to forward to

        Returns:
            dict: Test results
        """
        test_name = "Forward to Gmail"
        logger.info(f"\n{'='*60}")
        logger.info(f"Test: {test_name}")
        logger.info(f"{'='*60}")

        result = {
            'name': test_name,
            'status': 'FAIL',
            'details': {},
            'errors': []
        }

        try:
            # 1. Create routing rule
            logger.info("Step 1: Creating test routing rule...")
            self.create_test_routing_rule(
                recipient=to_addr,
                action='forward-to-gmail',
                target=gmail_target,
                description=f'Integration test: forward to {gmail_target}'
            )
            result['details']['routing_rule_created'] = True

            # 2. Send test email
            logger.info("Step 2: Sending test email...")
            subject = f"Integration Test - Forward to Gmail - {int(time.time())}"
            body = f"This is an integration test email.\nTimestamp: {datetime.now(timezone.utc).isoformat()}"

            message_id = self.send_test_email(from_addr, to_addr, subject, body)
            result['details']['message_id'] = message_id
            result['details']['email_sent'] = True

            # 3. Wait for router processing (SNS invokes router lambda directly)
            logger.info("Step 3: Waiting for router enrichment...")
            time.sleep(10)  # Give SES → SNS → Router Lambda time to process
            router_logs = self.get_router_logs(message_id, since_minutes=2)
            if router_logs:
                result['details']['router_processed'] = True
                # Look for routing decision in logs
                for log in router_logs:
                    if 'forward-to-gmail' in log.get('message', ''):
                        result['details']['routing_decision'] = 'forward-to-gmail'
                        break
            else:
                result['errors'].append("Router logs not found")

            # 4. Wait for message in gmail forwarder queue
            logger.info("Step 4: Checking Gmail forwarder queue...")
            gmail_queue_url = self.get_queue_url(self.gmail_queue_name)
            time.sleep(10)  # Give Router Lambda → Event Bus → Queue time

            gmail_message = self.wait_for_queue_message(gmail_queue_url, timeout_seconds=30)
            if gmail_message:
                result['details']['gmail_queue_received'] = True
            else:
                result['errors'].append("Message not found in gmail forwarder queue")

            # 5. Wait for Gmail forwarder lambda to successfully process the message
            logger.info("Step 5: Waiting for Gmail forwarder lambda to process message...")
            gmail_success = self.wait_for_handler_success(
                handler_name='gmail-forwarder',
                message_id=message_id,
                success_pattern='Successfully imported to Gmail',
                timeout_seconds=60
            )
            if gmail_success:
                result['details']['gmail_handler_success'] = True
                # If handler succeeded, clear the queue check error (message was already consumed)
                result['errors'] = [e for e in result['errors'] if 'not found in gmail forwarder queue' not in e.lower()]
            else:
                result['errors'].append("Gmail forwarder lambda did not successfully process message")

            # 6. Check DLQs
            logger.info("Step 6: Checking dead letter queues...")
            gmail_dlq_url = self.get_queue_url(self.gmail_dlq_name)
            dlq_count = self.check_dlq_messages(gmail_dlq_url)
            result['details']['dlq_messages'] = dlq_count
            if dlq_count > 0:
                result['errors'].append(f"Found {dlq_count} messages in DLQ")

            # 7. Wait for X-Ray trace
            logger.info("Step 7: Retrieving X-Ray trace...")
            trace = self.wait_for_xray_trace(message_id, timeout_seconds=90)
            if trace:
                result['details']['xray_trace_found'] = True
                result['details']['trace_id'] = trace.get('Id')

                # Verify trace segments
                segment_verification = self.verify_trace_segments(trace)
                result['details']['trace_segments'] = segment_verification

                # Check if all expected segments are present
                missing_segments = [k for k, v in segment_verification.items() if not v]
                if missing_segments:
                    result['errors'].append(f"Missing trace segments: {', '.join(missing_segments)}")
            else:
                result['errors'].append("X-Ray trace not found")

            # Determine overall status
            if not result['errors']:
                result['status'] = 'PASS'

        except Exception as e:
            logger.error(f"Test failed with exception: {e}", exc_info=True)
            result['errors'].append(str(e))

        return result

    def test_bounce(
        self,
        from_addr: str,
        to_addr: str
    ) -> Dict[str, Any]:
        """
        Test the bounce routing action.

        Args:
            from_addr: Sender email address
            to_addr: Recipient email address

        Returns:
            dict: Test results
        """
        test_name = "Bounce Email"
        logger.info(f"\n{'='*60}")
        logger.info(f"Test: {test_name}")
        logger.info(f"{'='*60}")

        result = {
            'name': test_name,
            'status': 'FAIL',
            'details': {},
            'errors': []
        }

        try:
            # 1. Create routing rule
            logger.info("Step 1: Creating test routing rule...")
            self.create_test_routing_rule(
                recipient=to_addr,
                action='bounce',
                target='',
                description='Integration test: bounce email'
            )
            result['details']['routing_rule_created'] = True

            # 2. Send test email
            logger.info("Step 2: Sending test email...")
            subject = f"Integration Test - Bounce - {int(time.time())}"
            body = f"This email should be bounced.\nTimestamp: {datetime.now(timezone.utc).isoformat()}"

            message_id = self.send_test_email(from_addr, to_addr, subject, body)
            result['details']['message_id'] = message_id
            result['details']['email_sent'] = True

            # 3. Wait for processing
            logger.info("Step 3: Waiting for pipeline processing...")
            time.sleep(15)

            # 4. Check router logs
            logger.info("Step 4: Checking router logs...")
            router_logs = self.get_router_logs(message_id, since_minutes=2)
            if router_logs:
                result['details']['router_processed'] = True
                for log in router_logs:
                    if 'bounce' in log.get('message', ''):
                        result['details']['routing_decision'] = 'bounce'
                        break

            # 5. Wait for message in bouncer queue
            logger.info("Step 5: Checking bouncer queue...")
            bouncer_queue_url = self.get_queue_url(self.bouncer_queue_name)
            time.sleep(10)

            bouncer_message = self.wait_for_queue_message(bouncer_queue_url, timeout_seconds=30)
            if bouncer_message:
                result['details']['bouncer_queue_received'] = True
            else:
                result['errors'].append("Message not found in bouncer queue")

            # 6. Wait for bouncer lambda to successfully process the message
            logger.info("Step 6: Waiting for bouncer lambda to process message...")
            bouncer_success = self.wait_for_handler_success(
                handler_name='bouncer',
                message_id=message_id,
                success_pattern='Bounce sent successfully',
                timeout_seconds=60
            )
            if bouncer_success:
                result['details']['bouncer_handler_success'] = True
                # If handler succeeded, clear the queue check error (message was already consumed)
                result['errors'] = [e for e in result['errors'] if 'not found in bouncer queue' not in e.lower()]
            else:
                result['errors'].append("Bouncer lambda did not successfully process message")

            # 7. Check DLQs
            logger.info("Step 7: Checking dead letter queues...")
            bouncer_dlq_url = self.get_queue_url(self.bouncer_dlq_name)
            dlq_count = self.check_dlq_messages(bouncer_dlq_url)
            result['details']['dlq_messages'] = dlq_count
            if dlq_count > 0:
                result['errors'].append(f"Found {dlq_count} messages in DLQ")

            # 8. Wait for X-Ray trace
            logger.info("Step 8: Retrieving X-Ray trace...")
            trace = self.wait_for_xray_trace(message_id, timeout_seconds=90)
            if trace:
                result['details']['xray_trace_found'] = True
                result['details']['trace_id'] = trace.get('Id')

                segment_verification = self.verify_trace_segments(trace)
                result['details']['trace_segments'] = segment_verification
            else:
                result['errors'].append("X-Ray trace not found")

            # Determine overall status
            if not result['errors']:
                result['status'] = 'PASS'

        except Exception as e:
            logger.error(f"Test failed with exception: {e}", exc_info=True)
            result['errors'].append(str(e))

        return result

    def test_store(
        self,
        from_addr: str,
        to_addr: str
    ) -> Dict[str, Any]:
        """
        Test the store routing action and verify S3 tags.

        Args:
            from_addr: Sender email address
            to_addr: Recipient email address

        Returns:
            dict: Test results including S3 tag verification
        """
        test_name = "Store Email with S3 Tags"
        logger.info(f"\n{'='*60}")
        logger.info(f"Test: {test_name}")
        logger.info(f"{'='*60}")

        result = {
            'name': test_name,
            'status': 'FAIL',
            'details': {},
            'errors': []
        }

        try:
            # 1. Create routing rule
            logger.info("Step 1: Creating test routing rule...")
            self.create_test_routing_rule(
                recipient=to_addr,
                action='store',
                target='',
                description='Integration test: store email and verify S3 tags'
            )
            result['details']['routing_rule_created'] = True

            # 2. Send test email with special characters in subject for sanitization testing
            logger.info("Step 2: Sending test email...")
            subject = f"Integration Test! @Store #Action - {int(time.time())}"
            body = f"This email should be stored with S3 tags.\nTimestamp: {datetime.now(timezone.utc).isoformat()}"

            message_id = self.send_test_email(from_addr, to_addr, subject, body)
            result['details']['message_id'] = message_id
            result['details']['email_sent'] = True

            # 3. Wait for router processing
            logger.info("Step 3: Waiting for router enrichment...")
            time.sleep(15)

            # 4. Check router logs for routing decision
            logger.info("Step 4: Checking router logs...")
            router_logs = self.get_router_logs(message_id, since_minutes=2)
            if router_logs:
                result['details']['router_processed'] = True
                for log in router_logs:
                    if 'store' in log.get('message', ''):
                        result['details']['routing_decision'] = 'store'
                        break

            # 5. Verify S3 object exists
            logger.info("Step 5: Verifying S3 object exists...")
            bucket = f"ses-mail-storage-{self.account_id}-{self.environment}"
            s3_key = f"emails/{message_id}"

            try:
                self.s3.head_object(Bucket=bucket, Key=s3_key)
                result['details']['s3_object_exists'] = True
                result['details']['s3_bucket'] = bucket
                result['details']['s3_key'] = s3_key
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code')
                result['errors'].append(f"S3 object not found: {error_code}")
                return result

            # 6. Verify S3 tags
            logger.info("Step 6: Verifying S3 object tags...")

            # Expected tags based on what router lambda should set
            # Note: Tag values are sanitized using the same logic as the lambda
            # Allowed chars: a-z, A-Z, 0-9, space, + - = . _ : / @
            # Invalid chars replaced with underscore, consecutive underscores/spaces collapsed
            import re
            allowed_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 +-=._:/@')
            sanitized_subject = ''.join(c if c in allowed_chars else '_' for c in subject)
            sanitized_subject = re.sub(r'[_\s]+', '_', sanitized_subject)
            sanitized_subject = sanitized_subject.strip('_').strip()
            if len(sanitized_subject) > 64:
                sanitized_subject = sanitized_subject[:61] + "..."

            expected_tags = {
                'messageId': message_id,
                'sender': from_addr,
                'subject': sanitized_subject,
                'recipient': to_addr,
                'action': 'store',
                'target': to_addr,  # For store action, target equals recipient
                'Project': 'ses-mail',
                'ManagedBy': 'terraform',
                'Environment': self.environment,
                'Application': f'ses-mail-{self.environment}'
            }

            tag_verification = self.verify_s3_tags(bucket, s3_key, expected_tags)
            result['details']['tag_verification'] = tag_verification

            if not tag_verification.get('all_match', False):
                result['errors'].append("S3 tags do not match expected values")

                # Log detailed tag mismatches for debugging
                if tag_verification.get('missing_tags'):
                    logger.error(f"Missing tags: {tag_verification['missing_tags']}")

                for tag_key, tag_info in tag_verification.get('expected_tags', {}).items():
                    if not tag_info.get('matches', True):
                        logger.error(f"Tag mismatch - {tag_key}: expected='{tag_info['expected']}', actual='{tag_info['actual']}'")

            # 7. Wait for X-Ray trace (optional, for consistency with other tests)
            logger.info("Step 7: Retrieving X-Ray trace...")
            trace = self.wait_for_xray_trace(message_id, timeout_seconds=90)
            if trace:
                result['details']['xray_trace_found'] = True
                result['details']['trace_id'] = trace.get('Id')

                segment_verification = self.verify_trace_segments(trace)
                result['details']['trace_segments'] = segment_verification
            else:
                result['errors'].append("X-Ray trace not found")

            # 8. Determine overall status
            if not result['errors']:
                result['status'] = 'PASS'

            # Log S3 object details for manual verification
            logger.info(f"\nS3 Object Details:")
            logger.info(f"  Bucket: {bucket}")
            logger.info(f"  Key: {s3_key}")
            logger.info(f"  Tag Count: {tag_verification.get('tag_count', 0)}")
            logger.info(f"  All Tags Match: {tag_verification.get('all_match', False)}")

        except Exception as e:
            logger.error(f"Test failed with exception: {e}", exc_info=True)
            result['errors'].append(str(e))

        return result

    def test_multi_action(
        self,
        from_addr: str,
        to_addr: str,
        gmail_target: str
    ) -> Dict[str, Any]:
        """
        Test multi-action routing rule (forward-to-gmail + store).

        Args:
            from_addr: Sender email address
            to_addr: Recipient email address
            gmail_target: Gmail address for forwarding

        Returns:
            dict: Test results
        """
        test_name = "Multi-Action Rule (forward-to-gmail + store)"
        logger.info(f"\n{'='*60}")
        logger.info(f"Test: {test_name}")
        logger.info(f"{'='*60}")

        result = {
            'name': test_name,
            'status': 'FAIL',
            'details': {},
            'errors': []
        }

        try:
            # 1. Create multi-action routing rule
            logger.info("Step 1: Creating multi-action routing rule...")
            self.create_test_routing_rule_multi(
                recipient=to_addr,
                actions=[
                    {'type': 'forward-to-gmail', 'target': gmail_target},
                    {'type': 'store'}
                ],
                description='Integration test: forward-to-gmail + store'
            )
            result['details']['routing_rule_created'] = True

            # 2. Send test email
            logger.info("Step 2: Sending test email...")
            subject = f"Integration Test - Multi-Action - {int(time.time())}"
            body = f"This email should be forwarded to Gmail AND stored.\nTimestamp: {datetime.now(timezone.utc).isoformat()}"

            message_id = self.send_test_email(from_addr, to_addr, subject, body)
            result['details']['message_id'] = message_id
            result['details']['email_sent'] = True

            # 3. Wait for router processing
            logger.info("Step 3: Waiting for router enrichment...")
            time.sleep(10)
            router_logs = self.get_router_logs(message_id, since_minutes=2)
            if router_logs:
                result['details']['router_processed'] = True
                # Look for both actions in logs
                found_forward = False
                found_store = False
                for log in router_logs:
                    msg = log.get('message', '')
                    if 'forward-to-gmail' in msg:
                        found_forward = True
                    if 'store' in msg:
                        found_store = True
                result['details']['found_forward_action'] = found_forward
                result['details']['found_store_action'] = found_store
            else:
                result['errors'].append("Router logs not found")

            # 4. Wait for Gmail forwarder to process
            logger.info("Step 4: Waiting for Gmail forwarder...")
            gmail_success = self.wait_for_handler_success(
                handler_name='gmail-forwarder',
                message_id=message_id,
                success_pattern='Successfully imported to Gmail',
                timeout_seconds=60
            )
            if gmail_success:
                result['details']['gmail_handler_success'] = True
            else:
                result['errors'].append("Gmail forwarder lambda did not successfully process message")

            # 5. Verify S3 object exists (from store action)
            logger.info("Step 5: Verifying S3 object exists...")
            bucket = f"ses-mail-storage-{self.account_id}-{self.environment}"
            s3_key = f"emails/{message_id}"

            try:
                self.s3.head_object(Bucket=bucket, Key=s3_key)
                result['details']['s3_object_exists'] = True
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code')
                result['errors'].append(f"S3 object not found: {error_code}")

            # 6. Verify S3 tags show multiple actions
            logger.info("Step 6: Verifying S3 tags show multiple actions...")
            tag_verification = self.verify_s3_tags(bucket, s3_key, {
                'messageId': message_id,
            })
            result['details']['tag_verification'] = tag_verification

            if tag_verification.get('found'):
                # Get actual action tag
                response = self.s3.get_object_tagging(Bucket=bucket, Key=s3_key)
                tags_dict = {t['Key']: t['Value'] for t in response.get('TagSet', [])}
                action_tag = tags_dict.get('action', '')
                result['details']['action_tag'] = action_tag

                # Check that action tag contains both actions
                if 'forward-to-gmail' in action_tag and 'store' in action_tag:
                    result['details']['multi_action_tag_verified'] = True
                else:
                    result['errors'].append(f"Action tag missing expected values: {action_tag}")

            # 7. Check DLQs
            logger.info("Step 7: Checking dead letter queues...")
            gmail_dlq_url = self.get_queue_url(self.gmail_dlq_name)
            dlq_count = self.check_dlq_messages(gmail_dlq_url)
            result['details']['dlq_messages'] = dlq_count
            if dlq_count > 0:
                result['errors'].append(f"Found {dlq_count} messages in DLQ")

            # 8. Determine overall status
            if not result['errors']:
                result['status'] = 'PASS'

        except Exception as e:
            logger.error(f"Test failed with exception: {e}", exc_info=True)
            result['errors'].append(str(e))

        return result

    def cleanup_test_rules(self, recipients: List[str]) -> None:
        """Clean up test routing rules."""
        logger.info("\nCleaning up test routing rules...")
        for recipient in recipients:
            self.delete_test_routing_rule(recipient)

    def generate_report(self) -> None:
        """Generate and display test report."""
        logger.info("\n" + "="*60)
        logger.info("INTEGRATION TEST REPORT")
        logger.info("="*60)
        logger.info(f"Environment: {self.results['environment']}")
        logger.info(f"Timestamp: {self.results['timestamp']}")
        logger.info(f"Total Tests: {len(self.results['tests'])}")

        passed = sum(1 for t in self.results['tests'] if t['status'] == 'PASS')
        failed = sum(1 for t in self.results['tests'] if t['status'] == 'FAIL')

        logger.info(f"Passed: {passed}")
        logger.info(f"Failed: {failed}")
        logger.info("="*60)

        for test in self.results['tests']:
            logger.info(f"\nTest: {test['name']}")
            logger.info(f"Status: {test['status']}")

            if test['details']:
                logger.info("Details:")
                for key, value in test['details'].items():
                    logger.info(f"  - {key}: {value}")

            if test['errors']:
                logger.info("Errors:")
                for error in test['errors']:
                    logger.info(f"  - {error}")

        logger.info("\n" + "="*60)

        # Save report to file
        report_file = f"integration_test_report_{self.environment}_{int(time.time())}.json"
        with open(report_file, 'w') as f:
            json.dump(self.results, f, indent=2)
        logger.info(f"Detailed report saved to: {report_file}")

    def run_all_tests(
        self,
        from_addr: str,
        test_domain: str,
        gmail_target: str,
        skip_cleanup: bool = False
    ) -> bool:
        """
        Run all integration tests.

        Args:
            from_addr: Sender email address (must be verified in SES)
            test_domain: Domain for test emails
            gmail_target: Gmail address for forwarding tests
            skip_cleanup: Skip cleanup of test routing rules

        Returns:
            bool: True if all tests passed
        """
        logger.info("Starting integration tests...")
        logger.info(f"From: {from_addr}")
        logger.info(f"Test domain: {test_domain}")
        logger.info(f"Gmail target: {gmail_target}")

        test_recipients = []

        # Test 1: Forward to Gmail
        to_addr = f"test-forward@{test_domain}"
        test_recipients.append(to_addr)
        result = self.test_forward_to_gmail(from_addr, to_addr, gmail_target)
        self.results['tests'].append(result)

        # Clean up test 1 rule only if test completed (successfully or not)
        # This ensures the rule exists during the entire test execution
        if not skip_cleanup:
            self.delete_test_routing_rule(to_addr)

        # Test 2: Bounce
        to_addr = f"test-bounce@{test_domain}"
        test_recipients.append(to_addr)
        result = self.test_bounce(from_addr, to_addr)
        self.results['tests'].append(result)

        # Clean up test 2 rule only if test completed (successfully or not)
        if not skip_cleanup:
            self.delete_test_routing_rule(to_addr)

        # Test 3: Store with S3 tag verification
        to_addr = f"test-store@{test_domain}"
        test_recipients.append(to_addr)
        result = self.test_store(from_addr, to_addr)
        self.results['tests'].append(result)

        # Clean up test 3 rule (but S3 object is left for manual inspection)
        if not skip_cleanup:
            self.delete_test_routing_rule(to_addr)
            # Note: S3 object is intentionally NOT deleted for manual verification

        # Test 4: Multi-action (forward-to-gmail + store)
        to_addr = f"test-multi-action@{test_domain}"
        test_recipients.append(to_addr)
        result = self.test_multi_action(from_addr, to_addr, gmail_target)
        self.results['tests'].append(result)

        # Clean up test 4 rule
        if not skip_cleanup:
            self.delete_test_routing_rule(to_addr)

        # Generate report
        self.generate_report()

        # Return True if all tests passed
        all_passed = all(t['status'] == 'PASS' for t in self.results['tests'])
        return all_passed


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Integration test for SES email processing pipeline'
    )
    parser.add_argument(
        '--env',
        required=True,
        choices=['test', 'prod'],
        help='Environment to test'
    )
    parser.add_argument(
        '--from',
        dest='from_addr',
        required=True,
        help='Sender email address (must be verified in SES)'
    )
    parser.add_argument(
        '--test-domain',
        required=True,
        help='Domain for test recipient addresses'
    )
    parser.add_argument(
        '--gmail-target',
        required=True,
        help='Gmail address for forwarding tests'
    )
    parser.add_argument(
        '--skip-cleanup',
        action='store_true',
        help='Skip cleanup of test routing rules'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Run tests
    tester = IntegrationTest(args.env)
    success = tester.run_all_tests(
        from_addr=args.from_addr,
        test_domain=args.test_domain,
        gmail_target=args.gmail_target,
        skip_cleanup=args.skip_cleanup
    )

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
