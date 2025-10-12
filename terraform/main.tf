terraform {
  required_version = ">= 1.0"

  backend "s3" {
    # Bucket name is set via -backend-config in Makefile
    # Key includes environment from -backend-config
    # Region is set via -backend-config
    encrypt = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "ses-mail"
      ManagedBy   = "terraform"
      Environment = var.environment
    }
  }
}
