import React from 'react';
import { Layout } from '../components/Layout';
import { useAuth } from '../components/AuthProvider';

export const DashboardPage: React.FC = () => {
  const { userInfo } = useAuth();

  return (
    <Layout>
      <div>
        <h2 style={{ marginTop: 0, color: '#232f3e' }}>
          Welcome to Token Management
        </h2>

        <div style={{
          backgroundColor: '#e7f6f8',
          border: '1px solid #00a1c9',
          borderRadius: '4px',
          padding: '1rem',
          marginBottom: '2rem'
        }}>
          <p style={{ margin: 0, color: '#00576b' }}>
            <strong>Authenticated as:</strong> {userInfo?.email}
          </p>
          {userInfo?.userId && (
            <p style={{ margin: '0.5rem 0 0 0', fontSize: '0.875rem', color: '#00576b' }}>
              <strong>User ID:</strong> {userInfo.userId}
            </p>
          )}
        </div>

        <div style={{
          backgroundColor: 'white',
          border: '1px solid #ddd',
          borderRadius: '8px',
          padding: '2rem',
          marginBottom: '2rem'
        }}>
          <h3 style={{ marginTop: 0, color: '#232f3e' }}>
            Token Status Dashboard
          </h3>
          <p style={{ color: '#666' }}>
            The token status dashboard will be implemented in task 7.2.
          </p>
          <p style={{ color: '#666', fontSize: '0.875rem' }}>
            This dashboard will show:
          </p>
          <ul style={{ color: '#666', fontSize: '0.875rem' }}>
            <li>Current Gmail refresh token status</li>
            <li>Token expiration information</li>
            <li>Renewal history</li>
            <li>Quick actions for token renewal</li>
          </ul>
        </div>

        <div style={{
          backgroundColor: 'white',
          border: '1px solid #ddd',
          borderRadius: '8px',
          padding: '2rem'
        }}>
          <h3 style={{ marginTop: 0, color: '#232f3e' }}>
            Google OAuth Renewal
          </h3>
          <p style={{ color: '#666' }}>
            The Google OAuth renewal flow will be implemented in task 7.3.
          </p>
          <p style={{ color: '#666', fontSize: '0.875rem' }}>
            This interface will provide:
          </p>
          <ul style={{ color: '#666', fontSize: '0.875rem' }}>
            <li>Secure OAuth flow integration</li>
            <li>Token renewal interface</li>
            <li>Callback handling</li>
            <li>User feedback during renewal process</li>
          </ul>
        </div>
      </div>
    </Layout>
  );
};
