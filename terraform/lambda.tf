# Archive the Lambda function code
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda/email_processor.py"
  output_path = "${path.module}/lambda/email_processor.zip"
}

# Lambda function for processing emails
resource "aws_lambda_function" "email_processor" {
  filename         = data.archive_file.lambda_zip.output_path
  function_name    = "ses-mail-email-processor"
  role            = aws_iam_role.lambda_execution.arn
  handler         = "email_processor.lambda_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime         = "python3.12"
  timeout         = 60
  memory_size     = 256

  environment {
    variables = {
      GMAIL_TOKEN_PARAMETER = aws_ssm_parameter.gmail_token.name
      EMAIL_BUCKET         = aws_s3_bucket.email_storage.id
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
