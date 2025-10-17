# Archive the Lambda function code with dependencies
# Note: Dependencies must be installed in lambda/package/ directory first
# Run: pip install -r ../requirements.txt -t lambda/package/
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/package"
  output_path = "${path.module}/lambda/email_processor.zip"
  excludes    = ["__pycache__", "*.pyc", ".DS_Store"]
}

# Lambda function for processing emails
resource "aws_lambda_function" "email_processor" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "ses-mail-email-processor-${var.environment}"
  role             = aws_iam_role.lambda_execution.arn
  handler          = "email_processor.lambda_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      GMAIL_TOKEN_PARAMETER = aws_ssm_parameter.gmail_token.name
      EMAIL_BUCKET          = aws_s3_bucket.email_storage.id
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic_execution,
    aws_iam_role_policy.lambda_s3_access,
    aws_iam_role_policy.lambda_ssm_access
  ]
}

# CloudWatch Log Group for Lambda function
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${aws_lambda_function.email_processor.function_name}"
  retention_in_days = 30
}

# Archive the email validator Lambda function code (single file, no dependencies)
data "archive_file" "validator_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/email_validator.py"
  output_path = "${path.module}/lambda/email_validator.zip"
}

# Lambda function for validating emails (RequestResponse invocation)
resource "aws_lambda_function" "email_validator" {
  filename         = data.archive_file.validator_zip.output_path
  function_name    = "ses-mail-email-validator-${var.environment}"
  role             = aws_iam_role.lambda_validator_execution.arn
  handler          = "email_validator.lambda_handler"
  source_code_hash = data.archive_file.validator_zip.output_base64sha256
  runtime          = "python3.12"
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      BOUNCE_SENDER = "mailer-daemon@${var.domain[0]}"
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_validator_basic_execution,
    aws_iam_role_policy.lambda_validator_ses_access
  ]
}

# CloudWatch Log Group for validator Lambda function
resource "aws_cloudwatch_log_group" "lambda_validator_logs" {
  name              = "/aws/lambda/${aws_lambda_function.email_validator.function_name}"
  retention_in_days = 30
}
