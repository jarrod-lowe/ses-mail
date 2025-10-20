# KMS key for encrypting SMTP credentials
resource "aws_kms_key" "smtp_credentials" {
  description             = "KMS key for encrypting SMTP credentials (${var.environment})"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name        = "ses-mail-smtp-credentials-${var.environment}"
    Environment = var.environment
    ManagedBy   = "Terraform"
    Purpose     = "SMTP credential encryption"
  }
}

# KMS key alias for easy reference
resource "aws_kms_alias" "smtp_credentials" {
  name          = "alias/ses-mail-smtp-credentials-${var.environment}"
  target_key_id = aws_kms_key.smtp_credentials.key_id
}
