# SESv2 email identity with DKIM and Configuration Set
# Using v2 API to support automatic Configuration Set association for outbound metrics
resource "aws_sesv2_email_identity" "main" {
  for_each = toset(var.domain)

  email_identity         = each.value
  configuration_set_name = aws_ses_configuration_set.outbound.name

  # Enable Easy DKIM (AWS managed keys)
  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }

  tags = {
    Name        = "ses-mail-identity-${each.value}-${var.environment}"
    Environment = var.environment
    Service     = "ses-mail"
    Purpose     = "Domain identity with DKIM and Configuration Set"
  }
}

# Custom MAIL FROM domain for better email deliverability
resource "aws_sesv2_email_identity_mail_from_attributes" "main" {
  for_each = toset(var.domain)

  email_identity         = aws_sesv2_email_identity.main[each.value].email_identity
  mail_from_domain       = "${var.mail_from_subdomain}.${each.value}"
  behavior_on_mx_failure = "REJECT_MESSAGE"
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

# SES Configuration Set for outbound email tracking and metrics
resource "aws_ses_configuration_set" "outbound" {
  name = "ses-mail-outbound-${var.environment}"

  # Enable reputation metrics tracking (bounce/complaint rates)
  reputation_metrics_enabled = true

  # Enable email sending through this configuration set
  sending_enabled = true
}

# Event destination for send events
resource "aws_ses_event_destination" "send" {
  name                   = "send-events"
  configuration_set_name = aws_ses_configuration_set.outbound.name
  enabled                = true
  matching_types         = ["send"]

  sns_destination {
    topic_arn = aws_sns_topic.outbound_send.arn
  }
}

# Event destination for delivery events
resource "aws_ses_event_destination" "delivery" {
  name                   = "delivery-events"
  configuration_set_name = aws_ses_configuration_set.outbound.name
  enabled                = true
  matching_types         = ["delivery"]

  sns_destination {
    topic_arn = aws_sns_topic.outbound_delivery.arn
  }
}

# Event destination for bounce events (includes both bounce and reject)
resource "aws_ses_event_destination" "bounce" {
  name                   = "bounce-events"
  configuration_set_name = aws_ses_configuration_set.outbound.name
  enabled                = true
  matching_types         = ["bounce", "reject"]

  sns_destination {
    topic_arn = aws_sns_topic.outbound_bounce.arn
  }
}

# Event destination for complaint events
resource "aws_ses_event_destination" "complaint" {
  name                   = "complaint-events"
  configuration_set_name = aws_ses_configuration_set.outbound.name
  enabled                = true
  matching_types         = ["complaint"]

  sns_destination {
    topic_arn = aws_sns_topic.outbound_complaint.arn
  }
}
