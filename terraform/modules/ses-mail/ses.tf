# SES domain identity for each domain
resource "aws_ses_domain_identity" "main" {
  for_each = toset(var.domain)
  domain   = each.value
}

# Enable DKIM signing with AWS managed keys (Easy DKIM)
resource "aws_ses_domain_dkim" "main" {
  for_each = aws_ses_domain_identity.main
  domain   = each.value.domain
}

# Custom MAIL FROM domain for better email deliverability
resource "aws_ses_domain_mail_from" "main" {
  for_each               = aws_ses_domain_identity.main
  domain                 = each.value.domain
  mail_from_domain       = "${var.mail_from_subdomain}.${each.value.domain}"
  behavior_on_mx_failure = "RejectMessage"
}

# SES receipt rule set
resource "aws_ses_receipt_rule_set" "main" {
  rule_set_name = "ses-mail-receipt-rules-${var.environment}"
}

# Set the receipt rule set as active (only if not joining another deployment)
resource "aws_ses_active_receipt_rule_set" "main" {
  count         = var.join_existing_deployment != null ? 0 : 1
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
}

# SES receipt rule for incoming emails (in own ruleset)
resource "aws_ses_receipt_rule" "main" {
  name          = "ses-mail-receive-emails-${var.environment}"
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
  recipients    = var.domain
  enabled       = true
  scan_enabled  = true # Enable spam and virus scanning

  # Store email in S3 and trigger SNS notification
  s3_action {
    bucket_name       = aws_s3_bucket.email_storage.id
    object_key_prefix = "emails/"
    topic_arn         = aws_sns_topic.email_processing.arn
    position          = 1
  }

  depends_on = [
    aws_s3_bucket_policy.email_storage,
    aws_sns_topic_policy.email_processing
  ]
}

# SES receipt rule in target environment's ruleset (only when joining another deployment)
resource "aws_ses_receipt_rule" "shared" {
  count = var.join_existing_deployment != null ? 1 : 0

  name          = "ses-mail-receive-emails-${var.environment}"
  rule_set_name = "ses-mail-receipt-rules-${var.join_existing_deployment}"
  recipients    = var.domain
  enabled       = true
  scan_enabled  = true # Enable spam and virus scanning

  # Store email in S3 and trigger SNS notification
  s3_action {
    bucket_name       = aws_s3_bucket.email_storage.id
    object_key_prefix = "emails/"
    topic_arn         = aws_sns_topic.email_processing.arn
    position          = 1
  }

  depends_on = [
    aws_s3_bucket_policy.email_storage,
    aws_sns_topic_policy.email_processing
  ]
}
