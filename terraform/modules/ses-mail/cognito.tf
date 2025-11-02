# Cognito User Pool for Token Management Web UI
# Provides secure authentication for manual refresh token renewal

# Build callback and logout URLs from all configured domains
locals {
  # Callback URLs: localhost for dev + mta-sts subdomain for each configured domain
  cognito_callback_urls = concat(
    ["https://localhost:3000/callback"],
    [for domain in var.domain : "https://mta-sts.${domain}/token-management/callback"]
  )

  # Logout URLs: localhost for dev + mta-sts subdomain for each configured domain
  cognito_logout_urls = concat(
    ["https://localhost:3000"],
    [for domain in var.domain : "https://mta-sts.${domain}/token-management"]
  )
}

# Cognito User Pool
resource "aws_cognito_user_pool" "main" {
  name = "ses-mail-user-pool-${var.environment}"

  # Email as username (required)
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # User attributes
  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = false

    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }

  schema {
    name                = "given_name"
    attribute_data_type = "String"
    required            = false
    mutable             = true

    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }

  schema {
    name                = "family_name"
    attribute_data_type = "String"
    required            = false
    mutable             = true

    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }

  # Password policy
  password_policy {
    minimum_length                   = 8
    require_uppercase                = true
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  # MFA configuration (optional)
  mfa_configuration = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  # Admin-only user creation
  admin_create_user_config {
    allow_admin_create_user_only = true

    invite_message_template {
      email_subject = "Your SES Mail token management account"
      email_message = "Welcome to SES Mail token management. Your username is {username} and temporary password is {####}. Please sign in at https://mta-sts.${var.domain[0]}/token-management to set a new password."
      sms_message   = "Your SES Mail username is {username} and temporary password is {####}"
    }
  }

  # Email configuration
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  # Account recovery
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # User pool deletion protection
  deletion_protection = "ACTIVE"

  tags = {
    Name        = "ses-mail-user-pool-${var.environment}"
    Project     = "ses-mail"
    ManagedBy   = "terraform"
    Environment = var.environment
    Application = "ses-mail-${var.environment}"
  }
}

# Cognito User Pool Client (for web UI)
resource "aws_cognito_user_pool_client" "main" {
  name         = "ses-mail-web-ui-${var.environment}"
  user_pool_id = aws_cognito_user_pool.main.id

  # OAuth configuration
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code", "implicit"]
  allowed_oauth_scopes                 = ["email", "openid", "profile"]

  # Callback URLs (multiple domains supported)
  callback_urls = local.cognito_callback_urls
  logout_urls   = local.cognito_logout_urls

  # Supported identity providers
  supported_identity_providers = ["COGNITO"]

  # Token validity
  id_token_validity      = 60 # 60 minutes
  access_token_validity  = 60 # 60 minutes
  refresh_token_validity = 30 # 30 days

  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }

  # Prevent client secret (for SPA)
  generate_secret = false

  # Enable token revocation
  enable_token_revocation = true

  # Prevent user existence errors
  prevent_user_existence_errors = "ENABLED"

  # Read attributes
  read_attributes = [
    "email",
    "email_verified",
    "given_name",
    "family_name",
  ]

  # Note: write_attributes is not specified for standard attributes
  # Standard attributes (given_name, family_name) are automatically writable if mutable
}

# Cognito User Pool Domain (AWS-managed domain)
resource "aws_cognito_user_pool_domain" "main" {
  domain       = "ses-mail-${var.environment}"
  user_pool_id = aws_cognito_user_pool.main.id
}
