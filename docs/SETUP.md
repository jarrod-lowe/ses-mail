# Setup Guide

This guide walks you through the complete first-time setup of the SES Mail system, from creating Google OAuth credentials to deploying and verifying the AWS infrastructure.

## Table of Contents

- [Prerequisites Verification](#prerequisites-verification)
- [1. Google Cloud Configuration](#1-google-cloud-configuration)
- [2. AWS Account Preparation](#2-aws-account-preparation)
- [3. Terraform Deployment](#3-terraform-deployment)
- [4. DNS Configuration](#4-dns-configuration)
- [5. MTA-STS Setup (Optional)](#5-mta-sts-setup-optional)
- [6. OAuth Token Setup](#6-oauth-token-setup)
- [7. Enable AWS Services](#7-enable-aws-services)
- [8. Verification](#8-verification)

## Prerequisites Verification

Before starting setup, verify you have all required tools and access:

| Prerequisite | Verification Command | Expected Result |
| ------------ | -------------------- | --------------- |
| AWS CLI | `aws --version` | AWS CLI 2.x or higher |
| AWS Profile | `aws sts get-caller-identity --profile ses-mail` | Account details |
| Terraform | `terraform version` | Terraform v1.0+ |
| Python 3 | `python3 --version` | Python 3.8+ |
| Git | `git --version` | Any recent version |
| Google Cloud access | Browser access to console.cloud.google.com | Can log in |

**AWS Requirements:**

- AWS account with administrative access
- SES production access enabled (not sandbox mode)
- AWS CLI configured with profile named `ses-mail`

**Google Cloud Requirements:**

- Google Cloud account
- Ability to create projects
- Gmail account for testing

## 1. Google Cloud Configuration

### 1.1 Create Google Cloud Project

1. Go to <https://console.cloud.google.com>
2. Click the current project (top left next to "Google Cloud")
3. Click "New Project"
4. Name it "ses-mail"
5. Leave the organisation as "No organisation"
6. Click "Create"

**Success Criteria:** You should see the new "ses-mail" project in the project selector.

### 1.2 Enable Gmail API

1. In the Google Cloud Console, click ☰ menu
2. Navigate to: **APIs and services** → **Library**
3. Search for "Gmail API"
4. Select "Gmail API"
5. Click "Enable"

**Success Criteria:** You should see "API enabled" with a green checkmark.

### 1.3 Configure OAuth Consent Screen

1. In the Google Cloud Console, navigate to: **APIs and services** → **OAuth consent screen**
2. If prompted "Google auth platform not configured yet", click "Get started"
3. Fill in the following:
   - **App name**: `ses-mail`
   - **User support email**: Select your email address
4. Click "Next"
5. Select "External" for user type
6. Click "Next"
7. Add your email address for the developer contact address
8. Click "Next"
9. Agree to the policy
10. Click "Continue"
11. Click "Create"

**Success Criteria:** OAuth consent screen should be configured and show "External" type.

### 1.4 Add Test Users

1. Navigate to: **OAuth consent screen** → **Audience**
2. Click "Add users"
3. Add your Gmail address (the one that will receive forwarded emails)
4. Click "Save"

**Success Criteria:** Your Gmail address should appear in the test users list.

### 1.5 Create OAuth Client Credentials

1. Navigate to: **APIs and services** → **Credentials**
2. Click "Create Credentials"
3. Select "OAuth client ID"
4. Configure:
   - **Application type**: Desktop app
   - **Name**: `ses-mail`
5. Click "Create"
6. Click "Download JSON" and save the file
7. Click "OK"
8. Rename the downloaded file to `client_secret.json`
9. Place it in the project root directory

**Success Criteria:** You should have a `client_secret.json` file in your project directory.

### 1.6 Configure OAuth Scopes

1. Navigate to: **OAuth consent screen** → **Data Access**
2. Click "Add or remove scopes"
3. Search for "Gmail API"
4. Select the scope: `.../auth/gmail.insert` (or `.../auth/gmail.modify`)
5. Click "Update"
6. Click "Save"

**Success Criteria:** The Gmail API scope should appear in your configured scopes.

### 1.7 Generate Initial Test Token (Local Testing Only)

This step is optional and only for local testing. The production system will use SSM Parameter Store.

```bash
# Create Python virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # On macOS/Linux
# OR
.venv\Scripts\activate     # On Windows

# Install dependencies
pip3 install -r requirements.txt

# Run the token creation script
./scripts/create_refresh_token.py
```

**What happens:**

1. A browser window will open
2. Select your Google account
3. Click "Continue" to grant permissions
4. Click "Continue" again
5. Close the browser tab
6. A `token.json` file will be created

**Success Criteria:** You should have `token.json` file in your project directory.

**Important:** After completing infrastructure setup, you'll upload these credentials to AWS SSM Parameter Store and delete the local files for security.

## 2. AWS Account Preparation

### 2.1 Enable SES Production Access

AWS SES starts in "sandbox mode" which restricts email sending. You must request production access.

1. Go to AWS Console → Amazon SES
2. Click "Account dashboard" in the left menu
3. Look for the message about sandbox mode
4. Click "Request production access"
5. Fill out the request form:
   - **Mail type**: Transactional
   - **Website URL**: Your domain or organization site
   - **Use case description**: Explain you're setting up email forwarding
   - **Compliance**: Confirm you'll only send to recipients who expect it
6. Submit the request

**Timeline:** AWS typically approves within 24-48 hours.

**Success Criteria:** Email from AWS confirming production access is granted.

### 2.2 Verify AWS CLI Profile

Ensure your AWS CLI profile is configured correctly:

```bash
# Test AWS profile
AWS_PROFILE=ses-mail aws sts get-caller-identity
```

**Expected output:**

```json
{
    "UserId": "AIDAEXAMPLEID",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/your-user"
}
```

**Success Criteria:** Command returns your AWS account details without errors.

## 3. Terraform Deployment

### 3.1 Understanding the Infrastructure Structure

The infrastructure is organized into environments and a reusable module:

```text
terraform/
├── environments/
│   ├── test/              # Test environment configuration
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── terraform.tfvars
│   │   └── outputs.tf
│   └── prod/              # Production environment configuration
│       └── (same structure)
└── modules/
    └── ses-mail/          # Reusable SES mail module
```

### 3.2 Configure Environment Variables

Edit the Terraform variables file for your environment:

```bash
# For test environment
vi terraform/environments/test/terraform.tfvars

# For production environment
vi terraform/environments/prod/terraform.tfvars
```

**Required variables:**

```hcl
# Domain(s) for email receiving - must be a list
domain = ["mail.example.com"]  # or ["mail1.example.com", "mail2.example.com"]

# AWS region
region = "ap-southeast-2"  # or your preferred region

# Environment name
environment = "test"  # or "prod"
```

**Optional variables:**

```hcl
# For MTA-STS support
enable_mta_sts = true

# For DMARC reporting
dmarc_report_email_prefix = "dmarc"  # Creates dmarc@your-domain

# For backup MX servers
backup_mx_servers = ["backup-mail.example.com"]
```

**Success Criteria:** Terraform variables file exists and contains your domain configuration.

### 3.3 Choose Deployment Mode

#### Option A: Separate AWS Accounts (Recommended)

If test and prod use different AWS accounts, no additional configuration needed. Each environment manages its own SES ruleset independently.

#### Option B: Shared AWS Account

If test and prod share the same AWS account, you must configure test to join prod's SES ruleset (AWS SES only allows one active ruleset per account).

Add to `terraform/environments/test/terraform.tfvars`:

```hcl
join_existing_deployment = "prod"
```

**Important deployment order for shared accounts:**

1. Deploy **prod** environment FIRST
2. Then deploy **test** environment

This is required because test adds rules to prod's active ruleset.

### 3.4 Initialize and Deploy Infrastructure

The Makefile handles all Terraform operations including state bucket creation and Lambda packaging.

**For separate AWS accounts:**

```bash
# Deploy test environment
AWS_PROFILE=ses-mail make init ENV=test
AWS_PROFILE=ses-mail make apply ENV=test

# Deploy production environment
AWS_PROFILE=ses-mail-prod make init ENV=prod
AWS_PROFILE=ses-mail-prod make apply ENV=prod
```

**For shared AWS account (deploy in this order):**

```bash
# Deploy prod FIRST
AWS_PROFILE=ses-mail make init ENV=prod
AWS_PROFILE=ses-mail make apply ENV=prod

# Then deploy test (after prod is complete)
AWS_PROFILE=ses-mail make init ENV=test
AWS_PROFILE=ses-mail make apply ENV=test
```

**What happens during deployment:**

1. **State bucket creation**: Makefile creates `terraform-state-{account-id}` S3 bucket if it doesn't exist
2. **Lambda packaging**: Packages Lambda functions with dependencies
3. **Terraform plan**: Shows what will be created
4. **User confirmation**: You'll be prompted to approve the plan
5. **Resource creation**: Creates all AWS resources (SES, S3, Lambda, DynamoDB, EventBridge, etc.)

**Expected duration:** 5-10 minutes for first deployment.

**Success Criteria:** Terraform completes without errors and shows "Apply complete! Resources: X added, 0 changed, 0 destroyed."

### 3.5 Save Terraform Outputs

After deployment, save important infrastructure details:

```bash
# Get all outputs
cd terraform/environments/test  # or prod
terraform output

# Get specific outputs
terraform output dns_configuration_summary
terraform output myapplications_url
terraform output resource_group_url
```

**Success Criteria:** Terraform outputs show resource ARNs and URLs for your deployed infrastructure.

## 4. DNS Configuration

### 4.1 Get DNS Records from Terraform

Terraform generates all required DNS records. Retrieve them:

```bash
# For test environment
cd terraform/environments/test
terraform output dns_configuration_summary

# For production environment
cd terraform/environments/prod
terraform output dns_configuration_summary
```

The output will be grouped by domain showing all required DNS records.

### 4.2 Add DNS Records

For each domain, add the following records to your DNS provider (Route53, Cloudflare, etc.):

#### Domain Verification (TXT Record)

**Required for:** SES to verify you own the domain

- **Name**: `_amazonses.YOUR_DOMAIN`
- **Type**: TXT
- **Value**: (from Terraform output - looks like `abc123...`)
- **TTL**: 1800

#### Email Receiving (MX Record)

**Required for:** Route emails to SES

- **Name**: `YOUR_DOMAIN` (or blank/@ if zone is YOUR_DOMAIN)
- **Type**: MX
- **Priority**: 10
- **Value**: `inbound-smtp.{region}.amazonaws.com` (e.g., `inbound-smtp.ap-southeast-2.amazonaws.com`)
- **TTL**: 1800

#### DKIM Authentication (3 CNAME Records)

**Required for:** Email authentication and deliverability

For each of the 3 DKIM tokens in the Terraform output:

- **Name**: `{token}._domainkey.YOUR_DOMAIN`
- **Type**: CNAME
- **Value**: `{token}.dkim.amazonses.com`
- **TTL**: 1800

Example:

```text
abcdef123._domainkey.mail.example.com → abcdef123.dkim.amazonses.com
ghijkl456._domainkey.mail.example.com → ghijkl456.dkim.amazonses.com
mnopqr789._domainkey.mail.example.com → mnopqr789.dkim.amazonses.com
```

#### DMARC Policy (TXT Record)

**Required for:** Prevent domain spoofing

- **Name**: `_dmarc.YOUR_DOMAIN`
- **Type**: TXT
- **Value**: `v=DMARC1; p=reject; rua=mailto:dmarc@YOUR_DOMAIN` (adjust email as needed)
- **TTL**: 1800

**DMARC policy options:**

- `p=none` - Monitor only (good for testing)
- `p=quarantine` - Mark suspicious emails
- `p=reject` - Block unauthenticated emails (recommended for production)

### 4.3 Add Records via AWS Console (Route53)

If using Route53:

1. Go to: **Route53** → **Hosted zones**
2. Select your hosted zone (e.g., `example.com`)
3. Click "Create record"
4. For each record type:
   - Choose record type (TXT, MX, CNAME)
   - Enter record name
   - Enter value
   - Set TTL
   - Click "Create records"

### 4.4 Add Records via AWS CLI (Route53)

```bash
# Get DNS records in JSON format
cd terraform/environments/test  # or prod
terraform output -json dns_configuration_summary > /tmp/dns-records.json

# Then manually create records using AWS CLI
# See terraform/modules/ses-mail/README.md for detailed CLI examples
```

### 4.5 Wait for DNS Propagation

DNS changes take time to propagate globally.

**Expected wait time:** 5-15 minutes (can be up to 48 hours in rare cases)

**Check DNS propagation:**

```bash
# Check domain verification record
dig +short TXT _amazonses.mail.example.com

# Check MX record
dig +short MX mail.example.com

# Check DKIM records
dig +short CNAME abc123._domainkey.mail.example.com
```

### 4.6 Verify Domain in SES

After DNS records propagate, verify the domain is recognized by SES:

```bash
AWS_PROFILE=ses-mail aws ses get-identity-verification-attributes \
  --identities mail.example.com
```

**Expected output:**

```json
{
    "VerificationAttributes": {
        "mail.example.com": {
            "VerificationStatus": "Success"
        }
    }
}
```

**Success Criteria:** All domains show `VerificationStatus: "Success"`.

## 5. MTA-STS Setup (Optional)

MTA-STS (Mail Transfer Agent Strict Transport Security) enforces TLS encryption for email delivery.

**When to use MTA-STS:**

- ✅ You require strict TLS enforcement for email delivery
- ✅ You want to protect against SMTP downgrade attacks
- ✅ You have compliance requirements for email encryption (HIPAA, PCI-DSS, etc.)
- ✅ You're running a production email service

**Skip MTA-STS if:**

- ❌ You're just testing the system
- ❌ Standard email security (DKIM/SPF/DMARC) is sufficient for your use case
- ❌ You don't want to manage additional AWS infrastructure (CloudFront + ACM certificates)
- ❌ You need to deploy quickly without waiting for DNS/certificate validation

**Time to set up**: ~30-45 minutes (includes DNS propagation and ACM certificate validation)

**Skip this section if** `enable_mta_sts = false` in your Terraform variables.

### 5.1 Add MTA-STS DNS Records

From the Terraform output, add these records for each domain:

#### MTA-STS Policy Record (TXT)

- **Name**: `_mta-sts.YOUR_DOMAIN`
- **Type**: TXT
- **Value**: (from Terraform output - contains policy version ID)
- **TTL**: 1800

#### MTA-STS Endpoint (CNAME)

- **Name**: `mta-sts.YOUR_DOMAIN`
- **Type**: CNAME
- **Value**: (CloudFront distribution URL from Terraform output)
- **TTL**: 1800

#### ACM Validation Records (CNAME)

For each domain, add ACM validation CNAME records from Terraform output.

### 5.2 Wait for ACM Certificate Validation

ACM must validate domain ownership before creating CloudFront distribution.

```bash
# Check certificate status
AWS_PROFILE=ses-mail aws acm list-certificates --region us-east-1
```

**Expected status:** `ISSUED` for all certificates

**Wait time:** Usually 5-30 minutes after adding DNS records

### 5.3 Complete CloudFront Creation

After all ACM certificates show "ISSUED" status, run Terraform again:

```bash
AWS_PROFILE=ses-mail make plan ENV=test
AWS_PROFILE=ses-mail make apply ENV=test
```

This creates the CloudFront distributions that were skipped in the initial deployment.

**Success Criteria:** Terraform completes without errors and CloudFront distributions are created.

### 5.4 Add TLS Reporting (Optional)

SMTP TLS Reporting (TLS-RPT) provides visibility into TLS connection failures when other mail servers deliver email to your domain.

**When to use TLS-RPT:**

- ✅ You want detailed reports on TLS handshake failures
- ✅ You're troubleshooting email delivery issues
- ✅ You want to monitor MTA-STS policy violations
- ✅ You have dedicated email monitoring infrastructure

**Skip TLS-RPT if:**

- ❌ You don't have infrastructure to receive and process reports (they arrive as emails)
- ❌ You're not actively debugging email delivery
- ❌ MTA-STS monitoring alone is sufficient

**Setup time**: ~5 minutes

**DNS Record:**

- **Name**: `_smtp._tls.YOUR_DOMAIN`
- **Type**: TXT
- **Value**: `v=TLSRPTv1; rua=mailto:tlsrpt@YOUR_DOMAIN`
- **TTL**: 1800

## 6. OAuth Token Setup

### 6.1 Upload OAuth Client Credentials to SSM

Upload the `client_secret.json` file to AWS SSM Parameter Store:

```bash
# For test environment
AWS_PROFILE=ses-mail aws ssm put-parameter \
  --name "/ses-mail/test/gmail-forwarder/oauth/client-credentials" \
  --value "$(cat client_secret.json)" \
  --type SecureString \
  --overwrite

# For production environment
AWS_PROFILE=ses-mail aws ssm put-parameter \
  --name "/ses-mail/prod/gmail-forwarder/oauth/client-credentials" \
  --value "$(cat client_secret.json)" \
  --type SecureString \
  --overwrite
```

**Success Criteria:** Command completes without errors and shows `"Version": 1` (or higher).

### 6.2 Generate and Store Refresh Token

Run the refresh script to generate a new refresh token through the OAuth flow:

```bash
# For test environment
AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env test

# For production environment
AWS_PROFILE=ses-mail python3 scripts/refresh_oauth_token.py --env prod
```

**What happens:**

1. Script retrieves OAuth client credentials from SSM
2. Opens your browser to Google's OAuth consent screen
3. You review permissions and click "Allow"
4. Script exchanges authorization code for refresh token
5. Refresh token is stored in SSM Parameter Store at `/ses-mail/{env}/gmail-forwarder/oauth/refresh-token`
6. CloudWatch metric is published for expiration monitoring

**Interactive prompts:**

- Browser opens automatically
- Review requested permissions
- Click "Allow" to grant access
- Return to terminal after authorization

**Expected output:**

```text
INFO - Starting OAuth token refresh for environment: test
INFO - Retrieving OAuth client credentials from SSM
INFO - Successfully retrieved OAuth credentials
INFO - Starting interactive OAuth authorization flow
INFO - Opening browser for OAuth consent
[Browser opens]
INFO - OAuth authorization completed - obtained refresh token
INFO - Stored refresh token in SSM Parameter Store
INFO - Published token expiration metric to CloudWatch
INFO - OAuth token refresh completed successfully
```

**Success Criteria:** Script completes successfully and confirms token storage.

### 6.3 Verify OAuth Token Storage

Verify the token is properly stored:

```bash
# Check that the parameter exists
AWS_PROFILE=ses-mail aws ssm get-parameter \
  --name "/ses-mail/test/gmail-forwarder/oauth/refresh-token" \
  --query 'Parameter.Name' \
  --output text
```

**Expected output:** `/ses-mail/test/gmail-forwarder/oauth/refresh-token`

**Success Criteria:** Parameter exists in SSM.

### 6.4 Delete Local Credential Files

**Important:** For security, delete local credential files after uploading to SSM:

```bash
rm client_secret.json
rm token.json  # if it exists from local testing
```

**Success Criteria:** Local credential files are deleted.

## 7. Enable AWS Services

### 7.1 Enable Group Lifecycle Events (GLE)

The GLE auto-enablement in Terraform doesn't work yet. Enable it manually:

```bash
AWS_PROFILE=ses-mail aws resource-groups update-account-settings \
  --group-lifecycle-events-desired-status ACTIVE
```

**What this does:** Enables AWS Resource Groups to track resource lifecycle events for myApplications integration.

**Success Criteria:** Command completes without errors.

### 7.2 Enable X-Ray Transaction Search

Enable X-Ray transaction search for better trace analysis:

1. Go to: **AWS Console** → **X-Ray**
2. Click "Settings" in the left menu
3. Under "Transaction search", click "Enable"
4. Set sampling rate to 1% (or desired value)
5. Click "Save"

**Success Criteria:** Transaction search shows as "Enabled" in X-Ray settings.

## 8. Verification

### 8.1 Verify Infrastructure Deployment

Check that all key resources exist:

```bash
# Verify S3 bucket exists
AWS_PROFILE=ses-mail aws s3 ls | grep ses-mail-storage

# Verify DynamoDB table exists
AWS_PROFILE=ses-mail aws dynamodb list-tables | grep ses-mail-email-routing

# Verify Lambda functions exist
AWS_PROFILE=ses-mail aws lambda list-functions --query 'Functions[?starts_with(FunctionName, `ses-mail-`)].FunctionName'

# Verify EventBridge bus exists
AWS_PROFILE=ses-mail aws events list-event-buses --query 'EventBuses[?starts_with(Name, `ses-mail-email-routing-`)].Name'
```

**Success Criteria:** All resources exist and are listed in the output.

### 8.2 Check CloudWatch Dashboard

Access the pre-configured monitoring dashboard:

```bash
# Get dashboard URL
cd terraform/environments/test
terraform output dashboard_url
```

Or navigate manually:

1. Go to: **AWS Console** → **CloudWatch** → **Dashboards**
2. Select: `ses-mail-dashboard-test` (or `ses-mail-dashboard-prod`)

**Success Criteria:** Dashboard loads and shows widgets (may show "No data" until emails are processed).

### 8.3 View myApplications

Check the application registration:

```bash
# Get myApplications URL
cd terraform/environments/test
terraform output myapplications_url
```

Or navigate manually:

1. Go to: **AWS Console** → **Systems Manager** → **AppManager** → **Applications**
2. Select: `ses-mail-test` (or `ses-mail-prod`)

**Success Criteria:** Application exists and shows tagged resources.

### 8.4 Add First Routing Rule

Add a test routing rule to verify DynamoDB is working:

```bash
# Add a routing rule to forward test emails to your Gmail
AWS_PROFILE=ses-mail aws dynamodb put-item \
  --table-name ses-mail-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#test@mail.example.com"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "test@mail.example.com"},
    "action": {"S": "forward-to-gmail"},
    "target": {"S": "your-email@gmail.com"},
    "enabled": {"BOOL": true},
    "created_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "updated_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "description": {"S": "Test routing rule"}
  }'
```

**Replace:**

- `test@mail.example.com` with your receiving email address
- `your-email@gmail.com` with your Gmail address

**Success Criteria:** Command completes without errors.

### 8.5 Send Test Email

Send a test email to verify the complete pipeline:

```bash
# Send test email from any email client to test@mail.example.com
```

Or use AWS SES:

```bash
# Send test email via SES (from a verified sender)
AWS_PROFILE=ses-mail aws ses send-email \
  --from verified-sender@example.com \
  --destination "ToAddresses=test@mail.example.com" \
  --message "Subject={Data='Test Email'},Body={Text={Data='This is a test'}}"
```

**What should happen:**

1. Email arrives at SES
2. SES stores in S3
3. SNS notification triggers router
4. Router looks up routing rule in DynamoDB
5. EventBridge routes to Gmail forwarder queue
6. Gmail forwarder Lambda imports to Gmail
7. You receive the email in your Gmail inbox

### 8.6 Check Logs

Verify the email was processed successfully:

```bash
# Check router logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow

# Check Gmail forwarder logs
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-gmail-forwarder-test --follow
```

**Look for:**

- Router log: "Successfully enriched message" with routing decision
- Gmail forwarder log: "Successfully imported message to Gmail"

**Success Criteria:** Logs show successful processing and email appears in Gmail inbox.

### 8.7 Check X-Ray Trace

View the distributed trace for the email:

1. Go to: **AWS Console** → **X-Ray** → **Traces**
2. Filter by service: `ses-mail-router-enrichment-test`
3. Select a recent trace
4. View the service map showing: SNS → SQS → EventBridge Pipes → Router Lambda → Event Bus → SQS → Gmail Forwarder

**Success Criteria:** Trace shows complete path from SES to Gmail with no errors.

## Next Steps

Setup is complete! Now you can:

1. **Add More Routing Rules** → See [OPERATIONS.md](OPERATIONS.md#email-routing-management)
2. **Set Up Monitoring Alerts** → See [OPERATIONS.md](OPERATIONS.md#monitoring--troubleshooting)
3. **Run Integration Tests** → See [DEVELOPMENT.md](DEVELOPMENT.md#integration-testing)
4. **Subscribe to Token Expiration Alerts** → See [OPERATIONS.md](OPERATIONS.md#oauth-token-management)

## Troubleshooting Setup

### Domain Verification Fails

**Symptoms:** SES shows domain verification status as "Pending" or "Failed"

**Solutions:**

1. Verify DNS TXT record was added correctly: `dig +short TXT _amazonses.mail.example.com`
2. Wait longer for DNS propagation (up to 48 hours)
3. Check for typos in TXT record value
4. Ensure TTL is set (1800 recommended)

### OAuth Authorization Fails

**Symptoms:** Browser doesn't open, or authorization callback fails

**Solutions:**

1. Check port 8080 is not in use: `lsof -i :8080`
2. Ensure `redirect_uris` in `client_secret.json` includes `http://localhost:8080/callback`
3. Try revoking app access at <https://myaccount.google.com/permissions> and re-authorizing
4. Verify OAuth consent screen is configured in Google Cloud Console

### Terraform Apply Fails

**Symptoms:** Terraform errors during `make apply`

**Common causes:**

1. **State bucket access denied**: Check AWS credentials have S3 permissions
2. **Resource already exists**: Run `terraform import` for existing resources
3. **Lambda packaging fails**: Ensure Python dependencies install correctly
4. **SES not in production mode**: Request production access first

### Email Not Received

**Symptoms:** Test email sent but not received in Gmail

**Debugging steps:**

1. Check SES rule is active: `aws ses describe-active-receipt-rule-set`
2. Verify routing rule exists in DynamoDB
3. Check Lambda logs for errors
4. Verify OAuth token is valid and not expired
5. Check SQS queues for messages: `aws sqs get-queue-attributes --queue-url <url> --attribute-names ApproximateNumberOfMessages`

For more troubleshooting, see [OPERATIONS.md](OPERATIONS.md#monitoring--troubleshooting).
