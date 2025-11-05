# SES Mail Token Management Web UI

React-based web interface for managing Google OAuth refresh tokens in the SES Mail system.

## Overview

This web application provides a secure, user-friendly interface for:
- Viewing Gmail OAuth token status
- Renewing expired refresh tokens
- Managing user authentication via AWS Cognito

## Prerequisites

- Node.js 18+ and npm
- AWS credentials configured with `AWS_PROFILE=ses-mail`
- Deployed infrastructure (Cognito User Pool and API Gateway)

## Getting Started

### 1. Install Dependencies

```bash
cd web-ui
npm install
```

### 2. Configure Environment Variables

First, get the infrastructure outputs from Terraform:

```bash
cd ..
AWS_PROFILE=ses-mail make outputs ENV=test
```

Then create a `.env` file in the `web-ui` directory:

```bash
cp .env.example .env
```

Edit `.env` and fill in the values from the Terraform outputs:

```env
VITE_AWS_REGION=us-east-1
VITE_COGNITO_USER_POOL_ID=<from cognito_user_pool_id output>
VITE_COGNITO_USER_POOL_CLIENT_ID=<from cognito_user_pool_client_id output>
VITE_COGNITO_DOMAIN=<from cognito_domain output>
VITE_API_ENDPOINT=<from token_api_url output>
VITE_OAUTH_REDIRECT_SIGN_IN=http://localhost:3000/callback
VITE_OAUTH_REDIRECT_SIGN_OUT=http://localhost:3000
```

### 3. Run Development Server

```bash
npm run dev
```

The application will be available at http://localhost:3000

### 4. Login

Use your Cognito user credentials to log in. If you don't have a user account, an administrator needs to create one in the AWS Cognito console.

## Available Scripts

- `npm run dev` - Start development server on port 3000
- `npm run build` - Build production bundle
- `npm run preview` - Preview production build locally
- `npm run lint` - Run ESLint
- `npm run type-check` - Run TypeScript type checking

## Project Structure

```
web-ui/
├── src/
│   ├── components/          # React components
│   │   ├── AuthProvider.tsx # Authentication context provider
│   │   ├── Layout.tsx       # Main application layout
│   │   └── ProtectedRoute.tsx # Route protection wrapper
│   ├── pages/               # Page components
│   │   ├── LoginPage.tsx    # Login page with Cognito
│   │   ├── DashboardPage.tsx # Main dashboard
│   │   └── CallbackPage.tsx # OAuth callback handler
│   ├── utils/               # Utility functions
│   │   └── api-client.ts    # API client with auth
│   ├── config/              # Configuration
│   │   └── amplify.ts       # AWS Amplify configuration
│   ├── types/               # TypeScript types
│   │   └── index.ts         # Type definitions
│   ├── App.tsx              # Main application component
│   ├── main.tsx             # Application entry point
│   └── index.css            # Global styles
├── index.html               # HTML template
├── vite.config.ts           # Vite configuration
├── tsconfig.json            # TypeScript configuration
└── package.json             # Dependencies and scripts
```

## Authentication Flow

1. User navigates to the application
2. If not authenticated, redirected to `/login`
3. AWS Amplify Authenticator component handles Cognito login
4. On successful login, user is redirected to `/dashboard`
5. All API calls include JWT token from Cognito in Authorization header

## API Integration

The application connects to the API Gateway endpoints:

- `GET /api/users/me` - Get current user information
- `GET /api/token/status` - Get Gmail token status
- `POST /api/token/renew` - Initiate token renewal
- `GET /api/token/callback` - Handle OAuth callback
- `GET /api/health` - Health check

All authenticated requests automatically include the Cognito JWT token.

## Building for Production

```bash
npm run build
```

The production build will be output to the `dist/` directory, ready for deployment to S3.

## Deployment

Deployment to S3 and CloudFront will be configured in task 8.1 and 8.2.

## Development Notes

- The app uses Vite for fast development and optimized production builds
- TypeScript provides type safety throughout the application
- AWS Amplify handles Cognito authentication and session management
- React Router provides client-side routing
- All environment variables must be prefixed with `VITE_` to be accessible

## Troubleshooting

### "No authentication token available" error

- Ensure you're logged in to Cognito
- Check that your session hasn't expired
- Try logging out and logging back in

### Environment variables not loading

- Ensure `.env` file exists in the `web-ui` directory
- Verify all variables are prefixed with `VITE_`
- Restart the development server after changing `.env`

### API calls failing

- Verify the API endpoint URL is correct
- Check that the Cognito configuration matches your infrastructure
- Ensure the API Gateway is deployed and accessible
- Check browser console for detailed error messages

## Next Steps

- **Task 7.2**: Implement token status dashboard with real-time data
- **Task 7.3**: Create Google OAuth renewal flow interface
- **Task 8.1**: Configure S3 bucket for web UI hosting
- **Task 8.2**: Update CloudFront distribution for production deployment
