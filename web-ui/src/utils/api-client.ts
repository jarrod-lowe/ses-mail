import { fetchAuthSession } from 'aws-amplify/auth';
import { getApiEndpoint } from '../config/amplify';
import type { UserInfo, TokenStatus, RenewalResponse } from '../types';

class ApiClient {
  private baseUrl: string;

  constructor() {
    this.baseUrl = getApiEndpoint();
  }

  private async getAuthHeaders(): Promise<HeadersInit> {
    try {
      const session = await fetchAuthSession();
      const token = session.tokens?.idToken?.toString();

      if (!token) {
        throw new Error('No authentication token available');
      }

      return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      };
    } catch (error) {
      console.error('Failed to get auth headers:', error);
      throw new Error('Authentication failed');
    }
  }

  private async makeRequest<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const headers = await this.getAuthHeaders();
    const url = `${this.baseUrl}${endpoint}`;

    const response = await fetch(url, {
      ...options,
      headers: {
        ...headers,
        ...options.headers,
      },
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({
        error: 'API Error',
        message: `Request failed with status ${response.status}`,
      }));
      throw new Error(errorData.message || 'API request failed');
    }

    return response.json();
  }

  async getUserInfo(): Promise<UserInfo> {
    return this.makeRequest<UserInfo>('/api/users/me');
  }

  async getTokenStatus(): Promise<TokenStatus> {
    return this.makeRequest<TokenStatus>('/api/token/status');
  }

  async initiateTokenRenewal(): Promise<RenewalResponse> {
    return this.makeRequest<RenewalResponse>('/api/token/renew', {
      method: 'POST',
    });
  }

  async handleOAuthCallback(code: string, state: string): Promise<void> {
    await this.makeRequest<void>(
      `/api/token/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}`
    );
  }

  async checkHealth(): Promise<{ status: string; timestamp: string }> {
    const url = `${this.baseUrl}/api/health`;
    const response = await fetch(url);
    return response.json();
  }
}

export const apiClient = new ApiClient();
