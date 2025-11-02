"""
OAuth Callback API Handler - Full Implementation

Handles OAuth callback from Google after authorization.
Exchanges authorization code for refresh token and stores in DynamoDB.

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
    load_google_oauth_config,
    create_oauth_flow,
    validate_state_token,
    get_gmail_address,
    calculate_token_expiration,
    format_api_response,
    format_error_response
)

# Configure logging
logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

# Initialize AWS clients
ssm = boto3.client('ssm')
dynamodb = boto3.client('dynamodb')

# Gmail API scopes
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.insert"]


def lambda_handler(event, context):
    """
    GET /api/token/callback - Handle OAuth callback for authenticated user

    Args:
        event: API Gateway Lambda proxy integration event with code and state params
        context: Lambda context object

    Returns:
        API Gateway Lambda proxy integration response with success status
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

        # Extract query parameters (OAuth callback includes code and state)
        query_params = event.get('queryStringParameters', {}) or {}
        oauth_code = query_params.get('code')
        state = query_params.get('state')

        logger.info(f"OAuth callback for user: {user_id}, has_code: {bool(oauth_code)}")

        # Validate required parameters
        if not oauth_code:
            logger.error("Missing OAuth authorization code")
            return format_error_response(
                "Missing authorization code in callback",
                status_code=400,
                request_id=request_id
            )

        if not state:
            logger.error("Missing state parameter")
            return format_error_response(
                "Missing state parameter in callback",
                status_code=400,
                request_id=request_id
            )

        # Validate state token (CSRF protection)
        is_valid, error_msg = validate_state_token(state, user_id)
        if not is_valid:
            logger.error(f"State validation failed: {error_msg}")
            return format_error_response(
                f"Invalid state parameter: {error_msg}",
                status_code=403,
                request_id=request_id
            )

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

        # Load Google OAuth configuration from SSM
        try:
            oauth_config = load_google_oauth_config(ssm, environment)
        except Exception as e:
            logger.error(f"Failed to load OAuth config: {str(e)}", exc_info=True)
            return format_error_response(
                "OAuth configuration error",
                status_code=500,
                request_id=request_id
            )

        # Create OAuth flow
        try:
            flow = create_oauth_flow(
                oauth_config,
                oauth_config['redirect_uri'],
                GMAIL_SCOPES
            )
        except Exception as e:
            logger.error(f"Failed to create OAuth flow: {str(e)}", exc_info=True)
            return format_error_response(
                "Failed to initialize OAuth flow",
                status_code=500,
                request_id=request_id
            )

        # Exchange authorization code for tokens
        try:
            flow.fetch_token(code=oauth_code)
            credentials = flow.credentials
        except Exception as e:
            logger.error(f"Failed to exchange authorization code: {str(e)}", exc_info=True)
            return format_error_response(
                "Failed to exchange authorization code for tokens",
                status_code=400,
                request_id=request_id
            )

        # Extract refresh token
        refresh_token = credentials.refresh_token
        if not refresh_token:
            logger.error("No refresh token in OAuth response")
            return format_error_response(
                "No refresh token received. User may need to revoke previous access and retry.",
                status_code=400,
                request_id=request_id
            )

        # Get Gmail address for the user
        try:
            gmail_address = get_gmail_address(credentials)
        except Exception as e:
            logger.error(f"Failed to get Gmail address: {str(e)}", exc_info=True)
            return format_error_response(
                "Failed to retrieve Gmail address",
                status_code=500,
                request_id=request_id
            )

        # Calculate expiration (7 days for testing mode)
        expires_at = calculate_token_expiration(days=7)

        # Update DynamoDB with new token
        try:
            update_user_token(
                table_name,
                user_id,
                refresh_token,
                expires_at,
                gmail_address
            )
        except Exception as e:
            logger.error(f"Failed to update user token: {str(e)}", exc_info=True)
            return format_error_response(
                "Failed to save Gmail token",
                status_code=500,
                request_id=request_id
            )

        # Build success response
        response_body = {
            "success": True,
            "gmail_address": gmail_address,
            "expires_at": expires_at,
            "user_id": user_id,
            "email": email,
            "message": "Gmail token successfully renewed and saved",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        logger.info(f"OAuth token successfully saved for user {user_id}: gmail={gmail_address}")

        return format_api_response(200, response_body, request_id)

    except ClientError as e:
        logger.error(f"AWS service error: {str(e)}", exc_info=True)
        return format_error_response(
            f"AWS service error: {e.response['Error']['Code']}",
            status_code=500,
            request_id=request_id
        )

    except Exception as e:
        logger.error(f"Error processing OAuth callback: {str(e)}", exc_info=True)
        return format_error_response(
            "Internal server error",
            status_code=500,
            request_id=request_id
        )


def update_user_token(table_name: str, user_id: str, refresh_token: str,
                      expires_at: str, gmail_address: str):
    """
    Update SMTP_USER record with Gmail refresh token.

    Args:
        table_name: DynamoDB table name
        user_id: Cognito user ID
        refresh_token: Google OAuth refresh token
        expires_at: ISO 8601 expiration timestamp
        gmail_address: Gmail email address

    Raises:
        ClientError: If DynamoDB update fails
    """
    current_time = datetime.utcnow().isoformat() + 'Z'

    # Update DynamoDB record
    dynamodb.update_item(
        TableName=table_name,
        Key={
            'PK': {'S': 'SMTP_USER'},
            'SK': {'S': f'USER#{user_id}'}
        },
        UpdateExpression='SET gmail_refresh_token = :token, '
                        'gmail_refresh_expires_at = :expires, '
                        'gmail_address = :addr, '
                        'gmail_renewal_count = if_not_exists(gmail_renewal_count, :zero) + :one, '
                        'gmail_status = :status, '
                        'updated_at = :updated',
        ExpressionAttributeValues={
            ':token': {'S': refresh_token},
            ':expires': {'S': expires_at},
            ':addr': {'S': gmail_address},
            ':zero': {'N': '0'},
            ':one': {'N': '1'},
            ':status': {'S': 'valid'},
            ':updated': {'S': current_time}
        },
        ReturnValues='NONE'
    )

    logger.info(f"Updated Gmail token for user {user_id} in DynamoDB")
