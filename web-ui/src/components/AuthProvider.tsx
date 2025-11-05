import React, { createContext, useContext, useEffect, useState } from 'react';
import { getCurrentUser, fetchAuthSession, signOut } from 'aws-amplify/auth';
import type { UserInfo } from '../types';
import { apiClient } from '../utils/api-client';

interface AuthContextType {
  isAuthenticated: boolean;
  isLoading: boolean;
  userInfo: UserInfo | null;
  error: string | null;
  logout: () => Promise<void>;
  refreshUserInfo: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
};

interface AuthProviderProps {
  children: React.ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadUserInfo = async () => {
    try {
      setError(null);
      // First check if user is authenticated with Cognito
      await getCurrentUser();

      // Verify we can get a valid session
      const session = await fetchAuthSession();
      if (!session.tokens?.idToken) {
        throw new Error('No valid session token');
      }

      // Fetch user info from API
      const info = await apiClient.getUserInfo();
      setUserInfo(info);
      setIsAuthenticated(true);
    } catch (err) {
      console.error('Failed to load user info:', err);
      setIsAuthenticated(false);
      setUserInfo(null);
      setError(err instanceof Error ? err.message : 'Authentication failed');
    } finally {
      setIsLoading(false);
    }
  };

  const logout = async () => {
    try {
      await signOut();
      setIsAuthenticated(false);
      setUserInfo(null);
      setError(null);
    } catch (err) {
      console.error('Logout failed:', err);
      setError(err instanceof Error ? err.message : 'Logout failed');
    }
  };

  const refreshUserInfo = async () => {
    if (isAuthenticated) {
      await loadUserInfo();
    }
  };

  useEffect(() => {
    loadUserInfo();
  }, []);

  const value: AuthContextType = {
    isAuthenticated,
    isLoading,
    userInfo,
    error,
    logout,
    refreshUserInfo,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
