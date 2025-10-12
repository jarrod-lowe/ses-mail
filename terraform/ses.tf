# SES domain identity
resource "aws_ses_domain_identity" "main" {
  domain = var.domain
}

# Enable DKIM signing with AWS managed keys (Easy DKIM)
resource "aws_ses_domain_dkim" "main" {
  domain = aws_ses_domain_identity.main.domain
}

# SES receipt rule set
resource "aws_ses_receipt_rule_set" "main" {
  rule_set_name = "ses-mail-receipt-rules"
}

# Set the receipt rule set as active
resource "aws_ses_active_receipt_rule_set" "main" {
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
}

# SES receipt rule for incoming emails
resource "aws_ses_receipt_rule" "main" {
  name          = "receive-emails"
  rule_set_name = aws_ses_receipt_rule_set.main.rule_set_name
  recipients    = [var.domain]
  enabled       = true
  scan_enabled  = true # Enable spam and virus scanning

  # Store email in S3
  s3_action {
    bucket_name       = aws_s3_bucket.email_storage.id
    object_key_prefix = "emails/"
    position          = 1
  }

  # Trigger Lambda function
  lambda_action {
    function_arn    = aws_lambda_function.email_processor.arn
    invocation_type = "Event"
    position        = 2
  }

  depends_on = [
    aws_s3_bucket_policy.email_storage,
    aws_lambda_permission.allow_ses
  ]
}

# Permission for SES to invoke Lambda
resource "aws_lambda_permission" "allow_ses" {
  statement_id  = "AllowExecutionFromSES"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.email_processor.function_name
  principal     = "ses.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
}
