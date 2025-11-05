// User information from Cognito
export interface UserInfo {
  userId: string;
  email: string;
  givenName?: string;
  familyName?: string;
}

// Token status from API
export interface TokenStatus {
  hasToken: boolean;
  expiresAt?: string;
  daysUntilExpiration?: number;
  gmailAddress?: string;
  status: 'valid' | 'expiring_soon' | 'expired' | 'not_configured';
}

// API error response
export interface ApiError {
  error: string;
  message: string;
  statusCode?: number;
}

// OAuth renewal response
export interface RenewalResponse {
  authUrl: string;
  state: string;
}
