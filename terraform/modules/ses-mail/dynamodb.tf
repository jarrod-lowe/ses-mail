# DynamoDB table for email routing rules using single-table design pattern
# This table stores routing rules with hierarchical address matching
# and can be extended for other use cases in the future

resource "aws_dynamodb_table" "email_routing" {
  name         = "ses-mail-email-routing-${var.environment}"
  billing_mode = "PAY_PER_REQUEST" # No standing costs, pay per request

  # Single-table design with generic keys
  hash_key  = "PK" # Partition key (generic for extensibility)
  range_key = "SK" # Sort key (generic for extensibility)

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  # Enable point-in-time recovery for data protection
  point_in_time_recovery {
    enabled = true
  }

  # Enable server-side encryption
  server_side_encryption {
    enabled = true
  }

  # Enable TTL for automatic cleanup of temporary records (e.g., canary completion records)
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  # Enable DynamoDB Streams to trigger Lambda functions for SMTP credential management
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES" # Capture both old and new item images for INSERT and MODIFY events

  tags = {
    Name        = "ses-mail-email-routing-${var.environment}"
    Environment = var.environment
    ManagedBy   = "Terraform"
    Purpose     = "Email routing rules and configuration"
  }
}

# Data structure documentation:
#
# Routing Rules Entity:
#   PK: "ROUTE#<email-pattern>"
#     Examples:
#       - "ROUTE#support@example.com" (exact match)
#       - "ROUTE#user@example.com" (normalized, without +tag)
#       - "ROUTE#*@example.com" (domain wildcard)
#       - "ROUTE#*" (global wildcard/default)
#
#   SK: "RULE#v1" (allows versioning)
#
#   Attributes (denormalized):
#     - entity_type: "ROUTE" (for filtering)
#     - recipient: "support@example.com" (denormalized from PK for querying)
#     - action: "forward-to-gmail" | "bounce"
#     - target: "user@gmail.com" (Gmail address) | "" (for bounce)
#     - enabled: true | false
#     - created_at: "2025-01-18T10:00:00Z" (ISO timestamp)
#     - updated_at: "2025-01-18T10:00:00Z" (ISO timestamp)
#     - description: "Human-readable description"
#
# Example record:
# {
#   "PK": "ROUTE#support@example.com",
#   "SK": "RULE#v1",
#   "entity_type": "ROUTE",
#   "recipient": "support@example.com",
#   "action": "forward-to-gmail",
#   "target": "support@gmail.com",
#   "enabled": true,
#   "created_at": "2025-01-18T10:00:00Z",
#   "updated_at": "2025-01-18T10:00:00Z",
#   "description": "Forward support emails to Gmail"
# }
#
# Hierarchical lookup strategy (implemented in router lambda):
# 1. Check exact match: ROUTE#user+tag@example.com
# 2. Check normalized: ROUTE#user@example.com (remove +tag)
# 3. Check domain wildcard: ROUTE#*@example.com
# 4. Check global wildcard: ROUTE#*
# First match wins.
#
# Future extensibility examples:
# - CONFIG#setting-name (application configuration)
# - METRICS#date#counter (usage metrics)
# - TEMPLATE#template-name (email templates)
