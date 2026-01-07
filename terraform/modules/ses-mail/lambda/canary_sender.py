"""
SES Mail Canary Sender Lambda

Sends a canary test email via anonymous SMTP to validate the complete
SMTP → SES → Router → Gmail pipeline. Validates DNS security records
(MX, SPF, DMARC, MTA-STS) before sending.

Environment Variables:
    ENVIRONMENT: Deployment environment (test/prod)
    CANARY_EMAIL: Full canary email address (e.g., ses-canary-test@domain.com)
    DOMAIN: Primary domain for the SES configuration
"""

import os
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Dict

import boto3
import dns.resolver
from aws_lambda_powertools import Logger, Tracer

logger = Logger()
tracer = Tracer()

# Initialize AWS clients
ses_client = boto3.client('ses')


class DNSValidationError(Exception):
    """Raised when DNS validation fails"""
    pass


class SMTPSendError(Exception):
    """Raised when SMTP send fails"""
    pass


@tracer.capture_method
def validate_dns(domain: str) -> Dict[str, bool]:
    """
    Validate DNS records that a sending SMTP server would check.

    Validates:
    - MX records: Mail server endpoints
    - SPF records: Sender Policy Framework
    - DMARC records: Domain-based Message Authentication
    - MTA-STS records: SMTP TLS enforcement

    Args:
        domain: Domain to validate (e.g., "example.com")

    Returns:
        Dictionary with validation results for each record type

    Raises:
        DNSValidationError: If critical DNS records are missing or invalid
    """
    logger.info(f"Validating DNS records for domain: {domain}")
    results = {
        "mx": False,
        "spf": False,
        "dmarc": False,
        "mta_sts": False
    }
    errors = []

    # Validate MX records (CRITICAL)
    try:
        mx_records = dns.resolver.resolve(domain, 'MX')
        if mx_records:
            results["mx"] = True
            logger.info(f"MX records found: {[r.exchange.to_text() for r in mx_records]}")
        else:
            errors.append(f"No MX records found for {domain}")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer) as e:
        errors.append(f"MX lookup failed for {domain}: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error checking MX records: {e}")
        errors.append(f"MX lookup error: {e}")

    # Validate SPF records (CRITICAL)
    try:
        txt_records = dns.resolver.resolve(domain, 'TXT')
        spf_found = False
        for record in txt_records:
            txt_value = record.to_text()
            if 'v=spf1' in txt_value:
                spf_found = True
                results["spf"] = True
                logger.info(f"SPF record found: {txt_value}")
                break
        if not spf_found:
            errors.append(f"No SPF record found for {domain}")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer) as e:
        errors.append(f"SPF lookup failed for {domain}: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error checking SPF records: {e}")
        errors.append(f"SPF lookup error: {e}")

    # Validate DMARC records (CRITICAL)
    dmarc_domain = f"_dmarc.{domain}"
    try:
        txt_records = dns.resolver.resolve(dmarc_domain, 'TXT')
        dmarc_found = False
        for record in txt_records:
            txt_value = record.to_text()
            if 'v=DMARC1' in txt_value:
                dmarc_found = True
                results["dmarc"] = True
                logger.info(f"DMARC record found: {txt_value}")
                break
        if not dmarc_found:
            errors.append(f"No DMARC record found for {dmarc_domain}")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer) as e:
        errors.append(f"DMARC lookup failed for {dmarc_domain}: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error checking DMARC records: {e}")
        errors.append(f"DMARC lookup error: {e}")

    # Validate MTA-STS records (WARNING - not critical)
    mta_sts_domain = f"_mta-sts.{domain}"
    try:
        txt_records = dns.resolver.resolve(mta_sts_domain, 'TXT')
        mta_sts_found = False
        for record in txt_records:
            txt_value = record.to_text()
            if 'v=STSv1' in txt_value:
                mta_sts_found = True
                results["mta_sts"] = True
                logger.info(f"MTA-STS record found: {txt_value}")
                break
        if not mta_sts_found:
            logger.warning(f"No MTA-STS record found for {mta_sts_domain}")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        logger.warning(f"MTA-STS lookup failed for {mta_sts_domain} (not critical)")
    except Exception as e:
        logger.warning(f"Unexpected error checking MTA-STS records: {e}")

    # Fail if critical records are missing
    if errors:
        error_msg = "; ".join(errors)
        logger.error(f"DNS validation failed: {error_msg}")
        raise DNSValidationError(error_msg)

    logger.info(f"DNS validation passed: {results}")
    return results


@tracer.capture_method
def send_canary_email(
    canary_id: str,
    from_address: str,
    to_address: str,
    environment: str
) -> str:
    """
    Send canary test email via SES SendRawEmail API.

    Sends email TO own domain, which triggers full round-trip: SES sends
    out via SMTP (validating SPF/DMARC/DKIM), email comes back in via MX
    records, triggers receipt rules. Same path as external email.

    Args:
        canary_id: Unique identifier for this canary test
        from_address: Sender email address
        to_address: Recipient email address (ses-canary-{env}@domain)
        environment: Deployment environment (test/prod)

    Returns:
        SES message ID from the send operation

    Raises:
        SMTPSendError: If SES send fails
    """
    logger.info(f"Sending canary email: {canary_id}")

    # Construct email message
    msg = MIMEText(
        f"Canary test email\n\n"
        f"Environment: {environment}\n"
        f"Canary ID: {canary_id}\n"
        f"Sent at: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"This is an automated test email to validate the SMTP → SES → Gmail pipeline.\n"
        f"If you see this email, the canary test failed to mark it as read.",
        "plain"
    )

    msg["Subject"] = f"Canary Test [{environment.upper()}] - {canary_id}"
    msg["From"] = from_address
    msg["To"] = to_address
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_address.split("@")[1])
    msg["X-Canary-ID"] = canary_id  # Custom header for tracking
    msg["X-Canary-Environment"] = environment  # Environment identifier

    # Send via SES SendRawEmail API
    try:
        logger.info(f"Sending email via SES API: {from_address} → {to_address}")
        response = ses_client.send_raw_email(
            Source=from_address,
            Destinations=[to_address],
            RawMessage={
                'Data': msg.as_string()
            }
        )

        message_id = response['MessageId']
        logger.info(f"Canary email sent successfully: {canary_id}, SES MessageId: {message_id}")
        return message_id

    except Exception as e:
        error_msg = f"Unexpected error sending email: {e}"
        logger.error(error_msg)
        raise SMTPSendError(error_msg)


@tracer.capture_method
def write_tracking_record(canary_id: str, sent_at: str) -> None:
    """
    Write canary tracking record to DynamoDB.

    TODO: Implement DynamoDB write in Phase 3

    Record structure:
    {
        "PK": "CANARY#{canary_id}",
        "SK": "TRACKING#v1",
        "entity_type": "CANARY_TRACKING",
        "canary_id": canary_id,
        "status": "pending",
        "sent_at": sent_at,
        "ttl": <7 days from now>
    }

    Args:
        canary_id: Unique identifier for this canary test
        sent_at: ISO 8601 timestamp when email was sent
    """
    # TODO: Implement DynamoDB write in Phase 3
    logger.info(f"TODO: Write tracking record for canary {canary_id} (sent_at: {sent_at})")
    logger.info("DynamoDB write not implemented yet - will be added in Phase 3")


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def lambda_handler(event: Dict, context) -> Dict:
    """
    Lambda handler for canary sender.

    Orchestrates the canary test:
    1. Validates DNS records (MX, SPF, DMARC, MTA-STS)
    2. Sends canary email via anonymous SMTP
    3. Writes tracking record to DynamoDB (TODO)
    4. Returns canary ID and timestamp for Step Functions

    Args:
        event: Lambda event (empty dict for scheduled invocation)
        context: Lambda context

    Returns:
        Dictionary with canary_id and sent_at timestamp for Step Functions

    Raises:
        DNSValidationError: If DNS validation fails
        SMTPSendError: If SMTP send fails
    """
    logger.info("Starting canary test")

    # Get environment variables
    environment = os.environ.get("ENVIRONMENT", "unknown")
    canary_email = os.environ.get("CANARY_EMAIL")
    domain = os.environ.get("DOMAIN")

    if not canary_email or not domain:
        raise ValueError("CANARY_EMAIL and DOMAIN environment variables are required")

    logger.info(f"Environment: {environment}")
    logger.info(f"Canary email: {canary_email}")
    logger.info(f"Domain: {domain}")

    # Generate canary ID (timestamp-based for uniqueness)
    now = datetime.now(timezone.utc)
    canary_id = f"canary-{now.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    sent_at = now.isoformat()

    try:
        # Step 1: Validate DNS records
        logger.info("Step 1: Validating DNS records")
        dns_results = validate_dns(domain)
        logger.info(f"DNS validation passed: {dns_results}")

        # Step 2: Send canary email via SES API
        logger.info("Step 2: Sending canary email via SES API")
        ses_message_id = send_canary_email(
            canary_id=canary_id,
            from_address=f"noreply@{domain}",
            to_address=canary_email,
            environment=environment
        )

        # Step 3: Write tracking record (TODO - Phase 2b)
        logger.info("Step 3: Writing tracking record")
        write_tracking_record(canary_id, sent_at)

        # Return result for Step Functions
        result = {
            "canary_id": canary_id,
            "sent_at": sent_at,
            "ses_message_id": ses_message_id,
            "status": "sent",
            "dns_validation": dns_results
        }

        logger.info(f"Canary test completed successfully: {result}")
        return result

    except DNSValidationError as e:
        logger.error(f"DNS validation failed: {e}")
        raise
    except SMTPSendError as e:
        logger.error(f"SMTP send failed: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in canary test: {e}")
        raise
