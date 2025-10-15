# Backup terraform.tfvars to the state bucket
# This ensures configuration is preserved alongside state

resource "aws_s3_object" "tfvars_backup" {
  bucket  = "terraform-state-${data.aws_caller_identity.current.account_id}"
  key     = "ses-mail/${var.environment}/terraform.tfvars"
  content = file("${path.root}/terraform.tfvars")
  etag    = filemd5("${path.root}/terraform.tfvars")

  tags = {
    Purpose = "Configuration backup"
  }
}
