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

# SES receipt rule set
resource "aws_ses_receipt_rule_set" "main" {
  rule_set_name = "ses-mail-receipt-rules-${var.environment}"
}

# Set the receipt rule set as active
resource "aws_ses_active_receipt_rule_set" "main" {
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
}

# SES receipt rule for incoming emails
resource "aws_ses_receipt_rule" "main" {
  name          = "receive-emails-${var.environment}"
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
  recipients    = var.domain
  enabled       = true
  scan_enabled  = true # Enable spam and virus scanning

  # Store email in S3
  s3_action {
    bucket_name       = aws_s3_bucket.email_storage.id
    object_key_prefix = "emails/"
    position          = 1
  }

  # Validate email synchronously (RequestResponse)
  lambda_action {
    function_arn    = aws_lambda_function.email_validator.arn
    invocation_type = "RequestResponse"
    position        = 2
  }

  # Trigger email processor Lambda function
  lambda_action {
    function_arn    = aws_lambda_function.email_processor.arn
    invocation_type = "Event"
    position        = 3
  }

  depends_on = [
    aws_s3_bucket_policy.email_storage,
    aws_lambda_permission.allow_ses,
    aws_lambda_permission.allow_ses_validator
  ]
}

# Permission for SES to invoke email processor Lambda
resource "aws_lambda_permission" "allow_ses" {
  statement_id  = "AllowExecutionFromSES"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.email_processor.function_name
  principal     = "ses.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
}

# Permission for SES to invoke email validator Lambda
resource "aws_lambda_permission" "allow_ses_validator" {
  statement_id  = "AllowExecutionFromSES"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.email_validator.function_name
  principal     = "ses.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
}
