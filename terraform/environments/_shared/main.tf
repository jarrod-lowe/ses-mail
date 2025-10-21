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
      version = "~> 6.0"
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
      Application = "ses-mail-${var.environment}" # Combined tag for AppRegistry tag-sync
    }
  }

  ignore_tags {
    keys = ["awsApplication"]
  }
}

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "ses-mail"
      ManagedBy   = "terraform"
      Environment = var.environment
      Application = "ses-mail-${var.environment}" # Combined tag for AppRegistry tag-sync
    }
  }

  ignore_tags {
    keys = ["awsApplication"]
  }
}

module "ses_mail" {
  source = "../../modules/ses-mail"

  providers = {
    aws.us_east_1 = aws.us_east_1
  }

  aws_region                     = var.aws_region
  environment                    = var.environment
  domain                         = var.domain
  email_retention_days           = var.email_retention_days
  alarm_sns_topic_arn            = var.alarm_sns_topic_arn
  alarm_email_count_threshold    = var.alarm_email_count_threshold
  alarm_rejection_rate_threshold = var.alarm_rejection_rate_threshold
  mta_sts_mode                   = var.mta_sts_mode
  dmarc_rua_prefix               = var.dmarc_rua_prefix
  tlsrpt_rua_prefix              = var.tlsrpt_rua_prefix
  spf_include_domains            = var.spf_include_domains
  spf_a_records                  = var.spf_a_records
  spf_mx_records                 = var.spf_mx_records
  spf_policy                     = var.spf_policy
  backup_mx_records              = var.backup_mx_records
}
