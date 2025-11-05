import { Amplify } from 'aws-amplify';

export const configureAmplify = () => {
  const config = {
    Auth: {
      Cognito: {
        userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID,
        userPoolClientId: import.meta.env.VITE_COGNITO_USER_POOL_CLIENT_ID,
        loginWith: {
          oauth: {
            domain: import.meta.env.VITE_COGNITO_DOMAIN,
            scopes: ['email', 'openid', 'profile'],
            redirectSignIn: [import.meta.env.VITE_OAUTH_REDIRECT_SIGN_IN],
            redirectSignOut: [import.meta.env.VITE_OAUTH_REDIRECT_SIGN_OUT],
            responseType: 'code' as const,
          },
        },
      },
    },
  };

  Amplify.configure(config);
};

export const getApiEndpoint = (): string => {
  const endpoint = import.meta.env.VITE_API_ENDPOINT;
  if (!endpoint) {
    throw new Error('VITE_API_ENDPOINT environment variable is not set');
  }
  return endpoint;
};
