.PHONY: help package init plan apply plan-destroy destroy clean

# Get environment from terraform.tfvars or default to production
ENVIRONMENT ?= $(shell grep '^environment' terraform/terraform.tfvars 2>/dev/null | cut -d'=' -f2 | tr -d ' "' || echo "production")
AWS_REGION ?= $(shell grep '^aws_region' terraform/terraform.tfvars 2>/dev/null | cut -d'=' -f2 | tr -d ' "' || echo "ap-southeast-2")

# Ensure state bucket and get its name
STATE_BUCKET = $(shell terraform/scripts/ensure-state-bucket.sh | grep TERRAFORM_STATE_BUCKET | cut -d'=' -f2)

# Terraform backend config
BACKEND_CONFIG = -backend-config="bucket=$(STATE_BUCKET)" \
                 -backend-config="key=ses-mail/$(ENVIRONMENT).tfstate" \
                 -backend-config="region=$(AWS_REGION)"

# Default target
help:
	@echo "SES Mail Infrastructure - Makefile targets:"
	@echo ""
	@echo "  make package       - Package Lambda function with dependencies"
	@echo "  make init          - Initialize Terraform (creates state bucket)"
	@echo "  make plan          - Create Terraform plan file"
	@echo "  make apply         - Apply the plan file (requires plan)"
	@echo "  make plan-destroy  - Create destroy plan file"
	@echo "  make destroy       - Apply the destroy plan (requires plan-destroy)"
	@echo "  make clean         - Clean up Terraform files and plans"
	@echo ""
	@echo "Current environment: $(ENVIRONMENT)"
	@echo "Current region: $(AWS_REGION)"
	@echo ""

# Package Lambda function with dependencies
terraform/lambda/package: requirements.txt terraform/lambda/email_processor.py
	@echo "Packaging Lambda function with dependencies..."
	@rm -rf terraform/lambda/package
	@mkdir -p terraform/lambda/package
	@pip3 install -q -r requirements.txt -t terraform/lambda/package
	@cp terraform/lambda/email_processor.py terraform/lambda/package/
	@echo "Lambda package created"

package: terraform/lambda/package

# Initialize Terraform (depends on state bucket existing)
terraform/.terraform: terraform/scripts/ensure-state-bucket.sh terraform/terraform.tfvars
	@echo "Ensuring state bucket exists..."
	@terraform/scripts/ensure-state-bucket.sh
	@echo "Initializing Terraform..."
	cd terraform && terraform init $(BACKEND_CONFIG)
	@touch terraform/.terraform

init: terraform/.terraform

# Create plan file
terraform/terraform.plan: terraform/.terraform terraform/*.tf terraform/lambda/package
	@echo "Creating Terraform plan..."
	cd terraform && terraform plan -out=terraform.plan
	@echo "Plan created: terraform/terraform.plan"

plan: terraform/terraform.plan

# Apply the plan file
apply: terraform/terraform.plan
	@echo "Applying Terraform plan..."
	cd terraform && terraform apply terraform.plan && rm -f terraform.plan || { rm -f terraform.plan; exit 1; }
	@echo "Plan applied and removed"

# Create destroy plan file
terraform/terraform.destroy.plan: terraform/.terraform
	@echo "Creating Terraform destroy plan..."
	cd terraform && terraform plan -destroy -out=terraform.destroy.plan
	@echo "Destroy plan created: terraform/terraform.destroy.plan"

plan-destroy: terraform/terraform.destroy.plan

# Apply the destroy plan
destroy: terraform/terraform.destroy.plan
	@echo "Applying Terraform destroy plan..."
	cd terraform && terraform apply terraform.destroy.plan && rm -f terraform.destroy.plan || { rm -f terraform.destroy.plan; exit 1; }
	@echo "Destroy plan applied and removed"

# Clean up Terraform files
clean:
	@echo "Cleaning up Terraform files..."
	rm -rf terraform/.terraform
	rm -f terraform/.terraform.lock.hcl
	rm -f terraform/terraform.plan
	rm -f terraform/terraform.destroy.plan
	rm -f terraform/*.tfstate
	rm -f terraform/*.tfstate.backup
	rm -f terraform/lambda/*.zip
	rm -rf terraform/lambda/package
	@echo "Clean-up complete"
