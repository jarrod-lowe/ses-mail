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

7. Enable GLE

The auto-enablement of the GLE does not work yet. Run it manually with:

```bash
aws resource-groups update-account-settings \
--group-lifecycle-events-desired-status ACTIVE
```

### Workflow

All commands now require an `ENV` parameter to specify which environment (test or prod):

* **make package ENV=test**: Packages the Lambda function with dependencies (automatically run by make plan)
* **make plan ENV=test**: Creates a plan file showing what changes will be made
* **make apply ENV=test**: Applies the plan file (depends on plan, so will create it if missing)
* **make plan-destroy ENV=test**: Creates a destroy plan
* **make destroy ENV=test**: Applies the destroy plan (depends on plan-destroy)

For detailed instructions and configuration options, see [terraform/modules/ses-mail/README.md](terraform/modules/ses-mail/README.md).

## Architecture

### Email Processing Flow

The system uses an event-driven architecture for processing incoming emails:

```text
SES → S3 → SNS (X-Ray tracing) → SQS Input Queue → EventBridge Pipes[router enrichment] → EventBridge Event Bus → Handler Queues → Lambda Processors
```

**Current Implementation Status:**

1. **SES Receipt** - Email arrives and is scanned for spam/virus
2. **S3 Storage + SNS Notification** - SES stores email in S3 and triggers SNS (single action)
3. **SNS Topic** - Receives notification with X-Ray Active tracing enabled
4. **SQS Input Queue** - Receives messages from SNS for EventBridge Pipes processing
5. **Legacy Lambda Actions** - Direct lambda invocations (TO BE REMOVED)

**Infrastructure Components:**

* **SNS Topic**: `ses-email-processing-{environment}` with X-Ray Active tracing
* **SQS Input Queue**: `ses-email-input-{environment}` with 3-retry DLQ policy
* **Dead Letter Queue**: `ses-email-input-dlq-{environment}` with 14-day retention
* **CloudWatch Alarms**: Monitors DLQ messages and queue age

**X-Ray Distributed Tracing:**

The SNS topic is configured with Active tracing to initiate X-Ray traces for the entire email processing pipeline. This allows end-to-end visibility of email processing across all AWS services.

## AWS myApplications Integration

The infrastructure is registered with AWS myApplications through AWS Service Catalog AppRegistry, providing application-level visibility and management capabilities in the AWS Console.

### What is myApplications?

AWS myApplications provides a centralized view to manage your applications and their resources. It integrates with AppRegistry to:

* View application health and status
* Track costs at the application level
* Monitor operational metrics
* Manage application metadata and documentation
* Access application resources in one place

**Accessing myApplications:**

```bash
# Get the myApplications URL from Terraform output
cd terraform/environments/test  # or prod
terraform output myapplications_url

# Or navigate in AWS Console:
# AWS Console → Systems Manager → AppManager → Applications
# Select "ses-mail-{environment}"
```

The AppRegistry application automatically discovers and includes all resources tagged with `Application=ses-mail-{environment}`. All infrastructure resources are tagged via Terraform provider `default_tags`, ensuring they automatically appear in the myApplications view.

**Note**: Optional tag-sync automation requires AWS Group Lifecycle Events (GLE) to be enabled at the account level. However, resources will still appear in myApplications based on the `Application` tag alone.

## AWS Resource Groups

The infrastructure includes an AWS Resource Group that provides a single view of all resources for each environment. All resources are automatically tagged with:

* **Project**: `ses-mail`
* **ManagedBy**: `terraform`
* **Environment**: `test` or `prod`
* **Application**: `ses-mail-{environment}` (combined tag for myApplications integration)

The Resource Group uses these tags to organize resources, making it easy to:

* View all related resources in one place
* Track costs by environment
* Manage resources collectively
* Monitor resource health

**Accessing the Resource Group:**

```bash
# Get the Resource Group URL from Terraform output
cd terraform/environments/test  # or prod
terraform output resource_group_url

# Or view directly in AWS Console:
# https://console.aws.amazon.com/resource-groups/group/ses-mail-{environment}
```

The Resource Group includes all infrastructure components:

* S3 buckets
* Lambda functions
* DynamoDB tables
* SQS queues
* SNS topics
* CloudWatch alarms and log groups
* IAM roles
* SES resources

## Email Routing Configuration

### DynamoDB Routing Rules Table

The system uses a DynamoDB table to store email routing rules. The table uses a single-table design pattern for extensibility.

**Table Structure:**

* **Primary Key (PK)**: `ROUTE#<email-pattern>` (e.g., `ROUTE#support@example.com`, `ROUTE#*@example.com`, `ROUTE#*`)
* **Sort Key (SK)**: `RULE#v1` (allows versioning)
* **Billing**: PAY_PER_REQUEST (no standing costs)

**Routing Rules Attributes:**

* `entity_type`: `ROUTE` (for filtering)
* `recipient`: Email pattern (denormalized from PK)
* `action`: `forward-to-gmail` | `bounce`
* `target`: Gmail address for forwarding, or empty for bounces
* `enabled`: Boolean (true/false)
* `created_at`: ISO timestamp
* `updated_at`: ISO timestamp
* `description`: Human-readable description

**Hierarchical Matching:**

The router lambda performs lookups in this order (first match wins):

1. Exact match: `ROUTE#user+tag@example.com`
2. Normalized match: `ROUTE#user@example.com` (removes +tag)
3. Domain wildcard: `ROUTE#*@example.com`
4. Global wildcard: `ROUTE#*`

**Managing Routing Rules:**

Add rules via AWS CLI or Console:

```bash
# Example: Forward support emails to Gmail
aws dynamodb put-item \
  --table-name ses-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#support@example.com"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "support@example.com"},
    "action": {"S": "forward-to-gmail"},
    "target": {"S": "your-email@gmail.com"},
    "enabled": {"BOOL": true},
    "created_at": {"S": "2025-01-18T10:00:00Z"},
    "updated_at": {"S": "2025-01-18T10:00:00Z"},
    "description": {"S": "Forward support emails to Gmail"}
  }'

# Example: Bounce all unmatched emails (default rule)
aws dynamodb put-item \
  --table-name ses-email-routing-test \
  --item '{
    "PK": {"S": "ROUTE#*"},
    "SK": {"S": "RULE#v1"},
    "entity_type": {"S": "ROUTE"},
    "recipient": {"S": "*"},
    "action": {"S": "bounce"},
    "target": {"S": ""},
    "enabled": {"BOOL": true},
    "created_at": {"S": "2025-01-18T10:00:00Z"},
    "updated_at": {"S": "2025-01-18T10:00:00Z"},
    "description": {"S": "Default: bounce all unmatched emails"}
  }'
```
