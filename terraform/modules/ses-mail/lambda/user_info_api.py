"""
User Info API Handler - Placeholder Implementation

Returns current authenticated user information from Cognito JWT.
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
    GET /api/users/me - Get current authenticated user information

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
        email_verified = jwt_claims.get('email_verified', False)
        username = jwt_claims.get('cognito:username', 'unknown')

        logger.info(f"User info request for user: {user_id}")

        # Return user information extracted from JWT
        response_body = {
            "user_id": user_id,
            "email": email,
            "email_verified": email_verified,
            "username": username,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "message": "User info extracted from Cognito JWT - full implementation in task 6.2"
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
        logger.error(f"Error processing user info request: {str(e)}", exc_info=True)

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
