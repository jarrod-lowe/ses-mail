.PHONY: help package init plan apply plan-destroy destroy clean backup-tfvars restore-tfvars fmt validate outputs

# Environment selection (test or prod)
ENV ?= test

# Validate environment
ifeq ($(filter $(ENV),test prod),)
$(error ENV must be 'test' or 'prod'. Usage: make <target> ENV=test)
endif

# Environment directory
ENV_DIR = terraform/environments/$(ENV)
MODULE_DIR = terraform/modules/ses-mail

# Get configuration from environment's terraform.tfvars
AWS_REGION ?= $(shell grep '^aws_region' $(ENV_DIR)/terraform.tfvars 2>/dev/null | cut -d'=' -f2 | tr -d ' "' || echo "ap-southeast-2")
ENVIRONMENT ?= $(ENV)

# Ensure state bucket and get its name
STATE_BUCKET = $(shell $(MODULE_DIR)/scripts/ensure-state-bucket.sh | grep TERRAFORM_STATE_BUCKET | cut -d'=' -f2)

# Terraform backend config
BACKEND_CONFIG = -backend-config="bucket=$(STATE_BUCKET)" \
                 -backend-config="key=ses-mail/$(ENVIRONMENT).tfstate" \
                 -backend-config="region=$(AWS_REGION)"

# Default target
help:
	@echo "SES Mail Infrastructure - Makefile targets:"
	@echo ""
	@echo "  make package ENV=<env>       - Package Lambda function with dependencies"
	@echo "  make init ENV=<env>          - Initialize Terraform (creates state bucket)"
	@echo "  make plan ENV=<env>          - Create Terraform plan file"
	@echo "  make apply ENV=<env>         - Apply the plan file (requires plan)"
	@echo "  make plan-destroy ENV=<env>  - Create destroy plan file"
	@echo "  make destroy ENV=<env>       - Apply the destroy plan (requires plan-destroy)"
	@echo "  make clean ENV=<env>         - Clean up Terraform files and plans"
	@echo ""
	@echo "Environments: test, prod"
	@echo "Current environment: $(ENV)"
	@echo "Current region: $(AWS_REGION)"
	@echo ""
	@echo "Examples:"
	@echo "  make init ENV=test"
	@echo "  make plan ENV=prod"
	@echo ""

# Package Lambda functions with dependencies
$(MODULE_DIR)/lambda/package: requirements.txt $(MODULE_DIR)/lambda/router_enrichment.py $(MODULE_DIR)/lambda/gmail_forwarder.py $(MODULE_DIR)/lambda/bouncer.py $(MODULE_DIR)/lambda/smtp_credential_manager.py
	@echo "Packaging Lambda functions with dependencies..."
	@rm -rf $(MODULE_DIR)/lambda/package
	@mkdir -p $(MODULE_DIR)/lambda/package
	@pip3 install -q -r requirements.txt -t $(MODULE_DIR)/lambda/package
	@cp $(MODULE_DIR)/lambda/router_enrichment.py $(MODULE_DIR)/lambda/package/
	@cp $(MODULE_DIR)/lambda/gmail_forwarder.py $(MODULE_DIR)/lambda/package/
	@cp $(MODULE_DIR)/lambda/bouncer.py $(MODULE_DIR)/lambda/package/
	@cp $(MODULE_DIR)/lambda/smtp_credential_manager.py $(MODULE_DIR)/lambda/package/
	@echo "Lambda package created"

package: $(MODULE_DIR)/lambda/package

# Initialize Terraform (depends on state bucket existing)
$(ENV_DIR)/.terraform: $(MODULE_DIR)/scripts/ensure-state-bucket.sh $(ENV_DIR)/terraform.tfvars terraform/.fmt
	@echo "Ensuring state bucket exists..."
	@$(MODULE_DIR)/scripts/ensure-state-bucket.sh
	@echo "Initializing Terraform for $(ENV) environment..."
	cd $(ENV_DIR) && terraform init $(BACKEND_CONFIG)
	@touch $(ENV_DIR)/.terraform

init: $(ENV_DIR)/.terraform

# Create plan file
$(ENV_DIR)/terraform.plan: $(ENV_DIR)/.terraform $(ENV_DIR)/*.tf $(MODULE_DIR)/*.tf $(MODULE_DIR)/lambda/package
	@echo "Creating Terraform plan for $(ENV) environment..."
	cd $(ENV_DIR) && terraform plan -out=terraform.plan
	@echo "Plan created: $(ENV_DIR)/terraform.plan"

plan: $(ENV_DIR)/terraform.plan

# Apply the plan file
apply: $(ENV_DIR)/terraform.plan
	@echo "Applying Terraform plan for $(ENV) environment..."
	cd $(ENV_DIR) && terraform apply terraform.plan && rm -f terraform.plan || { rm -f terraform.plan; exit 1; }
	@echo "Plan applied and removed"

# Create destroy plan file
$(ENV_DIR)/terraform.destroy.plan: $(ENV_DIR)/.terraform
	@echo "Creating Terraform destroy plan for $(ENV) environment..."
	cd $(ENV_DIR) && terraform plan -destroy -out=terraform.destroy.plan
	@echo "Destroy plan created: $(ENV_DIR)/terraform.destroy.plan"

plan-destroy: $(ENV_DIR)/terraform.destroy.plan

# Apply the destroy plan
destroy: $(ENV_DIR)/terraform.destroy.plan
	@echo "Applying Terraform destroy plan for $(ENV) environment..."
	cd $(ENV_DIR) && terraform apply terraform.destroy.plan && rm -f terraform.destroy.plan || { rm -f terraform.destroy.plan; exit 1; }
	@echo "Destroy plan applied and removed"

fmt: terraform/.fmt

terraform/.fmt: terraform/environments/*/*.tf terraform/modules/ses-mail/*.tf
	cd terraform && terraform fmt -recursive
	touch $@

validate: $(ENV_DIR)/.terraform
	cd $(ENV_DIR) && terraform validate

outputs: $(ENV_DIR)/.terraform
	cd $(ENV_DIR) && terraform output

# Clean up Terraform files
clean:
	@echo "Cleaning up Terraform files for $(ENV) environment..."
	rm -rf $(ENV_DIR)/.terraform
	rm -f $(ENV_DIR)/.terraform.lock.hcl
	rm -f $(ENV_DIR)/terraform.plan
	rm -f $(ENV_DIR)/terraform.destroy.plan
	rm -f $(ENV_DIR)/*.tfstate
	rm -f $(ENV_DIR)/*.tfstate.backup
	rm -f $(MODULE_DIR)/lambda/*.zip
	rm -rf $(MODULE_DIR)/lambda/package
	@echo "Clean-up complete for $(ENV) environment"
