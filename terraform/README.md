# SES Mail Infrastructure

This Terraform configuration sets up AWS SES for receiving emails and processing them through a Lambda function that will eventually insert them into Gmail via the API.

## Architecture

1. **SES Domain Identity**: Configures SES to receive emails for your domain
2. **S3 Storage**: Stores incoming emails with encryption and lifecycle management
3. **Lambda Function**: Processes emails (currently a stub for logging)
4. **CloudWatch**: Monitors email flow with metrics, alarms, and dashboard
5. **SSM Parameter Store**: Securely stores Gmail OAuth token

## Features

* Spam and virus scanning enabled on all incoming emails
* AWS managed DKIM keys for email authentication
* Encrypted email storage in S3
* CloudWatch alarms with SNS notifications
* Comprehensive monitoring dashboard
* Configurable email retention period

## Prerequisites

* AWS CLI configured with appropriate credentials
* Terraform >= 1.0
* Access to Route53 hosted zone for your domain (may be in another AWS account)
* Gmail OAuth token from the main project setup
* SNS topic ARN for alarm notifications

## State Management

This project uses S3 for Terraform state storage with the following setup:

* **State Bucket**: Automatically created as `terraform-state-{account-id}`
* **State Key**: `ses-mail/{environment}.tfstate` (environment from terraform.tfvars)
* **Locking**: S3 native locking (no DynamoDB required)
* **Encryption**: Server-side encryption with AES256
* **Versioning**: Enabled for state history

The Makefile handles state bucket creation and backend configuration automatically.

## Deployment

### 1. Configure your variables

Edit `terraform.tfvars` with your domain and settings:

```bash
vi terraform/terraform.tfvars
```

Required variables:

```hcl
aws_region                      = "ap-southeast-2"
environment                     = "production"
domain                          = ["mail.example.com", "mail2.example.com"]
email_retention_days            = 90
alarm_sns_topic_arn             = "arn:aws:sns:ap-southeast-2:123456789012:AlarmTopic"
alarm_email_count_threshold     = 100
alarm_rejection_rate_threshold  = 50
```

### 2. Initialize Terraform

From the project root:

```bash
make init
```

This will:

* Create the state bucket if it doesn't exist
* Configure versioning and encryption
* Initialize Terraform with the S3 backend

### 3. Create a plan

```bash
make plan
```

This creates `terraform/terraform.plan` showing all changes.

### 4. Apply the configuration

```bash
make apply
```

This applies the plan file created in the previous step. The plan file is automatically removed after successful apply.

**Note**: If you run `make apply` without running `make plan` first, Make's dependency system will automatically create the plan before applying it.

### 5. Configure DNS records in Route53

After deployment, view the DNS records:

```bash
cd terraform
terraform output dns_configuration_summary
```

Or from the project root:

```bash
make -C terraform output dns_configuration_summary
```

The output will be grouped by domain. For each domain, you'll need to add:

* **1 TXT record** for domain verification
* **1 MX record** for receiving emails
* **3 CNAME records** for DKIM authentication
* **Optional**: MTA-STS and TLS-RPT records (if enabled)

Example output structure:

```json
{
  "domains": {
    "mail.example.com": [
      {
        "name": "_amazonses.mail.example.com",
        "type": "TXT",
        "value": "abc123...",
        "purpose": "Domain verification"
      }
    ]
  }
}
```

Add the displayed DNS records to your Route53 hosted zone for each domain.

### 6. Verify domains in SES

After adding the DNS records, wait for DNS propagation (usually 5-15 minutes). Check the status:

```bash
aws ses get-identity-verification-attributes --identities mail.example.com mail2.example.com
```

### 7. Complete MTA-STS setup (if enabled)

If you configured `mta_sts_mode` to `testing` or `enforce`, the first `make apply` will create ACM certificates (one per domain) but CloudFront creation will fail. This is expected because the certificates need DNS validation records added first.

After adding all DNS records from step 5 (including the ACM validation CNAME records for each domain), wait for the certificates to validate:

```bash
# Check certificate status (should show ISSUED when ready)
aws acm list-certificates --region us-east-1
```

Certificate validation usually takes 5-30 minutes. Once all certificates show `Status: ISSUED`, run terraform again to create the CloudFront distributions:

```bash
make plan
make apply
```

The second apply will successfully create the CloudFront distributions (one per domain) and complete the MTA-STS setup.

### 8. Update Gmail OAuth token

Update the SSM parameter with your Gmail OAuth token from the token.json file:

```bash
# Replace 'production' with your environment name from terraform.tfvars
aws ssm put-parameter \
  --name "/ses-mail/production/gmail-token" \
  --value "$(cat ../token.json)" \
  --type SecureString \
  --overwrite
```

## Monitoring

### CloudWatch Dashboard

Access the monitoring dashboard:

```bash
terraform output cloudwatch_dashboard_url
```

The dashboard shows:

* Email processing statistics (accepted, spam, virus)
* Lambda performance metrics
* Recent email logs

### CloudWatch Alarms

Three alarms are configured:

1. **High Email Volume**: Triggers if more than 100 emails are received in 5 minutes
2. **High Spam Rate**: Triggers if spam rate exceeds 50%
3. **Lambda Errors**: Triggers if Lambda has more than 5 errors in 5 minutes

All alarms send notifications to the configured SNS topic.

## Testing

Send a test email to any address at any of your configured domains:

```bash
echo "Test email body" | mail -s "Test Subject" test@mail.example.com
```

Check the Lambda logs:

```bash
aws logs tail /aws/lambda/ses-mail-email-processor-ENVIRONMENT --follow
```

Replace `ENVIRONMENT` with your environment name from terraform.tfvars.

## Future Development

The Lambda function currently logs email metadata. To complete the integration:

1. Implement email parsing from S3
2. Add Gmail API client with OAuth token from SSM
3. Insert emails into Gmail using the API
4. Add error handling and retry logic
5. Update CloudWatch metrics for successful Gmail insertions

## Resources Created

* SES domain identity and receipt rules
* S3 bucket with encryption and lifecycle policies
* Lambda function with IAM role
* CloudWatch log group, metric filters, dashboard, and alarms
* SSM Parameter Store parameter for Gmail token

## Destroying Infrastructure

To destroy all resources:

```bash
make plan-destroy  # Create destroy plan
make destroy       # Apply the destroy plan
```

Or use the direct Terraform command:

```bash
cd terraform
terraform destroy
```

**Warning**: This will delete all stored emails in S3 and cannot be undone.

## Makefile Targets

* `make help` - Show available targets
* `make init` - Initialize Terraform and create state bucket
* `make plan` - Create a plan file
* `make apply` - Apply the plan file (depends on plan)
* `make plan-destroy` - Create a destroy plan file
* `make destroy` - Apply the destroy plan (depends on plan-destroy)
* `make clean` - Clean up local Terraform files and plans

## Outputs

Key outputs available after deployment:

* `dns_configuration_summary`: All DNS records to configure
* `s3_bucket_name`: Email storage bucket name
* `lambda_function_name`: Email processor function name
* `ssm_parameter_name`: Gmail token parameter name
* `cloudwatch_dashboard_url`: Direct link to dashboard
