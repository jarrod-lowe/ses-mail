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
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Gmail API scope for importing/inserting messages
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.insert']


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


def perform_interactive_oauth_flow(credentials: OAuthCredentials) -> Credentials:
    """
    Perform interactive OAuth authorization flow with browser interaction.

    Opens the user's default browser to Google's OAuth consent screen, runs a
    temporary local web server to receive the authorization callback, and exchanges
    the authorization code for OAuth tokens (access token and refresh token).

    Args:
        credentials: OAuthCredentials instance containing client ID, secret, and URIs

    Returns:
        Credentials object containing access token, refresh token, and metadata

    Raises:
        RuntimeError: If OAuth flow fails or user denies consent
    """
    logger.info("Starting interactive OAuth authorization flow")

    # Build client configuration dict from OAuthCredentials
    # This matches the format expected by InstalledAppFlow.from_client_config()
    client_config = {
        "installed": {
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "redirect_uris": credentials.redirect_uris,
            "auth_uri": credentials.auth_uri,
            "token_uri": credentials.token_uri,
            "auth_provider_x509_cert_url": credentials.auth_provider_x509_cert_url
        }
    }

    try:
        # Create OAuth flow with Gmail API scopes
        flow = InstalledAppFlow.from_client_config(
            client_config,
            scopes=GMAIL_SCOPES
        )

        logger.info(
            "Opening browser for OAuth consent. "
            "Please authorize the application to access Gmail."
        )
        print("\n" + "="*70)
        print("OAUTH AUTHORIZATION REQUIRED")
        print("="*70)
        print("\nYour browser will open automatically to Google's consent screen.")
        print("Please:")
        print("  1. Review the requested permissions")
        print("  2. Click 'Allow' to grant access")
        print("  3. Return to this terminal after authorization")
        print("\nIf your browser doesn't open automatically, copy the URL from below.")
        print("="*70 + "\n")

        # Run local server to handle OAuth callback
        # This will:
        # 1. Open the user's browser to Google's OAuth consent page
        # 2. Start a temporary HTTP server on localhost:8080
        # 3. Wait for Google to redirect back with authorization code
        # 4. Exchange authorization code for tokens
        # 5. Return Credentials object with access_token and refresh_token
        creds = flow.run_local_server(
            port=8080,
            authorization_prompt_message='Please visit this URL to authorize: {url}',
            success_message='Authorization successful! You may close this browser window and return to the terminal.',
            open_browser=True
        )

        logger.info("OAuth authorization flow completed successfully")
        logger.info(
            "Obtained OAuth tokens",
            extra={
                'has_refresh_token': creds.refresh_token is not None,
                'scopes': creds.scopes,
                'token_expiry': creds.expiry.isoformat() if creds.expiry else None
            }
        )

        if not creds.refresh_token:
            raise RuntimeError(
                "OAuth flow succeeded but did not return a refresh token.\n"
                "This can happen if the application was already authorized.\n"
                "To fix this:\n"
                "  1. Visit https://myaccount.google.com/permissions\n"
                "  2. Remove this application's access\n"
                "  3. Re-run this script to re-authorize"
            )

        return creds

    except Exception as e:
        # Catch various errors that can occur during OAuth flow
        error_message = str(e)

        if "invalid_client" in error_message.lower():
            raise RuntimeError(
                "OAuth client credentials are invalid.\n"
                "Please verify that the client_id and client_secret in SSM are correct.\n"
                f"Error details: {e}"
            )
        elif "access_denied" in error_message.lower():
            raise RuntimeError(
                "OAuth authorization denied by user.\n"
                "You must click 'Allow' on the consent screen to proceed.\n"
                "Re-run this script to try again."
            )
        elif "redirect_uri_mismatch" in error_message.lower():
            raise RuntimeError(
                "OAuth redirect URI mismatch.\n"
                "The redirect URI in your OAuth client configuration must include:\n"
                "  http://localhost:8080\n"
                f"Current redirect URIs: {credentials.redirect_uris}\n"
                f"Error details: {e}"
            )
        else:
            logger.exception("Unexpected error during OAuth flow")
            raise RuntimeError(
                f"OAuth authorization flow failed.\n"
                f"Error: {e}\n\n"
                "Common issues:\n"
                "  - Port 8080 is already in use (close other applications)\n"
                "  - Firewall blocking localhost connections\n"
                "  - Browser popup blockers preventing OAuth page from opening"
            )


def store_refresh_token(environment: str, refresh_token: str) -> datetime:
    """
    Store refresh token with metadata in SSM Parameter Store.

    Stores the refresh token along with creation and expiration timestamps in JSON format.
    Google refresh tokens in testing mode expire after 7 days, so we calculate the
    expiration time accordingly.

    Args:
        environment: Environment name (e.g., 'test', 'prod')
        refresh_token: The OAuth refresh token string from Google

    Returns:
        datetime: The expiration time (created_at + 7 days)

    Raises:
        RuntimeError: If token cannot be stored in SSM
    """
    parameter_name = f"/ses-mail/{environment}/gmail-forwarder/oauth/refresh-token"

    logger.info(f"Storing refresh token to SSM: {parameter_name}")

    # Calculate timestamps
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(days=7)  # Google testing mode limitation

    # Create JSON payload with token and metadata
    token_data = {
        "token": refresh_token,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "expires_at_epoch": int(expires_at.timestamp())
    }

    try:
        ssm_client = boto3.client('ssm')

        ssm_client.put_parameter(
            Name=parameter_name,
            Value=json.dumps(token_data),
            Type='SecureString',
            Overwrite=True
        )

        logger.info(
            "Successfully stored refresh token to SSM",
            extra={
                'parameter_name': parameter_name,
                'created_at': token_data['created_at'],
                'expires_at': token_data['expires_at'],
                'days_until_expiration': 7
            }
        )

        print("\n" + "="*70)
        print("REFRESH TOKEN STORED SUCCESSFULLY")
        print("="*70)
        print(f"\nParameter: {parameter_name}")
        print(f"Created:   {token_data['created_at']}")
        print(f"Expires:   {token_data['expires_at']}")
        print(f"Duration:  7 days (Google testing mode)")
        print("\n" + "="*70 + "\n")

        return expires_at

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')

        if error_code == 'AccessDeniedException':
            raise RuntimeError(
                f"Permission denied writing to SSM parameter: {parameter_name}\n\n"
                f"Ensure your AWS credentials have the following permissions:\n"
                f"  - ssm:PutParameter\n"
                f"  - kms:Encrypt (for SecureString parameters)\n\n"
                f"Current AWS profile: {boto3.Session().profile_name or 'default'}"
            )
        elif error_code == 'ParameterNotFound':
            raise RuntimeError(
                f"SSM parameter does not exist: {parameter_name}\n\n"
                f"This parameter should be created automatically by Terraform.\n"
                f"Please run:\n\n"
                f"   AWS_PROFILE=ses-mail make apply ENV={environment}\n\n"
                f"Then re-run this script."
            )
        else:
            raise RuntimeError(
                f"AWS error storing refresh token to SSM: {error_code}\n"
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
        logger.exception("Unexpected error storing refresh token")
        raise RuntimeError(f"Unexpected error storing token: {e}")


def trigger_retry_processing(environment: str) -> None:
    """
    Trigger Step Function execution to process messages in the retry queue.

    After successfully obtaining a new refresh token, this function starts the
    Step Function that processes all queued messages in the Gmail forwarder retry queue.
    The Step Function handles the actual message processing, retries, and error handling.

    Args:
        environment: Environment name (e.g., 'test', 'prod')

    Raises:
        RuntimeError: If Step Function cannot be triggered
    """
    logger.info("Triggering retry processing Step Function")

    # Get Step Function ARN from Terraform outputs
    # The Step Function ARN is exposed as a Terraform output
    try:
        # Construct Step Function name (matches Terraform naming convention)
        account_id = boto3.client('sts').get_caller_identity()['Account']
        region = boto3.Session().region_name or 'us-east-1'
        step_function_name = f"ses-mail-gmail-forwarder-retry-processor-{environment}"
        step_function_arn = f"arn:aws:states:{region}:{account_id}:stateMachine:{step_function_name}"

        logger.info(f"Step Function ARN: {step_function_arn}")

    except ClientError as e:
        raise RuntimeError(
            f"Failed to construct Step Function ARN: {e}\n"
            "Ensure AWS credentials are properly configured."
        )

    # Start Step Function execution
    try:
        sfn_client = boto3.client('stepfunctions')

        # Create unique execution name with timestamp
        execution_name = f"token-refresh-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

        response = sfn_client.start_execution(
            stateMachineArn=step_function_arn,
            name=execution_name
        )

        execution_arn = response['executionArn']

        logger.info(
            "Successfully started Step Function execution",
            extra={
                'execution_arn': execution_arn,
                'execution_name': execution_name,
                'step_function_arn': step_function_arn
            }
        )

        print("\n" + "="*70)
        print("RETRY PROCESSING TRIGGERED")
        print("="*70)
        print(f"\nStep Function: {step_function_name}")
        print(f"Execution:     {execution_name}")
        print(f"ARN:           {execution_arn}")
        print("\nThe Step Function will process all messages in the retry queue.")
        print("You can monitor execution in the AWS Console:")
        print(f"https://console.aws.amazon.com/states/home?region={region}#/executions/details/{execution_arn}")
        print("="*70 + "\n")

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')

        if error_code == 'AccessDeniedException':
            raise RuntimeError(
                f"Permission denied starting Step Function execution.\n\n"
                f"Ensure your AWS credentials have the following permissions:\n"
                f"  - states:StartExecution\n"
                f"  - states:DescribeExecution (optional, for monitoring)\n\n"
                f"Step Function ARN: {step_function_arn}\n"
                f"Current AWS profile: {boto3.Session().profile_name or 'default'}"
            )
        elif error_code == 'StateMachineDoesNotExist':
            raise RuntimeError(
                f"Step Function does not exist: {step_function_name}\n\n"
                f"Expected ARN: {step_function_arn}\n\n"
                f"This Step Function should be created by Terraform.\n"
                f"Please verify your infrastructure is deployed:\n\n"
                f"   AWS_PROFILE=ses-mail make apply ENV={environment}"
            )
        elif error_code == 'InvalidArn':
            raise RuntimeError(
                f"Invalid Step Function ARN: {step_function_arn}\n\n"
                f"This may indicate a configuration error.\n"
                f"Please verify the environment is correctly set up."
            )
        elif error_code == 'ExecutionAlreadyExists':
            logger.warning(
                f"Step Function execution '{execution_name}' already exists. "
                "This is expected if you've run the script multiple times in the same second."
            )
            print(f"\nNote: Retry processing execution already exists for this timestamp.")
        else:
            raise RuntimeError(
                f"AWS error starting Step Function execution: {error_code}\n"
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
        logger.exception("Unexpected error triggering retry processing")
        raise RuntimeError(f"Unexpected error triggering retry processing: {e}")


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
        oauth_credentials = retrieve_oauth_credentials(args.env)
        logger.info("OAuth client credentials retrieved successfully")

        # Task 3.2: Perform interactive OAuth flow
        gmail_credentials = perform_interactive_oauth_flow(oauth_credentials)
        logger.info("OAuth authorization completed - obtained refresh token")

        # Task 3.3: Store refresh token
        expires_at = store_refresh_token(args.env, gmail_credentials.refresh_token)
        logger.info("Refresh token stored successfully in SSM")

        # Task 3.4: Trigger retry processing
        trigger_retry_processing(args.env)
        logger.info("Retry processing Step Function triggered successfully")

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
