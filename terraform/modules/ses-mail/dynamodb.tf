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
#
# Canary Tracking Entity:
#   PK: "CANARY#<canary-id>"
#   SK: "TRACKING#v1"
#   Attributes:
#     - entity_type: "CANARY_TRACKING"
#     - canary_id: "canary-2026-01-08T12:00:00Z"
#     - status: "pending" | "completed" | "failed"
#     - sent_at: "2026-01-08T12:00:00Z"
#     - completed_at: "2026-01-08T12:01:30Z" (set by gmail_forwarder)
#     - ses_message_id: "..." (SES message ID)
#     - gmail_message_id: "..." (Gmail message ID, set by gmail_forwarder)
#     - ttl: 1736348400 (Unix timestamp for automatic deletion)

# Canary routing rule - creates a routing rule for canary test emails
# Only created if canary_target_email is set
resource "aws_dynamodb_table_item" "canary_routing_rule" {
  count = var.canary_target_email != null ? 1 : 0

  table_name = aws_dynamodb_table.email_routing.name
  hash_key   = aws_dynamodb_table.email_routing.hash_key
  range_key  = aws_dynamodb_table.email_routing.range_key

  item = jsonencode({
    PK          = { S = "ROUTE#ses-canary-${var.environment}@${var.domain[0]}" }
    SK          = { S = "RULE#v1" }
    entity_type = { S = "ROUTE" }
    recipient   = { S = "ses-canary-${var.environment}@${var.domain[0]}" }
    action      = { S = "forward-to-gmail" }
    target      = { S = var.canary_target_email }
    enabled     = { BOOL = true }
    metadata = {
      S = jsonencode({
        canary = true
        # No Gmail-specific config here - that goes in Lambda environment variables
      })
    }
    created_at  = { S = "2026-01-08T00:00:00Z" }
    updated_at  = { S = "2026-01-08T00:00:00Z" }
    description = { S = "Canary test routing rule (${var.environment} environment)" }
  })
}
