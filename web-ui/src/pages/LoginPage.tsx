import React, { useEffect } from 'react';
import { Authenticator } from '@aws-amplify/ui-react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../components/AuthProvider';
import '@aws-amplify/ui-react/styles.css';

export const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();

  useEffect(() => {
    if (isAuthenticated) {
      navigate('/dashboard');
    }
  }, [isAuthenticated, navigate]);

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: '#f5f5f5'
    }}>
      <div style={{
        backgroundColor: 'white',
        padding: '2rem',
        borderRadius: '8px',
        boxShadow: '0 4px 6px rgba(0,0,0,0.1)',
        maxWidth: '500px',
        width: '100%'
      }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <h1 style={{ color: '#232f3e', marginBottom: '0.5rem' }}>
            SES Mail
          </h1>
          <h2 style={{ color: '#666', fontSize: '1.2rem', fontWeight: 'normal' }}>
            Token Management
          </h2>
        </div>

        <Authenticator
          hideSignUp={true}
          components={{
            Header() {
              return (
                <div style={{ padding: '1rem 0', textAlign: 'center' }}>
                  <p style={{ color: '#666', margin: 0 }}>
                    Sign in to manage your Gmail OAuth tokens
                  </p>
                </div>
              );
            },
          }}
        />
      </div>

      <div style={{
        marginTop: '2rem',
        textAlign: 'center',
        color: '#666',
        fontSize: '0.875rem'
      }}>
        <p>Authorized users only</p>
      </div>
    </div>
  );
};
