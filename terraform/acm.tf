# ACM Certificate for MTA-STS (must be in us-east-1 for CloudFront)
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "ses-mail"
      ManagedBy   = "terraform"
      Environment = var.environment
    }
  }
}

# ACM certificate for mta-sts subdomain (one per domain)
resource "aws_acm_certificate" "mta_sts" {
  for_each = var.mta_sts_mode != "none" ? toset(var.domain) : []
  provider = aws.us_east_1

  domain_name       = "mta-sts.${each.value}"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# Note: Certificate validation is NOT automated here
# The user must:
# 1. Run terraform apply to create the certificate and get validation DNS records
# 2. Add the validation DNS records to Route53
# 3. Wait for AWS to validate the certificate (5-30 minutes)
# 4. Run terraform apply again to create the CloudFront distribution
