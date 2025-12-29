"""
Email Canary Monitor - CloudWatch Synthetics Canary

This canary sends hourly test emails through the SES inbound pipeline and monitors
end-to-end delivery latency. It validates MTA-STS configuration, sends via SMTP,
and polls DynamoDB for completion confirmation.

Runtime: syn-python-selenium-4.1 (Python 3.11)
"""

import smtplib
import time
import json
import re
import os
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

import boto3
from botocore.exceptions import ClientError

# AWS clients
ssm = boto3.client('ssm')
dynamodb = boto3.client('dynamodb')

# Environment variables (set by CloudWatch Synthetics)
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'test')
DOMAIN = os.environ.get('DOMAIN')
CANARY_TIMEOUT = 300  # 5 minutes in seconds
POLL_INTERVAL = 10  # Poll every 10 seconds
DYNAMODB_TABLE_NAME = f"ses-mail-email-routing-{ENVIRONMENT}"

# Cache for integration test token
_integration_test_token = None


# =============================================================================
# Minimal DNS Client (MX and TXT queries only)
# =============================================================================

def get_nameserver():
    """Read first nameserver from /etc/resolv.conf."""
    with open('/etc/resolv.conf', 'r') as f:
        for line in f:
            if line.startswith('nameserver'):
                return line.split()[1]
    raise Exception("No nameserver found in /etc/resolv.conf")


def encode_domain(domain):
    """Encode domain name to DNS wire format (length-prefixed labels)."""
    encoded = b''
    for label in domain.split('.'):
        encoded += bytes([len(label)]) + label.encode('ascii')
    encoded += b'\x00'  # Null terminator
    return encoded


def build_query(domain, qtype):
    """Build DNS query packet (header + question section)."""
    # Header (12 bytes)
    query_id = os.urandom(2)
    flags = b'\x01\x00'  # Standard query, recursion desired
    qdcount = b'\x00\x01'  # One question
    ancount = b'\x00\x00'  # No answers
    nscount = b'\x00\x00'  # No authority records
    arcount = b'\x00\x00'  # No additional records
    header = query_id + flags + qdcount + ancount + nscount + arcount

    # Question section
    qname = encode_domain(domain)
    qtype_bytes = qtype.to_bytes(2, 'big')
    qclass = b'\x00\x01'  # IN class
    question = qname + qtype_bytes + qclass

    return header + question


def send_query(packet):
    """Send DNS query via UDP socket with 2s timeout."""
    import socket
    nameserver = get_nameserver()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    sock.sendto(packet, (nameserver, 53))
    data, _ = sock.recvfrom(512)  # Max UDP DNS response size
    sock.close()
    return data


def parse_mx_response(data):
    """Parse MX records from DNS response. Returns list of (preference, hostname) tuples."""
    # Skip header (12 bytes) and question section
    pos = 12

    # Skip question section - read QNAME labels until null byte
    while data[pos] != 0:
        label_len = data[pos]
        pos += 1 + label_len
    pos += 1  # Skip null byte
    pos += 4  # Skip QTYPE (2) + QCLASS (2)

    # Read ANCOUNT from header (bytes 6-7)
    ancount = int.from_bytes(data[6:8], 'big')

    mx_records = []
    for _ in range(ancount):
        # Skip NAME (check for compression pointer)
        if data[pos] & 0xC0 == 0xC0:
            pos += 2  # Compression pointer (2 bytes)
        else:
            while data[pos] != 0:
                label_len = data[pos]
                pos += 1 + label_len
            pos += 1  # Skip null byte

        # Read TYPE, CLASS, TTL, RDLENGTH
        rtype = int.from_bytes(data[pos:pos+2], 'big')
        pos += 2  # TYPE
        pos += 2  # CLASS
        pos += 4  # TTL
        rdlength = int.from_bytes(data[pos:pos+2], 'big')
        pos += 2  # RDLENGTH

        if rtype == 15:  # MX record
            # Read preference (first 2 bytes of RDATA)
            preference = int.from_bytes(data[pos:pos+2], 'big')
            pos += 2

            # Read exchange hostname (rest of RDATA)
            hostname = ''
            while data[pos] != 0:
                if data[pos] & 0xC0 == 0xC0:
                    # Compression pointer - follow it
                    pointer = int.from_bytes(data[pos:pos+2], 'big') & 0x3FFF
                    temp_pos = pointer
                    while data[temp_pos] != 0:
                        label_len = data[temp_pos]
                        temp_pos += 1
                        hostname += data[temp_pos:temp_pos+label_len].decode('ascii') + '.'
                        temp_pos += label_len
                    pos += 2
                    break
                else:
                    label_len = data[pos]
                    pos += 1
                    hostname += data[pos:pos+label_len].decode('ascii') + '.'
                    pos += label_len

            if data[pos] == 0:
                pos += 1  # Skip null byte if not compression pointer

            mx_records.append((preference, hostname.rstrip('.')))
        else:
            pos += rdlength  # Skip non-MX records

    return sorted(mx_records)  # Sort by preference


def parse_txt_response(data):
    """Parse TXT records from DNS response. Returns list of text strings."""
    # Skip header and question section (same as MX)
    pos = 12
    while data[pos] != 0:
        label_len = data[pos]
        pos += 1 + label_len
    pos += 1
    pos += 4

    ancount = int.from_bytes(data[6:8], 'big')

    txt_records = []
    for _ in range(ancount):
        # Skip NAME
        if data[pos] & 0xC0 == 0xC0:
            pos += 2
        else:
            while data[pos] != 0:
                label_len = data[pos]
                pos += 1 + label_len
            pos += 1

        rtype = int.from_bytes(data[pos:pos+2], 'big')
        pos += 2
        pos += 2  # CLASS
        pos += 4  # TTL
        rdlength = int.from_bytes(data[pos:pos+2], 'big')
        pos += 2

        if rtype == 16:  # TXT record
            # TXT RDATA is length-prefixed strings
            txt_value = ''
            rdata_end = pos + rdlength
            while pos < rdata_end:
                txt_len = data[pos]
                pos += 1
                txt_value += data[pos:pos+txt_len].decode('ascii')
                pos += txt_len
            txt_records.append(txt_value)
        else:
            pos += rdlength

    return txt_records


def query_mx(domain):
    """Query MX records for domain. Returns hostname with lowest preference."""
    packet = build_query(domain, 15)  # MX = type 15
    response = send_query(packet)
    mx_records = parse_mx_response(response)
    if not mx_records:
        raise Exception(f"No MX records found for {domain}")
    return mx_records[0][1]  # Return hostname with lowest preference


def query_txt(domain):
    """Query TXT records for domain. Returns list of text values."""
    packet = build_query(domain, 16)  # TXT = type 16
    response = send_query(packet)
    txt_records = parse_txt_response(response)
    if not txt_records:
        raise Exception(f"No TXT records found for {domain}")
    return txt_records


# =============================================================================
# Network Diagnostics
# =============================================================================

def test_network_connectivity():
    """Test network connectivity to diagnose VPC restrictions."""
    import socket
    print("=== Network Connectivity Diagnostics ===")

    tests = [
        # Test general outbound connectivity (baseline)
        ("google.com", 443, "HTTPS to google.com (baseline)"),

        # Test SMTP ports to known working SMTP server (Gmail)
        ("smtp.gmail.com", 25, "Gmail SMTP port 25"),
        ("smtp.gmail.com", 587, "Gmail SMTP port 587"),

        # Test SMTP ports on SES MX host
        ("inbound-smtp.ap-southeast-2.amazonaws.com", 25, "SES port 25 (known blocked)"),
        ("inbound-smtp.ap-southeast-2.amazonaws.com", 587, "SES port 587 (timed out)"),
        ("inbound-smtp.ap-southeast-2.amazonaws.com", 2587, "SES port 2587 (timed out)"),
    ]

    for host, port, description in tests:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                print(f"  ✓ {description}: SUCCESS")
            else:
                print(f"  ✗ {description}: FAILED (errno {result})")
        except Exception as e:
            print(f"  ✗ {description}: EXCEPTION {e}")

    print("=== End Diagnostics ===")


# =============================================================================
# Canary Handler
# =============================================================================

def handler(event, context):
    """
    CloudWatch Synthetics canary handler.

    This function is invoked by CloudWatch Synthetics on schedule.
    It performs end-to-end email testing and returns success/failure.

    Args:
        event: Synthetics event (contains timestamp)
        context: Lambda context

    Returns:
        str: Success message if email delivered within timeout

    Raises:
        Exception: If canary fails (timeout, MTA-STS error, SMTP error, etc.)
    """
    start_time = time.time()

    if not DOMAIN:
        raise ValueError("DOMAIN environment variable must be set")

    print(f"Starting canary test for domain: {DOMAIN}")

    # Step 1: Validate MTA-STS configuration (client-side check)
    print("Step 1: Validating MTA-STS configuration...")
    validate_mta_sts(DOMAIN)
    print("✓ MTA-STS configuration valid")

    # Step 2: Resolve MX records
    print("Step 2: Resolving MX records...")
    mx_host = resolve_mx_records(DOMAIN)
    print(f"✓ Resolved MX: {mx_host}")

    # Step 3: Load integration test token from SSM
    print("Step 3: Loading integration test token from SSM...")
    integration_token = get_integration_test_token()
    print("✓ Integration test token loaded")

    # Step 4: Running network diagnostics (SMTP send temporarily disabled)
    print("Step 4: Running network diagnostics...")
    test_network_connectivity()
    raise Exception("Stopping here to analyze diagnostics - SMTP send commented out")

    # SMTP send temporarily disabled for diagnostics
    # message_id = send_test_email(mx_host, DOMAIN, integration_token)
    # print(f"✓ Email sent successfully, SES Message ID: {message_id}")

    # Step 5: Poll DynamoDB for completion record
    # print("Step 5: Polling DynamoDB for completion confirmation...")
    # completion_time = poll_dynamodb_for_completion(message_id, start_time)

    # Calculate total latency
    # total_latency = completion_time - start_time
    # print(f"✓ Email delivered successfully in {total_latency:.2f} seconds")

    # return f"Canary test passed - Email delivered in {total_latency:.2f}s (Message ID: {message_id})"


def validate_mta_sts(domain):
    """
    Validate MTA-STS configuration for the domain (client-side check).

    Simulates a real email client checking MTA-STS policy before sending.
    Fails if MTA-STS is misconfigured or unavailable.

    Args:
        domain: Domain to validate (e.g., "example.com")

    Raises:
        Exception: If MTA-STS is misconfigured or unavailable
    """
    # Step 1: Check DNS TXT record for MTA-STS
    mta_sts_record = f"_mta-sts.{domain}"
    txt_records = query_txt(mta_sts_record)

    record_found = False
    for txt_value in txt_records:
        if txt_value.startswith('v=STSv1'):
            record_found = True
            print(f"  Found MTA-STS DNS record: {txt_value}")
            break

    if not record_found:
        raise Exception(f"MTA-STS DNS record exists but doesn't contain v=STSv1: {mta_sts_record}")

    # Step 2: Fetch MTA-STS policy file
    policy_url = f"https://mta-sts.{domain}/.well-known/mta-sts.txt"
    try:
        with urlopen(policy_url, timeout=10) as response:
            policy_content = response.read().decode('utf-8')
            print(f"  Fetched MTA-STS policy from: {policy_url}")

            # Validate policy has mode=enforce
            if 'mode: enforce' not in policy_content:
                raise Exception(f"MTA-STS policy does not have mode=enforce: {policy_content[:200]}")

            # Validate policy has version
            if 'version: STSv1' not in policy_content:
                raise Exception(f"MTA-STS policy missing version: STSv1")

            print(f"  MTA-STS policy validated (mode=enforce)")

    except (URLError, HTTPError) as e:
        raise Exception(f"Failed to fetch MTA-STS policy from {policy_url}: {e}")


def resolve_mx_records(domain):
    """
    Resolve MX records for the domain.

    Args:
        domain: Domain to resolve (e.g., "example.com")

    Returns:
        str: MX hostname with highest priority (lowest number)

    Raises:
        Exception: If no MX records found
    """
    mx_host = query_mx(domain)
    print(f"  Resolved MX: {mx_host}")
    return mx_host


def get_integration_test_token():
    """
    Load integration test token from SSM Parameter Store.

    Returns:
        str: Integration test token value

    Raises:
        Exception: If token cannot be loaded
    """
    global _integration_test_token

    if _integration_test_token is not None:
        return _integration_test_token

    try:
        parameter_name = f'/ses-mail/{ENVIRONMENT}/integration-test-token'
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        _integration_test_token = response['Parameter']['Value']
        return _integration_test_token
    except Exception as e:
        raise Exception(f"Failed to load integration test token from SSM: {e}")


def send_test_email(mx_host, domain, integration_token):
    """
    Send test email via SMTP with STARTTLS on port 587.

    Args:
        mx_host: MX hostname to connect to
        domain: Domain for From/To addresses
        integration_token: Integration test token (X-Integration-Test-Token header)

    Returns:
        str: SES message ID extracted from SMTP response

    Raises:
        Exception: If SMTP send fails or message ID cannot be extracted
    """
    timestamp = datetime.utcnow().isoformat() + 'Z'
    from_addr = f"canary-sender@{domain}"
    to_addr = f"canary@{domain}"
    subject = f"Canary Test - {timestamp}"

    # Create email message
    msg = MIMEMultipart()
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg['X-Integration-Test-Token'] = integration_token

    # Email body
    body = f"""This is an automated canary test email sent at {timestamp}.

This email validates the end-to-end email delivery pipeline:
- MTA-STS validation
- SMTP delivery via port 587 with STARTTLS
- SES inbound processing
- Router enrichment
- EventBridge routing
- Canary monitor Lambda processing
- DynamoDB completion record

If you receive this email, please disregard it.
"""
    msg.attach(MIMEText(body, 'plain'))

    # Connect to SMTP server with STARTTLS
    try:
        smtp = smtplib.SMTP(mx_host, 587, timeout=30)
        smtp.set_debuglevel(0)  # Set to 1 for debugging

        # Upgrade to TLS
        smtp.starttls()

        # Send email
        smtp.sendmail(from_addr, to_addr, msg.as_string())

        # Get SMTP response to extract message ID
        # SES responds with: 250 Ok <message-id>
        code, response_msg = smtp.getreply()
        smtp.quit()

        if code != 250:
            raise Exception(f"SMTP send failed with code {code}: {response_msg}")

        # Extract message ID from response (format: "250 Ok <message-id>")
        message_id = extract_message_id_from_response(response_msg)

        if not message_id:
            raise Exception(f"Failed to extract message ID from SMTP response: {response_msg}")

        return message_id

    except Exception as e:
        raise Exception(f"SMTP send failed: {e}")


def extract_message_id_from_response(response):
    """
    Extract SES message ID from SMTP response.

    SES responds with format: b'Ok <message-id>' or '250 Ok <message-id>'

    Args:
        response: SMTP response (bytes or str)

    Returns:
        str: Message ID (without angle brackets) or None if not found
    """
    # Convert bytes to string if needed
    if isinstance(response, bytes):
        response = response.decode('utf-8')

    # Extract message ID using regex (matches <...>)
    match = re.search(r'<([^>]+)>', response)
    if match:
        return match.group(1)

    return None


def poll_dynamodb_for_completion(message_id, start_time):
    """
    Poll DynamoDB for canary completion record.

    Polls every 10 seconds for up to 5 minutes (CANARY_TIMEOUT).

    Args:
        message_id: SES message ID to poll for
        start_time: Time when canary started (for timeout calculation)

    Returns:
        float: Time when completion record was found (time.time())

    Raises:
        Exception: If timeout occurs before completion record found
    """
    pk = f"CANARY#{message_id}"
    sk = "COMPLETION#v1"

    max_polls = CANARY_TIMEOUT // POLL_INTERVAL

    for poll_count in range(max_polls):
        elapsed = time.time() - start_time

        try:
            response = dynamodb.get_item(
                TableName=DYNAMODB_TABLE_NAME,
                Key={
                    'PK': {'S': pk},
                    'SK': {'S': sk}
                },
                ConsistentRead=True  # Use consistent read for immediate visibility
            )

            if 'Item' in response:
                # Completion record found!
                timestamp_ms = int(response['Item']['timestamp']['N'])
                completion_time = time.time()
                print(f"  ✓ Completion record found after {elapsed:.2f}s (poll #{poll_count + 1})")
                return completion_time

            # Record not found yet, wait and retry
            if poll_count < max_polls - 1:
                print(f"  Waiting for completion... ({elapsed:.0f}s elapsed, poll #{poll_count + 1})")
                time.sleep(POLL_INTERVAL)

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code')
            if error_code == 'ResourceNotFoundException':
                raise Exception(f"DynamoDB table not found: {DYNAMODB_TABLE_NAME}")
            else:
                print(f"  Warning: DynamoDB poll failed: {e}")
                # Continue polling
                if poll_count < max_polls - 1:
                    time.sleep(POLL_INTERVAL)

    # Timeout reached
    elapsed = time.time() - start_time
    raise Exception(f"Timeout after {elapsed:.2f}s - No completion record found for message: {message_id}")
