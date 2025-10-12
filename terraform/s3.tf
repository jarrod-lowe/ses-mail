# S3 bucket for storing incoming emails
resource "aws_s3_bucket" "email_storage" {
  bucket = var.email_bucket_name
}

# Enable versioning for the email storage bucket
resource "aws_s3_bucket_versioning" "email_storage" {
  bucket = aws_s3_bucket.email_storage.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Enable server-side encryption with AWS managed keys
resource "aws_s3_bucket_server_side_encryption_configuration" "email_storage" {
  bucket = aws_s3_bucket.email_storage.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Block public access to the email storage bucket
resource "aws_s3_bucket_public_access_block" "email_storage" {
  bucket = aws_s3_bucket.email_storage.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle rule to expire old emails
resource "aws_s3_bucket_lifecycle_configuration" "email_storage" {
  bucket = aws_s3_bucket.email_storage.id

  rule {
    id     = "expire-old-emails"
    status = "Enabled"

    filter {}

    expiration {
      days = var.email_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# IAM policy document for S3 bucket to allow SES writes
data "aws_iam_policy_document" "s3_bucket_policy" {
  statement {
    sid    = "AllowSESPuts"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ses.amazonaws.com"]
    }

    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.email_storage.arn}/*"]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "StringLike"
      variable = "AWS:SourceArn"
      values   = ["arn:aws:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:receipt-rule-set/*"]
    }
  }
}

# S3 bucket policy to allow SES to write emails
resource "aws_s3_bucket_policy" "email_storage" {
  bucket = aws_s3_bucket.email_storage.id
  policy = data.aws_iam_policy_document.s3_bucket_policy.json
}

# Get current AWS account ID
data "aws_caller_identity" "current" {}
