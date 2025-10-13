# MTA-STS Infrastructure
# Provides email security by enforcing TLS for incoming mail

# S3 bucket for MTA-STS policy file
resource "aws_s3_bucket" "mta_sts" {
  count  = var.mta_sts_mode != "none" ? 1 : 0
  bucket = "mta-sts-ses-mail-${var.environment}"
}

# Block public access (CloudFront will access via OAC)
resource "aws_s3_bucket_public_access_block" "mta_sts" {
  count  = var.mta_sts_mode != "none" ? 1 : 0
  bucket = aws_s3_bucket.mta_sts[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# MTA-STS policy file content
locals {
  mta_sts_policy = <<-EOT
    version: STSv1
    mode: ${var.mta_sts_mode}
    mx: inbound-smtp.${var.aws_region}.amazonaws.com
    max_age: 86400
  EOT

  # Policy ID is a hash of the content to change when policy changes
  mta_sts_policy_id = substr(sha256(local.mta_sts_policy), 0, 8)
}

# Upload policy file to S3
resource "aws_s3_object" "mta_sts_policy" {
  count  = var.mta_sts_mode != "none" ? 1 : 0
  bucket = aws_s3_bucket.mta_sts[0].id
  key    = ".well-known/mta-sts.txt"

  content      = local.mta_sts_policy
  content_type = "text/plain"
  etag         = md5(local.mta_sts_policy)
}

# CloudFront Origin Access Control
resource "aws_cloudfront_origin_access_control" "mta_sts" {
  count                             = var.mta_sts_mode != "none" ? 1 : 0
  name                              = "mta-sts-ses-mail-${var.environment}"
  description                       = "OAC for MTA-STS S3 bucket (${var.environment})"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# CloudFront distribution
resource "aws_cloudfront_distribution" "mta_sts" {
  count   = var.mta_sts_mode != "none" ? 1 : 0
  enabled = true
  comment = "MTA-STS policy for ${var.domain} (${var.environment})"

  aliases = ["mta-sts.${var.domain}"]

  origin {
    domain_name              = aws_s3_bucket.mta_sts[0].bucket_regional_domain_name
    origin_id                = "S3-${aws_s3_bucket.mta_sts[0].id}"
    origin_access_control_id = aws_cloudfront_origin_access_control.mta_sts[0].id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "S3-${aws_s3_bucket.mta_sts[0].id}"
    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    min_ttl     = 0
    default_ttl = 86400
    max_ttl     = 31536000
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate.mta_sts[0].arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  # Note: This will fail if the ACM certificate is not yet validated
  # User must add DNS validation records and wait for validation before this succeeds
}

# S3 bucket policy to allow CloudFront OAC access
data "aws_iam_policy_document" "mta_sts_bucket_policy" {
  count = var.mta_sts_mode != "none" ? 1 : 0

  statement {
    sid    = "AllowCloudFrontOAC"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.mta_sts[0].arn}/*"]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.mta_sts[0].arn]
    }
  }
}

resource "aws_s3_bucket_policy" "mta_sts" {
  count  = var.mta_sts_mode != "none" ? 1 : 0
  bucket = aws_s3_bucket.mta_sts[0].id
  policy = data.aws_iam_policy_document.mta_sts_bucket_policy[0].json
}
