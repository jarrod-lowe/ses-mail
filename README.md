# SES Mail System

## Initial Google Cloud Setup

* Go to <https://console.cloud.google.com>
* Click the current project (top left next to "Google Cloud")
* Click "New Project"
* Name it "ses-mail"
* Leave the organisation as "No organisation"
* Create
* ☰ -> APIs and services -> Library
* Search for "Gmail API"
* Select "Gmail API"
* Click "Enable"
* Select "OAuth consent screen" in the left-side menu
* It will say "Google auth platform not configured yet" - Click "Get started"
* App name: ses-mail
* User support email: select your email address
* Next
* Select External
* Next
* Add your email address for the contact address
* Next
* Agree to the policy
* Continue
* Create
* Audience -> Add users
* Add your gmail address
* Save
* Clients -> Create client
* Select "Create OAuth client"
* Application type: Desktop app
* Name: ses-mail
* Create
* Select "Download JSON" - and save it for later
* OK
* From the left menu -> Data Access -> Add or remove scopes
* Add "Gmail API .../auth/gmail.insert"
* Update
* Save
* Put the client secret file in this directory, named "client_secret.json"
* `python3 -m venv .venv`
* Either source the file mentioned, or let VSCode handle it and create a new terminal
* `pip3 install -r requirements.txt`
* `./scripts/create_refresh_token.py`
* In the browser window that pops up, select your account
* Continue
* Continue
* Close the tab
* You can use the scripts in `scripts/` to test the token
* After setting up the infrastructure, set the token parameter to the value of token.json
* Delete the `client_secret.json` and `token.json` files from the local filesystem

## Terraform Infrastructure Setup

Once you have created your Gmail OAuth token (above), you can deploy the AWS infrastructure:

### Directory Structure

The infrastructure is organized into environments and a reusable module:

```plain
terraform/
├── environments/
│   ├── test/          # Test environment configuration
│   └── prod/          # Production environment configuration
└── modules/
    └── ses-mail/      # Reusable SES mail module
```

### Quick Start

1. The infrastructure uses a Terraform state bucket in S3. The Makefile will automatically create `terraform-state-{account-id}` in your AWS account on first run.

2. Review and customize the configuration for your environment:

   ```bash
   # For test environment
   vi terraform/environments/test/terraform.tfvars

   # For production environment
   vi terraform/environments/prod/terraform.tfvars

   # Update domain(s) and SNS topic ARN as needed
   # domain should be a list: ["mail.example.com", "mail2.example.com"]
   ```

3. Deploy the infrastructure (first pass):

   ```bash
   # For test environment
   make init ENV=test   # Initialize Terraform and create state bucket
   make plan ENV=test   # Package Lambda and create a plan file
   make apply ENV=test  # Apply the plan (creates resources and outputs DNS records)

   # For production environment
   make init ENV=prod   # Initialize Terraform and create state bucket
   make plan ENV=prod   # Package Lambda and create a plan file
   make apply ENV=prod  # Apply the plan (creates resources and outputs DNS records)
   ```

   **Note:** The `make plan` target automatically packages the Lambda function with its dependencies. If MTA-STS is enabled, the first apply will create the ACM certificate but CloudFront creation will fail. This is expected - continue to step 4.

4. Configure DNS records in Route53:

   First, get the DNS records from Terraform:

   ```bash
   # For test environment
   cd terraform/environments/test
   terraform output dns_configuration_summary

   # For production environment
   cd terraform/environments/prod
   terraform output dns_configuration_summary
   ```

   The output will be grouped by domain. For each domain, you'll need to add the following records to your Route53 hosted zone:

   **Domain Verification (TXT record)**

   * Name: `_amazonses.YOUR_DOMAIN`
   * Type: TXT
   * Value: The verification token from the output
   * TTL: 1800 (or default)

   **Email Receiving (MX record)**

   * Name: `YOUR_DOMAIN` (or leave blank if zone is YOUR_DOMAIN)
   * Type: MX
   * Value: `10 inbound-smtp.ap-southeast-2.amazonaws.com` (adjust region as needed)
   * TTL: 1800 (or default)

   **DKIM Authentication (3 CNAME records per domain)**

   * For each of the 3 DKIM tokens in the output:
     * Name: `{token}._domainkey.YOUR_DOMAIN`
     * Type: CNAME
     * Value: `{token}.dkim.amazonses.com`
     * TTL: 1800 (or default)

   **DMARC Policy (TXT record per domain)**

   * Name: `_dmarc.YOUR_DOMAIN`
   * Type: TXT
   * Value: `v=DMARC1; p=reject; rua=mailto:dmarc@YOUR_DOMAIN` (if prefix configured)
   * TTL: 1800 (or default)
   * Purpose: Prevents others from spoofing your domain

   **MTA-STS (if enabled - records per domain)**

   * Name: `_mta-sts.YOUR_DOMAIN`
   * Type: TXT
   * Value: From terraform output (contains policy ID)
   * TTL: 1800 (or default)

   * Name: `mta-sts.YOUR_DOMAIN`
   * Type: CNAME
   * Value: CloudFront distribution URL from output
   * TTL: 1800 (or default)

   * ACM validation CNAME records (from terraform output, one set per domain)

   **TLS Reporting (if email configured - per domain)**

   * Name: `_smtp._tls.YOUR_DOMAIN`
   * Type: TXT
   * Value: `v=TLSRPTv1; rua=mailto:tlsrpt@YOUR_DOMAIN`
   * TTL: 1800 (or default)

   **Via AWS Console:**
   1. Go to Route53 → Hosted zones
   2. Select your hosted zone
   3. Click "Create record"
   4. Add each record as shown above

   **Via AWS CLI:**

   ```bash
   # Get the records in JSON format
   cd terraform/environments/test  # or prod
   terraform output -json dns_configuration_summary > /tmp/dns-records.json

   # Then manually create records or use change-resource-record-sets
   # See terraform/modules/ses-mail/README.md for detailed CLI examples
   ```

   Wait 5-15 minutes for DNS propagation, then verify:

   ```bash
   aws ses get-identity-verification-attributes --identities mail.example.com mail2.example.com
   ```

5. Complete MTA-STS setup (if enabled):

   After adding the DNS records (including ACM validation records for each domain), wait for all ACM certificates to validate:

   ```bash
   # Check certificate status (should show ISSUED for all)
   aws acm list-certificates --region us-east-1
   ```

   Once all certificates are validated (usually 5-30 minutes), run terraform again to create CloudFront distributions:

   ```bash
   make plan ENV=test  # or ENV=prod
   make apply ENV=test  # or ENV=prod
   ```

6. Upload your Gmail token to SSM Parameter Store:

   ```bash
   # For test environment
   aws ssm put-parameter \
     --name "/ses-mail/test/gmail-token" \
     --value "$(cat token.json)" \
     --type SecureString \
     --overwrite

   # For production environment
   aws ssm put-parameter \
     --name "/ses-mail/prod/gmail-token" \
     --value "$(cat token.json)" \
     --type SecureString \
     --overwrite
   ```

### Workflow

All commands now require an `ENV` parameter to specify which environment (test or prod):

* **make package ENV=test**: Packages the Lambda function with dependencies (automatically run by make plan)
* **make plan ENV=test**: Creates a plan file showing what changes will be made
* **make apply ENV=test**: Applies the plan file (depends on plan, so will create it if missing)
* **make plan-destroy ENV=test**: Creates a destroy plan
* **make destroy ENV=test**: Applies the destroy plan (depends on plan-destroy)

For detailed instructions and configuration options, see [terraform/modules/ses-mail/README.md](terraform/modules/ses-mail/README.md).
