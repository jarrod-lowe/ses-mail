"""
Lambda function to start a tag-sync task for AWS Service Catalog AppRegistry.

This function is invoked during Terraform deployment to automatically configure
tag-sync for the AppRegistry application, allowing it to discover resources
tagged with the Application tag.
"""
import json
import os
import boto3
import time
from botocore.exceptions import ClientError

appregistry = boto3.client('servicecatalog-appregistry')
resource_groups = boto3.client('resource-groups')


def lambda_handler(event, context):
    """
    Start a tag-sync task for an AppRegistry application.

    Args:
        event: {
            "application_arn": "arn:aws:servicecatalog:region:account:application/app-id",
            "tag_key": "Application",
            "tag_value": "ses-mail-test"  # Or "ses-mail-prod" for production
        }

    Returns:
        {
            "statusCode": 200,
            "body": {
                "status": "success",
                "message": "Tag-sync task started",
                "application_arn": "arn:..."
            }
        }
    """
    try:
        # Extract parameters from event
        application_arn = event.get('application_arn')
        tag_key = event.get('tag_key', 'Application')
        tag_value = event.get('tag_value')  # Must be provided (e.g., "ses-mail-test")

        if not application_arn:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'status': 'error',
                    'message': 'Missing required parameter: application_arn'
                })
            }

        print(f"Starting tag-sync for tag: {tag_key}={tag_value}")

        # Enable Group Lifecycle Events (GLE) at account level
        # This is required for tag-sync to work
        # NOTE: This does not work yet - there is a permissions error
        # you can run enabled it manually with:
        # aws resource-groups update-account-settings --group-lifecycle-events-desired-status ACTIVE
        attempts = 0
        max_attempts = 5
        while attempts < max_attempts:
            print("Enabling Group Lifecycle Events (GLE)...")
            update_response = resource_groups.update_account_settings(
                GroupLifecycleEventsDesiredStatus='ACTIVE'
            )
            gle_status = update_response['AccountSettings']['GroupLifecycleEventsStatus']
            gle_message = update_response['AccountSettings'].get('GroupLifecycleEventsStatusMessage', '')

            print(f"GLE Status: {gle_status}")
            if gle_message:
                print(f"GLE Status Message: {gle_message}")

            if gle_status == 'ACTIVE':
                break  # Successfully enabled

            if gle_status == 'IN_PROGRESS':
                time.sleep(5)  # Wait before retrying
                attempts += 1
                continue

            # Error out for any other status (including ERROR)
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'status': 'error',
                    'message': f'GLE failed to enable: {gle_status}; {gle_message}'
                })
            }

        # Get the tag-sync role ARN from environment variable
        # This role allows Resource Groups to discover and tag resources
        tag_sync_role_arn = os.environ.get('TAG_SYNC_ROLE_ARN')
        if not tag_sync_role_arn:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'status': 'error',
                    'message': 'TAG_SYNC_ROLE_ARN environment variable not set'
                })
            }

        print(f"Using tag-sync role: {tag_sync_role_arn} to set {tag_key}={tag_value} on members of {application_arn}")

        # Start the tag-sync task
        sync_response = resource_groups.start_tag_sync_task(
            Group=application_arn,
            TagKey=tag_key,
            TagValue=tag_value,
            RoleArn=tag_sync_role_arn
        )

        print(f"Tag-sync task started successfully")
        print(f"Response: {json.dumps(sync_response, default=str)}")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'status': 'success',
                'message': f'Tag-sync task started for {tag_key}={tag_value}',
                'application_arn': application_arn,
                'tag_key': tag_key,
                'tag_value': tag_value
            })
        }

    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']

        print(f"AWS API Error: {error_code} - {error_message}")

        # If tag-sync is already configured, that's okay
        if error_code == 'ConflictException' or 'already exists' in error_message.lower():
            print("Rule already exists - exit ok")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'status': 'success',
                    'message': 'Tag-sync task already configured (idempotent)',
                    'detail': error_message
                })
            }

        return {
            'statusCode': 500,
            'body': json.dumps({
                'status': 'error',
                'error_code': error_code,
                'message': error_message
            })
        }

    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'status': 'error',
                'message': f'Unexpected error: {str(e)}'
            })
        }
