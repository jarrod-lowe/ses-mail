#!/bin/bash
set -e

# Script to help set up .env file from Terraform outputs

echo "Setting up web-ui environment variables..."
echo ""

# Check if we're in the web-ui directory
if [ ! -f "package.json" ]; then
    echo "Error: Please run this script from the web-ui directory"
    exit 1
fi

# Get terraform outputs
echo "Fetching Terraform outputs..."
cd ..
OUTPUTS=$(AWS_PROFILE=ses-mail make outputs ENV=test 2>/dev/null)

if [ -z "$OUTPUTS" ]; then
    echo "Error: Could not fetch Terraform outputs"
    echo "Make sure infrastructure is deployed: AWS_PROFILE=ses-mail make apply ENV=test"
    exit 1
fi

# Extract values from outputs
REGION=$(echo "$OUTPUTS" | grep 'aws_region' | awk '{print $3}' | tr -d '"')
USER_POOL_ID=$(echo "$OUTPUTS" | grep 'cognito_user_pool_id' | awk '{print $3}' | tr -d '"')
CLIENT_ID=$(echo "$OUTPUTS" | grep 'cognito_user_pool_client_id' | awk '{print $3}' | tr -d '"')
COGNITO_DOMAIN=$(echo "$OUTPUTS" | grep 'cognito_domain' | awk '{print $3}' | tr -d '"')
API_URL=$(echo "$OUTPUTS" | grep 'token_api_url' | awk '{print $3}' | tr -d '"')

cd web-ui

# Create .env file
cat > .env <<EOF
# AWS Cognito Configuration
VITE_AWS_REGION=${REGION}
VITE_COGNITO_USER_POOL_ID=${USER_POOL_ID}
VITE_COGNITO_USER_POOL_CLIENT_ID=${CLIENT_ID}
VITE_COGNITO_DOMAIN=${COGNITO_DOMAIN}

# API Gateway Configuration
VITE_API_ENDPOINT=${API_URL}

# OAuth Configuration (for local development)
VITE_OAUTH_REDIRECT_SIGN_IN=http://localhost:3000/callback
VITE_OAUTH_REDIRECT_SIGN_OUT=http://localhost:3000
EOF

echo ""
echo "✓ Created .env file with the following configuration:"
echo ""
cat .env
echo ""
echo "✓ Setup complete! You can now run: npm run dev"
