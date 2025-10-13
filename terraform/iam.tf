# SSM Parameter for storing Gmail OAuth token
resource "aws_ssm_parameter" "gmail_token" {
  name        = "/ses-mail/gmail-token"
  description = "Gmail OAuth token for inserting emails via API"
  type        = "SecureString"
  value       = "PLACEHOLDER - Update this value after deployment"

  lifecycle {
    ignore_changes = [value]
  }
}

# IAM policy document for Lambda assume role
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# IAM role for Lambda function
resource "aws_iam_role" "lambda_execution" {
  name               = "ses-mail-lambda-execution-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# IAM policy document for Lambda S3 access
data "aws_iam_policy_document" "lambda_s3_access" {
  statement {
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:DeleteObject",
      "s3:ListBucket"
    ]
    resources = [
      aws_s3_bucket.email_storage.arn,
      "${aws_s3_bucket.email_storage.arn}/*"
    ]
  }
}

# IAM policy for Lambda to access S3
resource "aws_iam_role_policy" "lambda_s3_access" {
  name   = "lambda-s3-access"
  role   = aws_iam_role.lambda_execution.id
  policy = data.aws_iam_policy_document.lambda_s3_access.json
}

# IAM policy document for Lambda SSM access
data "aws_iam_policy_document" "lambda_ssm_access" {
  statement {
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:PutParameter"
    ]
    resources = [aws_ssm_parameter.gmail_token.arn]
  }
}

# IAM policy for Lambda to access SSM Parameter Store
resource "aws_iam_role_policy" "lambda_ssm_access" {
  name   = "lambda-ssm-access"
  role   = aws_iam_role.lambda_execution.id
  policy = data.aws_iam_policy_document.lambda_ssm_access.json
}

# Attach AWS managed policy for basic Lambda execution (CloudWatch Logs)
resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
