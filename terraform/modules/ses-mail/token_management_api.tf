# ==============================================================================
# Token Management API Gateway (HTTP API v2) with Cognito Authorization
# ==============================================================================
# This file implements task 6.1: API Gateway with Cognito authorizer for token
# management. Provides REST API endpoints for managing Gmail OAuth refresh tokens
# with per-user authentication via Cognito User Pool.

# ------------------------------------------------------------------------------
# HTTP API Gateway
# ------------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "token_management" {
  name          = "ses-mail-token-api-${var.environment}"
  protocol_type = "HTTP"
  description   = "Token management API for Gmail OAuth refresh tokens (${var.environment})"

  cors_configuration {
    allow_origins = [
      "https://localhost:3000",          # Development
      "https://mta-sts.${var.domain[0]}" # Production CloudFront
    ]
    allow_methods  = ["GET", "POST", "OPTIONS"]
    allow_headers  = ["Content-Type", "Authorization", "X-Amz-Date", "X-Api-Key", "X-Amz-Security-Token"]
    expose_headers = ["X-Request-Id"]
    max_age        = 300
  }

  tags = {
    Name        = "ses-mail-token-api-${var.environment}"
    Environment = var.environment
    Purpose     = "token-management"
  }
}

# ------------------------------------------------------------------------------
# Cognito JWT Authorizer
# ------------------------------------------------------------------------------

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.token_management.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "cognito-authorizer-${var.environment}"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.main.id]
    issuer   = "https://${aws_cognito_user_pool.main.endpoint}"
  }
}

# ------------------------------------------------------------------------------
# API Gateway Stage with Logging and Throttling
# ------------------------------------------------------------------------------

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.token_management.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway_logs.arn
    format = jsonencode({
      requestId       = "$context.requestId"
      ip              = "$context.identity.sourceIp"
      requestTime     = "$context.requestTime"
      httpMethod      = "$context.httpMethod"
      routeKey        = "$context.routeKey"
      status          = "$context.status"
      protocol        = "$context.protocol"
      responseLength  = "$context.responseLength"
      errorMessage    = "$context.error.message"
      authorizerError = "$context.authorizer.error"
      userPoolId      = "$context.authorizer.claims.sub"
    })
  }

  default_route_settings {
    throttling_burst_limit = 10 # Conservative: max 10 concurrent requests
    throttling_rate_limit  = 10 # Conservative: 10 requests per second
  }

  tags = {
    Name        = "ses-mail-token-api-default-stage-${var.environment}"
    Environment = var.environment
  }
}

# CloudWatch Log Group for API Gateway Access Logs
resource "aws_cloudwatch_log_group" "api_gateway_logs" {
  name              = "/aws/apigateway/ses-mail-token-api-${var.environment}"
  retention_in_days = 30

  tags = {
    Name        = "ses-mail-token-api-logs-${var.environment}"
    Environment = var.environment
  }
}

# ==============================================================================
# Lambda Functions for API Endpoints
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. Token Status API Lambda
# ------------------------------------------------------------------------------

resource "aws_lambda_function" "token_status_api" {
  filename         = data.archive_file.token_status_api_package.output_path
  function_name    = "ses-mail-token-status-api-${var.environment}"
  role             = aws_iam_role.lambda_token_api_execution.arn
  handler          = "token_status_api.lambda_handler"
  source_code_hash = data.archive_file.token_status_api_package.output_base64sha256
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      ENVIRONMENT  = var.environment
      TABLE_NAME   = aws_dynamodb_table.email_routing.name
      USER_POOL_ID = aws_cognito_user_pool.main.id
      LOG_LEVEL    = "INFO"
    }
  }

  tracing_config {
    mode = "Active" # X-Ray tracing enabled
  }

  tags = {
    Name        = "ses-mail-token-status-api-${var.environment}"
    Environment = var.environment
    Purpose     = "token-status"
  }
}

# Package Lambda function from lambda/package directory
data "archive_file" "token_status_api_package" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/package"
  output_path = "${path.module}/lambda/token_status_api_${var.environment}.zip"
  excludes    = ["__pycache__", "*.pyc", ".DS_Store", "token_renew_api.py", "oauth_callback_api.py", "user_info_api.py", "health_check_api.py", "email_processor.py", "router_enrichment.py", "gmail_forwarder.py", "bouncer.py", "smtp_credential_manager.py", "tag_sync_starter.py", "email_validator.py"]
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "token_status_api_logs" {
  name              = "/aws/lambda/${aws_lambda_function.token_status_api.function_name}"
  retention_in_days = 30

  tags = {
    Name        = "token-status-api-logs-${var.environment}"
    Environment = var.environment
  }
}

# API Gateway permission to invoke Lambda
resource "aws_lambda_permission" "token_status_api_apigw" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.token_status_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.token_management.execution_arn}/*/*"
}

# ------------------------------------------------------------------------------
# 2. Token Renew API Lambda
# ------------------------------------------------------------------------------

resource "aws_lambda_function" "token_renew_api" {
  filename         = data.archive_file.token_renew_api_package.output_path
  function_name    = "ses-mail-token-renew-api-${var.environment}"
  role             = aws_iam_role.lambda_token_api_execution.arn
  handler          = "token_renew_api.lambda_handler"
  source_code_hash = data.archive_file.token_renew_api_package.output_base64sha256
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      ENVIRONMENT  = var.environment
      TABLE_NAME   = aws_dynamodb_table.email_routing.name
      USER_POOL_ID = aws_cognito_user_pool.main.id
      LOG_LEVEL    = "INFO"
    }
  }

  tracing_config {
    mode = "Active"
  }

  tags = {
    Name        = "ses-mail-token-renew-api-${var.environment}"
    Environment = var.environment
    Purpose     = "token-renewal"
  }
}

data "archive_file" "token_renew_api_package" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/package"
  output_path = "${path.module}/lambda/token_renew_api_${var.environment}.zip"
  excludes    = ["__pycache__", "*.pyc", ".DS_Store", "token_status_api.py", "oauth_callback_api.py", "user_info_api.py", "health_check_api.py", "email_processor.py", "router_enrichment.py", "gmail_forwarder.py", "bouncer.py", "smtp_credential_manager.py", "tag_sync_starter.py", "email_validator.py"]
}

resource "aws_cloudwatch_log_group" "token_renew_api_logs" {
  name              = "/aws/lambda/${aws_lambda_function.token_renew_api.function_name}"
  retention_in_days = 30

  tags = {
    Name        = "token-renew-api-logs-${var.environment}"
    Environment = var.environment
  }
}

resource "aws_lambda_permission" "token_renew_api_apigw" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.token_renew_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.token_management.execution_arn}/*/*"
}

# ------------------------------------------------------------------------------
# 3. OAuth Callback API Lambda
# ------------------------------------------------------------------------------

resource "aws_lambda_function" "oauth_callback_api" {
  filename         = data.archive_file.oauth_callback_api_package.output_path
  function_name    = "ses-mail-oauth-callback-api-${var.environment}"
  role             = aws_iam_role.lambda_token_api_execution.arn
  handler          = "oauth_callback_api.lambda_handler"
  source_code_hash = data.archive_file.oauth_callback_api_package.output_base64sha256
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      ENVIRONMENT  = var.environment
      TABLE_NAME   = aws_dynamodb_table.email_routing.name
      USER_POOL_ID = aws_cognito_user_pool.main.id
      LOG_LEVEL    = "INFO"
    }
  }

  tracing_config {
    mode = "Active"
  }

  tags = {
    Name        = "ses-mail-oauth-callback-api-${var.environment}"
    Environment = var.environment
    Purpose     = "oauth-callback"
  }
}

data "archive_file" "oauth_callback_api_package" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/package"
  output_path = "${path.module}/lambda/oauth_callback_api_${var.environment}.zip"
  excludes    = ["__pycache__", "*.pyc", ".DS_Store", "token_status_api.py", "token_renew_api.py", "user_info_api.py", "health_check_api.py", "email_processor.py", "router_enrichment.py", "gmail_forwarder.py", "bouncer.py", "smtp_credential_manager.py", "tag_sync_starter.py", "email_validator.py"]
}

resource "aws_cloudwatch_log_group" "oauth_callback_api_logs" {
  name              = "/aws/lambda/${aws_lambda_function.oauth_callback_api.function_name}"
  retention_in_days = 30

  tags = {
    Name        = "oauth-callback-api-logs-${var.environment}"
    Environment = var.environment
  }
}

resource "aws_lambda_permission" "oauth_callback_api_apigw" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.oauth_callback_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.token_management.execution_arn}/*/*"
}

# ------------------------------------------------------------------------------
# 4. User Info API Lambda
# ------------------------------------------------------------------------------

resource "aws_lambda_function" "user_info_api" {
  filename         = data.archive_file.user_info_api_package.output_path
  function_name    = "ses-mail-user-info-api-${var.environment}"
  role             = aws_iam_role.lambda_token_api_execution.arn
  handler          = "user_info_api.lambda_handler"
  source_code_hash = data.archive_file.user_info_api_package.output_base64sha256
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      ENVIRONMENT  = var.environment
      TABLE_NAME   = aws_dynamodb_table.email_routing.name
      USER_POOL_ID = aws_cognito_user_pool.main.id
      LOG_LEVEL    = "INFO"
    }
  }

  tracing_config {
    mode = "Active"
  }

  tags = {
    Name        = "ses-mail-user-info-api-${var.environment}"
    Environment = var.environment
    Purpose     = "user-info"
  }
}

data "archive_file" "user_info_api_package" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/package"
  output_path = "${path.module}/lambda/user_info_api_${var.environment}.zip"
  excludes    = ["__pycache__", "*.pyc", ".DS_Store", "token_status_api.py", "token_renew_api.py", "oauth_callback_api.py", "health_check_api.py", "email_processor.py", "router_enrichment.py", "gmail_forwarder.py", "bouncer.py", "smtp_credential_manager.py", "tag_sync_starter.py", "email_validator.py"]
}

resource "aws_cloudwatch_log_group" "user_info_api_logs" {
  name              = "/aws/lambda/${aws_lambda_function.user_info_api.function_name}"
  retention_in_days = 30

  tags = {
    Name        = "user-info-api-logs-${var.environment}"
    Environment = var.environment
  }
}

resource "aws_lambda_permission" "user_info_api_apigw" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.user_info_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.token_management.execution_arn}/*/*"
}

# ------------------------------------------------------------------------------
# 5. Health Check API Lambda
# ------------------------------------------------------------------------------

resource "aws_lambda_function" "health_check_api" {
  filename         = data.archive_file.health_check_api_package.output_path
  function_name    = "ses-mail-health-check-api-${var.environment}"
  role             = aws_iam_role.lambda_token_api_execution.arn
  handler          = "health_check_api.lambda_handler"
  source_code_hash = data.archive_file.health_check_api_package.output_base64sha256
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      ENVIRONMENT = var.environment
      LOG_LEVEL   = "INFO"
    }
  }

  tracing_config {
    mode = "Active"
  }

  tags = {
    Name        = "ses-mail-health-check-api-${var.environment}"
    Environment = var.environment
    Purpose     = "health-check"
  }
}

data "archive_file" "health_check_api_package" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/package"
  output_path = "${path.module}/lambda/health_check_api_${var.environment}.zip"
  excludes    = ["__pycache__", "*.pyc", ".DS_Store", "token_status_api.py", "token_renew_api.py", "oauth_callback_api.py", "user_info_api.py", "email_processor.py", "router_enrichment.py", "gmail_forwarder.py", "bouncer.py", "smtp_credential_manager.py", "tag_sync_starter.py", "email_validator.py"]
}

resource "aws_cloudwatch_log_group" "health_check_api_logs" {
  name              = "/aws/lambda/${aws_lambda_function.health_check_api.function_name}"
  retention_in_days = 30

  tags = {
    Name        = "health-check-api-logs-${var.environment}"
    Environment = var.environment
  }
}

resource "aws_lambda_permission" "health_check_api_apigw" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.health_check_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.token_management.execution_arn}/*/*"
}

# ==============================================================================
# IAM Role for Token Management API Lambda Functions
# ==============================================================================

resource "aws_iam_role" "lambda_token_api_execution" {
  name               = "ses-mail-lambda-token-api-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name        = "ses-mail-lambda-token-api-${var.environment}"
    Environment = var.environment
  }
}

# Attach basic Lambda execution policy (CloudWatch Logs)
resource "aws_iam_role_policy_attachment" "lambda_token_api_basic_execution" {
  role       = aws_iam_role.lambda_token_api_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Attach X-Ray write access for distributed tracing
resource "aws_iam_role_policy_attachment" "lambda_token_api_xray_access" {
  role       = aws_iam_role.lambda_token_api_execution.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# Custom policy for DynamoDB access to routing table
resource "aws_iam_role_policy" "lambda_token_api_dynamodb_access" {
  name   = "DynamoDBAccess"
  role   = aws_iam_role.lambda_token_api_execution.id
  policy = data.aws_iam_policy_document.lambda_token_api_dynamodb_access.json
}

data "aws_iam_policy_document" "lambda_token_api_dynamodb_access" {
  statement {
    sid    = "DynamoDBReadWriteUserRecords"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem"
    ]
    resources = [
      aws_dynamodb_table.email_routing.arn,
      "${aws_dynamodb_table.email_routing.arn}/*"
    ]
  }
}

# Custom policy for Cognito user context operations
resource "aws_iam_role_policy" "lambda_token_api_cognito_access" {
  name   = "CognitoAccess"
  role   = aws_iam_role.lambda_token_api_execution.id
  policy = data.aws_iam_policy_document.lambda_token_api_cognito_access.json
}

data "aws_iam_policy_document" "lambda_token_api_cognito_access" {
  statement {
    sid    = "CognitoUserInfo"
    effect = "Allow"
    actions = [
      "cognito-idp:GetUser",
      "cognito-idp:ListUsers",
      "cognito-idp:AdminGetUser"
    ]
    resources = [
      aws_cognito_user_pool.main.arn
    ]
  }
}

# Custom policy for SSM Parameter Store access (Google OAuth credentials)
resource "aws_iam_role_policy" "lambda_token_api_ssm_access" {
  name   = "SSMParameterAccess"
  role   = aws_iam_role.lambda_token_api_execution.id
  policy = data.aws_iam_policy_document.lambda_token_api_ssm_access.json
}

data "aws_iam_policy_document" "lambda_token_api_ssm_access" {
  statement {
    sid    = "SSMGoogleOAuthParameters"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters"
    ]
    resources = [
      "arn:aws:ssm:${var.aws_region}:*:parameter/ses-mail/${var.environment}/google-oauth-*"
    ]
  }
}

# ==============================================================================
# API Gateway Routes and Integrations
# ==============================================================================

# ------------------------------------------------------------------------------
# Route 1: GET /api/token/status (Authenticated)
# ------------------------------------------------------------------------------

resource "aws_apigatewayv2_route" "token_status" {
  api_id             = aws_apigatewayv2_api.token_management.id
  route_key          = "GET /api/token/status"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
  target             = "integrations/${aws_apigatewayv2_integration.token_status.id}"
}

resource "aws_apigatewayv2_integration" "token_status" {
  api_id                 = aws_apigatewayv2_api.token_management.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.token_status_api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# ------------------------------------------------------------------------------
# Route 2: POST /api/token/renew (Authenticated)
# ------------------------------------------------------------------------------

resource "aws_apigatewayv2_route" "token_renew" {
  api_id             = aws_apigatewayv2_api.token_management.id
  route_key          = "POST /api/token/renew"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
  target             = "integrations/${aws_apigatewayv2_integration.token_renew.id}"
}

resource "aws_apigatewayv2_integration" "token_renew" {
  api_id                 = aws_apigatewayv2_api.token_management.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.token_renew_api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# ------------------------------------------------------------------------------
# Route 3: GET /api/token/callback (Authenticated)
# ------------------------------------------------------------------------------

resource "aws_apigatewayv2_route" "oauth_callback" {
  api_id             = aws_apigatewayv2_api.token_management.id
  route_key          = "GET /api/token/callback"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
  target             = "integrations/${aws_apigatewayv2_integration.oauth_callback.id}"
}

resource "aws_apigatewayv2_integration" "oauth_callback" {
  api_id                 = aws_apigatewayv2_api.token_management.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.oauth_callback_api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# ------------------------------------------------------------------------------
# Route 4: GET /api/users/me (Authenticated)
# ------------------------------------------------------------------------------

resource "aws_apigatewayv2_route" "user_info" {
  api_id             = aws_apigatewayv2_api.token_management.id
  route_key          = "GET /api/users/me"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
  target             = "integrations/${aws_apigatewayv2_integration.user_info.id}"
}

resource "aws_apigatewayv2_integration" "user_info" {
  api_id                 = aws_apigatewayv2_api.token_management.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.user_info_api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# ------------------------------------------------------------------------------
# Route 5: GET /api/health (Public - No Authentication)
# ------------------------------------------------------------------------------

resource "aws_apigatewayv2_route" "health_check" {
  api_id    = aws_apigatewayv2_api.token_management.id
  route_key = "GET /api/health"
  target    = "integrations/${aws_apigatewayv2_integration.health_check.id}"
  # Note: No authorization_type or authorizer_id - this is a public endpoint
}

resource "aws_apigatewayv2_integration" "health_check" {
  api_id                 = aws_apigatewayv2_api.token_management.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.health_check_api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}
