#!/usr/bin/env python3
"""
Enhanced OAuth Token Refresh Script

This script manages the complete OAuth token lifecycle for Gmail API access:
1. Retrieves OAuth client credentials from SSM Parameter Store
2. Performs interactive OAuth flow to obtain new refresh token
3. Stores refresh token in SSM Parameter Store
4. Sets up CloudWatch alarms for token expiration monitoring
5. Triggers retry processing of queued messages

Usage:
    AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test

Requirements:
    - AWS credentials configured (via AWS_PROFILE or default credentials)
    - OAuth client credentials already uploaded to SSM Parameter Store
    - Appropriate IAM permissions for SSM access
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from typing import Dict, Any, List

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class OAuthCredentials:
    """Structured representation of Google OAuth client credentials."""
    client_id: str
    client_secret: str
    redirect_uris: List[str]
    auth_uri: str
    token_uri: str
    auth_provider_x509_cert_url: str

    @classmethod
    def from_json(cls, credentials_json: str) -> 'OAuthCredentials':
        """
        Parse OAuth credentials from Google's JSON format.

        Google OAuth credentials come in a JSON structure with an 'installed' wrapper:
        {
            "installed": {
                "client_id": "...",
                "client_secret": "...",
                "redirect_uris": ["..."],
                ...
            }
        }

        Args:
            credentials_json: JSON string containing OAuth client credentials

        Returns:
            OAuthCredentials instance with parsed values

        Raises:
            ValueError: If JSON is malformed or missing required fields
        """
        try:
            data = json.loads(credentials_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format in client credentials: {e}")

        # Handle Google's OAuth JSON format (may have 'installed' or 'web' wrapper)
        if 'installed' in data:
            creds_data = data['installed']
        elif 'web' in data:
            creds_data = data['web']
        else:
            raise ValueError(
                "OAuth credentials JSON must contain 'installed' or 'web' key. "
                "Expected format: {'installed': {'client_id': '...', ...}}"
            )

        # Validate required fields
        required_fields = ['client_id', 'client_secret', 'redirect_uris', 'token_uri']
        missing_fields = [field for field in required_fields if field not in creds_data]
        if missing_fields:
            raise ValueError(
                f"OAuth credentials missing required fields: {', '.join(missing_fields)}"
            )

        return cls(
            client_id=creds_data['client_id'],
            client_secret=creds_data['client_secret'],
            redirect_uris=creds_data['redirect_uris'],
            auth_uri=creds_data.get('auth_uri', 'https://accounts.google.com/o/oauth2/auth'),
            token_uri=creds_data['token_uri'],
            auth_provider_x509_cert_url=creds_data.get(
                'auth_provider_x509_cert_url',
                'https://www.googleapis.com/oauth2/v1/certs'
            )
        )


def retrieve_oauth_credentials(environment: str) -> OAuthCredentials:
    """
    Retrieve OAuth client credentials from SSM Parameter Store.

    Fetches the complete client credentials JSON from SSM and parses it into
    a structured OAuthCredentials object.

    Args:
        environment: Environment name (e.g., 'test', 'prod')

    Returns:
        OAuthCredentials instance with parsed client credentials

    Raises:
        RuntimeError: If credentials cannot be retrieved or parsed
    """
    parameter_name = f"/ses-mail/{environment}/gmail-forwarder/oauth/client-credentials"

    logger.info(f"Retrieving OAuth client credentials from SSM: {parameter_name}")

    try:
        ssm_client = boto3.client('ssm')

        response = ssm_client.get_parameter(
            Name=parameter_name,
            WithDecryption=True
        )

        credentials_json = response['Parameter']['Value']

        # Check if this is still the placeholder value
        if credentials_json.startswith('PLACEHOLDER'):
            raise RuntimeError(
                f"OAuth client credentials not yet configured in SSM.\n\n"
                f"To set up client credentials:\n"
                f"1. Ensure you have your Google OAuth client_secret.json file\n"
                f"2. Upload it to SSM Parameter Store:\n\n"
                f"   AWS_PROFILE=ses-mail aws ssm put-parameter \\\n"
                f"     --name \"{parameter_name}\" \\\n"
                f"     --value \"$(cat client_secret.json)\" \\\n"
                f"     --type SecureString \\\n"
                f"     --overwrite\n\n"
                f"3. Re-run this script"
            )

        # Parse the credentials JSON
        try:
            credentials = OAuthCredentials.from_json(credentials_json)
            logger.info(
                f"Successfully retrieved OAuth credentials",
                extra={
                    'client_id': credentials.client_id[:20] + '...',  # Log partial ID only
                    'redirect_uris': credentials.redirect_uris
                }
            )
            return credentials

        except ValueError as e:
            raise RuntimeError(
                f"Failed to parse OAuth client credentials from SSM.\n"
                f"Error: {e}\n\n"
                f"The credentials must be in Google's OAuth JSON format.\n"
                f"Please verify the contents of {parameter_name} in SSM Parameter Store."
            )

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')

        if error_code == 'ParameterNotFound':
            raise RuntimeError(
                f"OAuth client credentials parameter not found in SSM: {parameter_name}\n\n"
                f"This parameter should be created automatically by Terraform.\n"
                f"If you've just deployed, run:\n\n"
                f"   AWS_PROFILE=ses-mail make apply ENV={environment}\n\n"
                f"Then upload your client credentials:\n\n"
                f"   AWS_PROFILE=ses-mail aws ssm put-parameter \\\n"
                f"     --name \"{parameter_name}\" \\\n"
                f"     --value \"$(cat client_secret.json)\" \\\n"
                f"     --type SecureString \\\n"
                f"     --overwrite"
            )
        elif error_code == 'AccessDeniedException':
            raise RuntimeError(
                f"Permission denied accessing SSM parameter: {parameter_name}\n\n"
                f"Ensure your AWS credentials have the following permissions:\n"
                f"  - ssm:GetParameter\n"
                f"  - kms:Decrypt (for SecureString parameters)\n\n"
                f"Current AWS profile: {boto3.Session().profile_name or 'default'}"
            )
        else:
            raise RuntimeError(
                f"AWS error retrieving OAuth credentials from SSM: {error_code}\n"
                f"Details: {e}"
            )

    except NoCredentialsError:
        raise RuntimeError(
            "No AWS credentials configured.\n\n"
            "Configure AWS credentials using one of:\n"
            "  - AWS_PROFILE=ses-mail environment variable\n"
            "  - ~/.aws/credentials file\n"
            "  - AWS IAM role (if running on EC2/Lambda)"
        )

    except Exception as e:
        logger.exception("Unexpected error retrieving OAuth credentials")
        raise RuntimeError(f"Unexpected error: {e}")


def main():
    """Main entry point for the OAuth token refresh script."""
    parser = argparse.ArgumentParser(
        description='Refresh Gmail OAuth token and manage token lifecycle',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--env',
        required=True,
        choices=['test', 'prod'],
        help='Environment to refresh token for'
    )

    args = parser.parse_args()

    try:
        logger.info(f"Starting OAuth token refresh for environment: {args.env}")

        # Task 3.1: Retrieve OAuth credentials from SSM
        credentials = retrieve_oauth_credentials(args.env)
        logger.info("OAuth client credentials retrieved successfully")

        # TODO: Task 3.2 - Implement interactive OAuth flow
        logger.warning("Interactive OAuth flow not yet implemented (Task 3.2)")

        # TODO: Task 3.3 - Store refresh token and setup expiration monitoring
        logger.warning("Token storage and expiration monitoring not yet implemented (Task 3.3)")

        # TODO: Task 3.4 - Trigger retry processing
        logger.warning("Retry processing trigger not yet implemented (Task 3.4)")

        logger.info("OAuth token refresh completed successfully")
        return 0

    except RuntimeError as e:
        logger.error(f"Failed to refresh OAuth token: {e}")
        return 1
    except KeyboardInterrupt:
        logger.info("Script interrupted by user")
        return 130
    except Exception as e:
        logger.exception("Unexpected error in OAuth token refresh")
        return 1


if __name__ == "__main__":
    sys.exit(main())
