import React, { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { apiClient } from '../utils/api-client';

export const CallbackPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState<'processing' | 'success' | 'error'>('processing');
  const [message, setMessage] = useState('Processing OAuth callback...');

  useEffect(() => {
    const handleCallback = async () => {
      const code = searchParams.get('code');
      const state = searchParams.get('state');

      if (!code || !state) {
        setStatus('error');
        setMessage('Invalid OAuth callback parameters');
        setTimeout(() => navigate('/dashboard'), 3000);
        return;
      }

      try {
        await apiClient.handleOAuthCallback(code, state);
        setStatus('success');
        setMessage('OAuth callback successful! Redirecting...');
        setTimeout(() => navigate('/dashboard'), 2000);
      } catch (error) {
        console.error('OAuth callback error:', error);
        setStatus('error');
        setMessage(
          error instanceof Error ? error.message : 'OAuth callback failed'
        );
        setTimeout(() => navigate('/dashboard'), 3000);
      }
    };

    handleCallback();
  }, [searchParams, navigate]);

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: '#f5f5f5'
    }}>
      <div style={{
        backgroundColor: 'white',
        padding: '3rem',
        borderRadius: '8px',
        boxShadow: '0 4px 6px rgba(0,0,0,0.1)',
        maxWidth: '500px',
        width: '100%',
        textAlign: 'center'
      }}>
        {status === 'processing' && (
          <>
            <div style={{
              fontSize: '3rem',
              marginBottom: '1rem'
            }}>
              ⏳
            </div>
            <h2 style={{ color: '#232f3e', marginBottom: '1rem' }}>
              Processing...
            </h2>
            <p style={{ color: '#666' }}>{message}</p>
          </>
        )}

        {status === 'success' && (
          <>
            <div style={{
              fontSize: '3rem',
              marginBottom: '1rem',
              color: '#28a745'
            }}>
              ✓
            </div>
            <h2 style={{ color: '#28a745', marginBottom: '1rem' }}>
              Success!
            </h2>
            <p style={{ color: '#666' }}>{message}</p>
          </>
        )}

        {status === 'error' && (
          <>
            <div style={{
              fontSize: '3rem',
              marginBottom: '1rem',
              color: '#dc3545'
            }}>
              ✗
            </div>
            <h2 style={{ color: '#dc3545', marginBottom: '1rem' }}>
              Error
            </h2>
            <p style={{ color: '#666' }}>{message}</p>
          </>
        )}
      </div>
    </div>
  );
};
