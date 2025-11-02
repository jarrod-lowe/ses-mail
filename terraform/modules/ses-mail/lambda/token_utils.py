"""
Token Management Utility Functions

Shared utilities for Gmail OAuth token management API endpoints.
Handles Google OAuth flows, state token generation/validation, and Gmail API calls.

Author: Claude Code
Task: 6.2 - Token status and renewal API endpoints
"""

import json
import base64
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

logger = logging.getLogger()


def load_google_oauth_config(ssm_client, environment: str) -> Dict[str, str]:
    """
    Load Google OAuth configuration from SSM Parameter Store.

    Args:
        ssm_client: boto3 SSM client
        environment: Environment name (test, prod)

    Returns:
        Dict with client_id, client_secret, and redirect_uri

    Raises:
        ValueError: If parameters are missing or invalid
    """
    try:
        # Load parameters from SSM
        param_names = [
            f"/ses-mail/{environment}/google-oauth-client-id",
            f"/ses-mail/{environment}/google-oauth-client-secret",
            f"/ses-mail/{environment}/google-oauth-redirect-uri"
        ]

        response = ssm_client.get_parameters(
            Names=param_names,
            WithDecryption=True
        )

        if len(response['Parameters']) != 3:
            missing = set(param_names) - {p['Name'] for p in response['Parameters']}
            raise ValueError(f"Missing SSM parameters: {missing}")

        # Build config dict
        config = {}
        for param in response['Parameters']:
            if 'client-id' in param['Name']:
                config['client_id'] = param['Value']
            elif 'client-secret' in param['Name']:
                config['client_secret'] = param['Value']
            elif 'redirect-uri' in param['Name']:
                config['redirect_uri'] = param['Value']

        logger.info(f"Loaded Google OAuth config for environment: {environment}")
        return config

    except Exception as e:
        logger.error(f"Failed to load Google OAuth config: {str(e)}", exc_info=True)
        raise


def create_oauth_flow(client_config: Dict[str, str], redirect_uri: str, scopes: list) -> Flow:
    """
    Create Google OAuth flow object.

    Args:
        client_config: Dict with client_id and client_secret
        redirect_uri: OAuth redirect URI
        scopes: List of OAuth scopes

    Returns:
        Configured Flow object
    """
    # Build client config in format expected by Flow
    flow_config = {
        "web": {
            "client_id": client_config['client_id'],
            "client_secret": client_config['client_secret'],
            "redirect_uris": [redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
    }

    flow = Flow.from_client_config(
        flow_config,
        scopes=scopes,
        redirect_uri=redirect_uri
    )

    return flow


def generate_state_token(user_id: str) -> str:
    """
    Generate CSRF state token with user ID and timestamp.

    Args:
        user_id: Cognito user ID

    Returns:
        Base64-encoded state token
    """
    state_data = {
        "user_id": user_id,
        "timestamp": int(time.time())
    }

    state_json = json.dumps(state_data)
    state_token = base64.urlsafe_b64encode(state_json.encode()).decode()

    logger.info(f"Generated state token for user: {user_id}")
    return state_token


def validate_state_token(state: str, expected_user_id: str, max_age_seconds: int = 600) -> Tuple[bool, str]:
    """
    Validate CSRF state token.

    Args:
        state: Base64-encoded state token
        expected_user_id: Expected Cognito user ID from JWT
        max_age_seconds: Maximum age of state token (default 10 minutes)

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        # Decode state token
        state_json = base64.urlsafe_b64decode(state.encode()).decode()
        state_data = json.loads(state_json)

        # Validate user ID
        if state_data.get('user_id') != expected_user_id:
            return False, f"State user_id mismatch: expected {expected_user_id}, got {state_data.get('user_id')}"

        # Validate timestamp
        token_age = int(time.time()) - state_data.get('timestamp', 0)
        if token_age > max_age_seconds:
            return False, f"State token expired: {token_age} seconds old"

        logger.info(f"State token validated successfully for user: {expected_user_id}")
        return True, ""

    except Exception as e:
        logger.error(f"State token validation failed: {str(e)}", exc_info=True)
        return False, f"Invalid state token format: {str(e)}"


def get_gmail_address(credentials: Credentials) -> str:
    """
    Get Gmail email address for the authenticated user.

    Args:
        credentials: Google OAuth credentials

    Returns:
        Gmail email address

    Raises:
        Exception: If Gmail API call fails
    """
    try:
        # Build Gmail API service
        service = build('gmail', 'v1', credentials=credentials)

        # Get user profile
        profile = service.users().getProfile(userId='me').execute()
        email_address = profile.get('emailAddress')

        if not email_address:
            raise ValueError("No email address found in Gmail profile")

        logger.info(f"Retrieved Gmail address: {email_address}")
        return email_address

    except Exception as e:
        logger.error(f"Failed to get Gmail address: {str(e)}", exc_info=True)
        raise


def calculate_token_expiration(days: int = 7) -> str:
    """
    Calculate token expiration timestamp.

    Args:
        days: Number of days until expiration (default 7 for testing mode)

    Returns:
        ISO 8601 formatted expiration timestamp
    """
    expires_at = datetime.utcnow() + timedelta(days=days)
    return expires_at.isoformat() + 'Z'


def calculate_time_remaining(expires_at_iso: str) -> Dict[str, int]:
    """
    Calculate time remaining until token expiration.

    Args:
        expires_at_iso: ISO 8601 formatted expiration timestamp

    Returns:
        Dict with days_remaining and hours_remaining
    """
    try:
        # Parse expiration timestamp
        if expires_at_iso.endswith('Z'):
            expires_at_iso = expires_at_iso[:-1]

        expires_at = datetime.fromisoformat(expires_at_iso)
        now = datetime.utcnow()

        # Calculate remaining time
        remaining = expires_at - now

        if remaining.total_seconds() < 0:
            return {"days_remaining": 0, "hours_remaining": 0}

        days = remaining.days
        hours = int(remaining.total_seconds() // 3600)

        return {
            "days_remaining": days,
            "hours_remaining": hours
        }

    except Exception as e:
        logger.error(f"Failed to calculate time remaining: {str(e)}", exc_info=True)
        return {"days_remaining": 0, "hours_remaining": 0}


def format_api_response(status_code: int, body: dict, request_id: str = None) -> dict:
    """
    Format API Gateway Lambda proxy integration response.

    Args:
        status_code: HTTP status code
        body: Response body dict
        request_id: Optional request ID for X-Request-Id header

    Returns:
        API Gateway response dict
    """
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
    }

    if request_id:
        headers["X-Request-Id"] = request_id

    return {
        "statusCode": status_code,
        "headers": headers,
        "body": json.dumps(body)
    }


def format_error_response(error_message: str, status_code: int = 500, request_id: str = None) -> dict:
    """
    Format error response for API Gateway.

    Args:
        error_message: Error message
        status_code: HTTP status code (default 500)
        request_id: Optional request ID

    Returns:
        API Gateway error response dict
    """
    body = {
        "error": "true",
        "message": error_message,
        "timestamp": datetime.utcnow().isoformat() + 'Z'
    }

    return format_api_response(status_code, body, request_id)
