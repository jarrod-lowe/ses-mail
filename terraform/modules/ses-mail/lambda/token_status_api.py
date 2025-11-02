"""
Token Status API Handler - Full Implementation

Returns the current status of Gmail refresh token for the authenticated user.
Queries DynamoDB for SMTP_USER record and extracts Gmail token metadata.

Author: Claude Code
Task: 6.2 - Token status and renewal API endpoints
"""

import json
import os
import logging
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

# Import shared utilities
from token_utils import (
    calculate_time_remaining,
    format_api_response,
    format_error_response
)

# Configure logging
logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

# Initialize AWS clients
dynamodb = boto3.client('dynamodb')


def lambda_handler(event, context):
    """
    GET /api/token/status - Get refresh token status for authenticated user

    Args:
        event: API Gateway Lambda proxy integration event
        context: Lambda context object

    Returns:
        API Gateway Lambda proxy integration response with token status
    """
    request_id = context.aws_request_id

    try:
        # Extract Cognito user ID from JWT claims
        authorizer = event.get('requestContext', {}).get('authorizer', {})
        jwt_claims = authorizer.get('jwt', {}).get('claims', {})
        user_id = jwt_claims.get('sub')
        email = jwt_claims.get('email', 'unknown')

        if not user_id:
            logger.error("No user_id found in JWT claims")
            return format_error_response(
                "Missing user_id in authentication token",
                status_code=401,
                request_id=request_id
            )

        logger.info(f"Token status request for user: {user_id}")

        # Get environment and table name
        environment = os.environ.get('ENVIRONMENT', 'test')
        table_name = os.environ.get('TABLE_NAME')

        if not table_name:
            logger.error("TABLE_NAME environment variable not set")
            return format_error_response(
                "Configuration error: TABLE_NAME not set",
                status_code=500,
                request_id=request_id
            )

        # Query DynamoDB for SMTP_USER record
        user_record = get_user_record(table_name, user_id)

        if not user_record:
            # User record doesn't exist
            logger.info(f"No user record found for user: {user_id}")
            response_body = {
                "has_token": False,
                "status": "no_user_record",
                "message": "No user record found. Please contact administrator.",
                "user_id": user_id,
                "email": email,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            return format_api_response(200, response_body, request_id)

        # Extract Gmail token fields
        gmail_token = user_record.get('gmail_refresh_token', {}).get('S')
        gmail_expires_at = user_record.get('gmail_refresh_expires_at', {}).get('S')
        gmail_address = user_record.get('gmail_address', {}).get('S')
        gmail_status = user_record.get('gmail_status', {}).get('S', 'missing')
        gmail_renewal_count = int(user_record.get('gmail_renewal_count', {}).get('N', '0'))

        # Check if token exists
        if not gmail_token:
            logger.info(f"No Gmail token found for user: {user_id}")
            response_body = {
                "has_token": False,
                "status": "missing",
                "message": "No Gmail token configured. Please initiate OAuth renewal.",
                "user_id": user_id,
                "email": email,
                "renewal_count": gmail_renewal_count,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            return format_api_response(200, response_body, request_id)

        # Calculate time remaining
        time_remaining = {"days_remaining": 0, "hours_remaining": 0}
        if gmail_expires_at:
            time_remaining = calculate_time_remaining(gmail_expires_at)

        # Determine status
        if time_remaining['days_remaining'] <= 0:
            status = "expired"
            message = "Gmail token has expired. Please renew your token."
        elif time_remaining['days_remaining'] <= 1:
            status = "expiring_soon"
            message = f"Gmail token expires in {time_remaining['hours_remaining']} hours. Please renew soon."
        else:
            status = "valid"
            message = f"Gmail token is valid for {time_remaining['days_remaining']} days."

        # Build response
        response_body = {
            "has_token": True,
            "gmail_address": gmail_address,
            "expires_at": gmail_expires_at,
            "days_remaining": time_remaining['days_remaining'],
            "hours_remaining": time_remaining['hours_remaining'],
            "status": status,
            "renewal_count": gmail_renewal_count,
            "message": message,
            "user_id": user_id,
            "email": email,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        logger.info(f"Token status retrieved successfully for user {user_id}: status={status}, days_remaining={time_remaining['days_remaining']}")

        return format_api_response(200, response_body, request_id)

    except ClientError as e:
        logger.error(f"DynamoDB error: {str(e)}", exc_info=True)
        return format_error_response(
            f"Database error: {e.response['Error']['Code']}",
            status_code=500,
            request_id=request_id
        )

    except Exception as e:
        logger.error(f"Error processing token status request: {str(e)}", exc_info=True)
        return format_error_response(
            "Internal server error",
            status_code=500,
            request_id=request_id
        )


def get_user_record(table_name: str, user_id: str) -> dict:
    """
    Get SMTP_USER record from DynamoDB.

    Args:
        table_name: DynamoDB table name
        user_id: Cognito user ID

    Returns:
        DynamoDB item dict or None if not found
    """
    try:
        response = dynamodb.get_item(
            TableName=table_name,
            Key={
                'PK': {'S': 'SMTP_USER'},
                'SK': {'S': f'USER#{user_id}'}
            }
        )

        return response.get('Item')

    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            logger.warning(f"Table {table_name} not found")
            return None
        raise
