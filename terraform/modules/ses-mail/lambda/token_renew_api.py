"""
Token Renewal API Handler - Full Implementation

Initiates Google OAuth flow for obtaining new refresh tokens.
Generates OAuth authorization URL with state parameter for CSRF protection.

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
    generate_state_token,
    format_api_response,
    format_error_response
)

# Configure logging
logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))

# Initialize AWS clients
ssm = boto3.client('ssm')

# Gmail API scopes
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.insert"]


def lambda_handler(event, context):
    """
    POST /api/token/renew - Initiate OAuth renewal flow for authenticated user

    Args:
        event: API Gateway Lambda proxy integration event
        context: Lambda context object

    Returns:
        API Gateway Lambda proxy integration response with OAuth authorization URL
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

        logger.info(f"Token renewal request for user: {user_id}")

        # Get environment
        environment = os.environ.get('ENVIRONMENT', 'test')

        # Load Google OAuth configuration from SSM
        try:
            oauth_config = load_google_oauth_config(ssm, environment)
        except Exception as e:
            logger.error(f"Failed to load OAuth config: {str(e)}", exc_info=True)
            return format_error_response(
                "OAuth configuration error. Please contact administrator.",
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

        # Generate state token with user ID for CSRF protection
        state_token = generate_state_token(user_id)

        # Generate authorization URL
        try:
            authorization_url, _ = flow.authorization_url(
                access_type='offline',  # Request refresh token
                prompt='consent',       # Force consent screen to get refresh token
                state=state_token,
                include_granted_scopes='true'
            )
        except Exception as e:
            logger.error(f"Failed to generate authorization URL: {str(e)}", exc_info=True)
            return format_error_response(
                "Failed to generate OAuth authorization URL",
                status_code=500,
                request_id=request_id
            )

        # Build response
        response_body = {
            "authorization_url": authorization_url,
            "state": state_token,
            "user_id": user_id,
            "email": email,
            "redirect_uri": oauth_config['redirect_uri'],
            "message": "OAuth authorization URL generated successfully. Redirect user to this URL.",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        logger.info(f"OAuth authorization URL generated for user: {user_id}")

        return format_api_response(200, response_body, request_id)

    except ClientError as e:
        logger.error(f"AWS service error: {str(e)}", exc_info=True)
        return format_error_response(
            f"AWS service error: {e.response['Error']['Code']}",
            status_code=500,
            request_id=request_id
        )

    except Exception as e:
        logger.error(f"Error processing token renewal request: {str(e)}", exc_info=True)
        return format_error_response(
            "Internal server error",
            status_code=500,
            request_id=request_id
        )
