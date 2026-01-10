# Upgrading Guide

This guide covers upgrading the SES Mail system infrastructure, dependencies, and configurations.

## Table of Contents

- [Terraform Version Upgrades](#terraform-version-upgrades)
- [Lambda Function Updates](#lambda-function-updates)
- [Python Dependency Updates](#python-dependency-updates)
- [AWS Service Updates](#aws-service-updates)
- [Database Schema Changes](#database-schema-changes)
- [Multi-Environment Upgrades](#multi-environment-upgrades)

## Terraform Version Upgrades

### Upgrading Terraform CLI

**Current version**: 1.0+
**Recommended approach**: Incremental upgrades (e.g., 1.5 → 1.6 → 1.7)

```bash
# Check current Terraform version
terraform version

# Upgrade Terraform CLI (macOS with Homebrew)
brew upgrade terraform

# Verify new version
terraform version
```

**After upgrading Terraform:**

1. **Run plan to check for deprecations**:

   ```bash
   AWS_PROFILE=ses-mail make validate ENV=test
   AWS_PROFILE=ses-mail make plan ENV=test
   ```

2. **Review changes carefully** - Terraform version upgrades may:
   - Change resource attribute names
   - Introduce new required fields
   - Deprecate old syntax

3. **Test in test environment first**:

   ```bash
   AWS_PROFILE=ses-mail make apply ENV=test
   ```

4. **Only upgrade prod after testing**:

   ```bash
   AWS_PROFILE=ses-mail make apply ENV=prod
   ```

### Upgrading AWS Provider Version

**Location**: `terraform/environments/{env}/main.tf`

```hcl
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"  # Update this version
    }
  }
}
```

**Upgrade process:**

1. Check AWS provider changelog: <https://github.com/hashicorp/terraform-provider-aws/releases>
2. Update version in `main.tf`
3. Run `terraform init -upgrade`
4. Run `make plan` and review changes
5. Test in test environment first

## Lambda Function Updates

### Updating Lambda Code

Lambda functions are automatically packaged during Terraform apply. To update:

#### Option A: Modify code and deploy

```bash
# 1. Edit Lambda function code
vi terraform/modules/ses-mail/lambda/router_enrichment.py

# 2. Plan and apply (Makefile handles packaging)
AWS_PROFILE=ses-mail make plan ENV=test
AWS_PROFILE=ses-mail make apply ENV=test

# 3. Verify deployment
AWS_PROFILE=ses-mail aws lambda get-function \
  --function-name ses-mail-router-enrichment-test \
  --query 'Configuration.LastModified'
```

#### Option B: Force repackage without code changes

```bash
# Remove existing package to force rebuild
rm -f terraform/modules/ses-mail/lambda_router_enrichment.zip

# Repackage and deploy
AWS_PROFILE=ses-mail make package ENV=test
AWS_PROFILE=ses-mail make apply ENV=test
```

### Updating Lambda Runtime

To upgrade Python runtime (e.g., 3.12 → 3.13):

**Location**: `terraform/modules/ses-mail/lambda.tf`

```hcl
resource "aws_lambda_function" "router_enrichment" {
  runtime = "python3.13"  # Update this
  # ...
}
```

**Important**:

1. Test locally with new Python version first
2. Update dependencies in `requirements.txt` if needed
3. Deploy to test environment and run integration tests
4. Monitor CloudWatch Logs for compatibility issues

## Python Dependency Updates

### Updating Lambda Dependencies

**Location**: `requirements.txt`

```bash
# 1. Update requirements.txt
vi requirements.txt

# 2. Test locally (optional)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Run local tests

# 3. Deploy (Makefile installs dependencies automatically)
AWS_PROFILE=ses-mail make clean
AWS_PROFILE=ses-mail make package ENV=test
AWS_PROFILE=ses-mail make apply ENV=test

# 4. Test deployed functions
source .venv/bin/activate
AWS_PROFILE=ses-mail python3 scripts/integration_test.py --env test
```

**Critical dependencies to watch:**

- `google-auth` / `google-auth-oauthlib` (Gmail API authentication)
- `google-api-python-client` (Gmail API)
- `boto3` (AWS SDK - usually managed by Lambda runtime)

**Breaking change checklist:**

- [ ] Review dependency changelog
- [ ] Update authentication code if OAuth library changed
- [ ] Test token refresh flow
- [ ] Test email forwarding end-to-end

## AWS Service Updates

### Enabling New AWS Features

When AWS releases new features for services used in this project:

#### Example: EventBridge Pipes new features

1. Check if Terraform AWS provider supports the feature
2. Update provider version if needed (see above)
3. Add feature configuration to Terraform
4. Test in test environment

### Upgrading Lambda Layers (Future)

If you add Lambda layers for dependencies:

```hcl
resource "aws_lambda_layer_version" "dependencies" {
  layer_name = "ses-mail-dependencies"
  filename   = "lambda_layer.zip"

  compatible_runtimes = ["python3.12"]
}
```

**Update process:**

1. Build new layer with updated dependencies
2. Deploy new layer version
3. Update Lambda functions to use new layer version
4. Test thoroughly before removing old layer

## Database Schema Changes

### Adding New DynamoDB Attributes

The DynamoDB table uses single-table design with generic PK/SK. Adding new attributes is safe:

```bash
# Example: Adding a new attribute to routing rules
AWS_PROFILE=ses-mail aws dynamodb update-item \
  --table-name ses-mail-email-routing-test \
  --key '{"PK": {"S": "ROUTE#test@example.com"}, "SK": {"S": "RULE#v1"}}' \
  --update-expression "SET new_attribute = :val" \
  --expression-attribute-values '{":val": {"S": "value"}}'
```

**Safe changes:**

- Adding new attributes
- Adding new entity types (e.g., `CONFIG#`, `METRICS#`)

**Breaking changes (require migration):**

- Changing PK/SK format
- Renaming critical attributes (`action`, `target`, `recipient`)
- Changing attribute types (String → Number)

### DynamoDB Migration Process

For breaking schema changes:

1. **Create migration script**:

   ```python
   # scripts/migrate_dynamodb_schema.py
   # Scan table, transform items, write back
   ```

2. **Test on copy of table**:

   ```bash
   # Create table backup
   AWS_PROFILE=ses-mail aws dynamodb create-backup \
     --table-name ses-mail-email-routing-test \
     --backup-name pre-migration-backup

   # Run migration on test table
   python3 scripts/migrate_dynamodb_schema.py --table ses-mail-email-routing-test --dry-run
   python3 scripts/migrate_dynamodb_schema.py --table ses-mail-email-routing-test
   ```

3. **Verify migration**:

   ```bash
   # Check data integrity
   AWS_PROFILE=ses-mail aws dynamodb scan --table-name ses-mail-email-routing-test

   # Run integration tests
   source .venv/bin/activate
   AWS_PROFILE=ses-mail python3 scripts/integration_test.py --env test
   ```

4. **Update Lambda code** to use new schema

5. **Deploy Lambda updates** before migrating prod data

## Multi-Environment Upgrades

### Deployment Order

**For shared AWS account** (`join_existing_deployment = "prod"` in test):

1. **Upgrade prod first**:

   ```bash
   AWS_PROFILE=ses-mail make apply ENV=prod
   ```

2. **Then upgrade test**:

   ```bash
   AWS_PROFILE=ses-mail make apply ENV=test
   ```

**For separate AWS accounts**: Upgrade test first, then prod after validation.

### Staged Rollout

For major changes:

1. **Test environment** (days 1-7):
   - Deploy changes
   - Run integration tests
   - Monitor for one week

2. **Production environment** (day 8+):
   - Deploy changes
   - Monitor closely for 24-48 hours
   - Have rollback plan ready

### Rollback Procedure

If upgrade causes issues:

#### Option A: Terraform rollback

```bash
# View Terraform state history
aws s3 ls s3://terraform-state-{account-id}/ses-mail/

# Restore previous state (CAREFUL!)
aws s3 cp s3://terraform-state-{account-id}/ses-mail/test.tfstate.backup \
  s3://terraform-state-{account-id}/ses-mail/test.tfstate

# Apply previous configuration
git checkout <previous-commit>
AWS_PROFILE=ses-mail make apply ENV=test
```

#### Option B: Lambda function rollback

```bash
# List function versions
AWS_PROFILE=ses-mail aws lambda list-versions-by-function \
  --function-name ses-mail-router-enrichment-test

# Update alias to previous version
AWS_PROFILE=ses-mail aws lambda update-alias \
  --function-name ses-mail-router-enrichment-test \
  --name live \
  --function-version <previous-version>
```

## Best Practices

### Before Any Upgrade

1. **Backup critical data**:
   - DynamoDB table backup
   - SSM parameters export
   - Terraform state snapshot

2. **Review changelogs**:
   - Terraform provider changes
   - AWS service updates
   - Python dependency updates

3. **Test locally** when possible:
   - Lambda functions with new dependencies
   - Terraform plan output

### During Upgrade

1. **Test environment first** - always
2. **Monitor CloudWatch** - watch for errors
3. **Run integration tests** - verify end-to-end flow
4. **Check DLQs** - ensure no failed messages

### After Upgrade

1. **Monitor for 24-48 hours**:
   - CloudWatch alarms
   - DLQ depth
   - Lambda error rates

2. **Verify all features**:
   - Email forwarding
   - OAuth token refresh
   - Routing rules

3. **Update documentation** if needed

## Troubleshooting Upgrades

### Terraform State Lock Issues

```bash
# If state is locked
aws s3 rm s3://terraform-state-{account-id}/ses-mail/.terraform.lock.hcl
```

### Lambda Deployment Fails

```bash
# Check Lambda logs for errors
AWS_PROFILE=ses-mail aws logs tail /aws/lambda/ses-mail-router-enrichment-test --follow

# Verify IAM permissions
AWS_PROFILE=ses-mail aws lambda get-function --function-name ses-mail-router-enrichment-test
```

### DynamoDB Migration Fails

```bash
# Restore from backup
AWS_PROFILE=ses-mail aws dynamodb restore-table-from-backup \
  --target-table-name ses-mail-email-routing-test \
  --backup-arn <backup-arn>
```

## Further Reading

- [Terraform Upgrade Guide](https://developer.hashicorp.com/terraform/language/upgrade-guides)
- [AWS Provider Upgrade Guide](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/guides/version-5-upgrade)
- [Lambda Runtime Upgrade Guide](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html)
