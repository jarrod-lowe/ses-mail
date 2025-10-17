# AWS Resource Group for organizing all ses-mail resources by environment
# This creates a single view of all resources for a particular environment
# using the default tags applied at the provider level

resource "aws_resourcegroups_group" "ses_mail" {
  name        = "ses-mail-${var.environment}"
  description = "All SES Mail resources for the ${var.environment} environment"

  resource_query {
    query = jsonencode({
      ResourceTypeFilters = [
        "AWS::AllSupported"
      ]
      TagFilters = [
        {
          Key    = "Project"
          Values = ["ses-mail"]
        },
        {
          Key    = "Environment"
          Values = [var.environment]
        }
      ]
    })
  }

  tags = {
    Name    = "ses-mail-${var.environment}"
    Purpose = "Resource group for all ses-mail resources in ${var.environment}"
  }
}
