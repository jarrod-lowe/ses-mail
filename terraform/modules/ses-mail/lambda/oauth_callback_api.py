"""
OAuth Callback API Handler - Placeholder Implementation

Handles OAuth callback from Google after authorization.
This is a minimal placeholder that will be fully implemented in task 6.2.

Author: Claude Code
Task: 6.1 - API Gateway with Cognito authorizer
"""

import json
import os
import logging
from datetime import datetime

# Configure logging
logger = logging.getLogger()
logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO'))


def lambda_handler(event, context):
    """
    GET /api/token/callback - Handle OAuth callback for authenticated user

    Args:
        event: API Gateway Lambda proxy integration event
        context: Lambda context object

    Returns:
        API Gateway Lambda proxy integration response
    """
    try:
        # Extract Cognito user ID from JWT claims
        authorizer = event.get('requestContext', {}).get('authorizer', {})
        jwt_claims = authorizer.get('jwt', {}).get('claims', {})
        user_id = jwt_claims.get('sub', 'unknown')
        email = jwt_claims.get('email', 'unknown')

        # Extract query parameters (OAuth callback includes code and state)
        query_params = event.get('queryStringParameters', {}) or {}
        oauth_code = query_params.get('code', None)
        state = query_params.get('state', None)

        logger.info(f"OAuth callback for user: {user_id}, code: {bool(oauth_code)}, state: {state}")

        # Placeholder response - will be fully implemented in task 6.2
        response_body = {
            "status": "not_implemented",
            "message": "OAuth callback API placeholder - implementation pending task 6.2",
            "user_id": user_id,
            "email": email,
            "has_code": bool(oauth_code),
            "state": state,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "X-Request-Id": context.aws_request_id
            },
            "body": json.dumps(response_body)
        }

    except Exception as e:
        logger.error(f"Error processing OAuth callback: {str(e)}", exc_info=True)

        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "error": "Internal server error",
                "message": str(e)
            })
        }
