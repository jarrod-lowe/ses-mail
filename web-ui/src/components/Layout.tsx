import React from 'react';
import { useAuth } from './AuthProvider';

interface LayoutProps {
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  const { userInfo, logout } = useAuth();

  const handleLogout = async () => {
    if (confirm('Are you sure you want to log out?')) {
      await logout();
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <header style={{
        backgroundColor: '#232f3e',
        color: 'white',
        padding: '1rem 2rem',
        boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
      }}>
        <div style={{
          maxWidth: '1200px',
          margin: '0 auto',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center'
        }}>
          <div>
            <h1 style={{ margin: 0, fontSize: '1.5rem' }}>
              SES Mail - Token Management
            </h1>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem' }}>
            {userInfo && (
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: '0.9rem', opacity: 0.9 }}>
                  {userInfo.givenName && userInfo.familyName
                    ? `${userInfo.givenName} ${userInfo.familyName}`
                    : userInfo.email}
                </div>
                <div style={{ fontSize: '0.75rem', opacity: 0.7 }}>
                  {userInfo.email}
                </div>
              </div>
            )}

            <button
              onClick={handleLogout}
              style={{
                backgroundColor: '#ff9900',
                color: '#232f3e',
                border: 'none',
                padding: '0.5rem 1rem',
                borderRadius: '4px',
                cursor: 'pointer',
                fontWeight: 'bold',
                fontSize: '0.9rem'
              }}
            >
              Logout
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main style={{
        flex: 1,
        maxWidth: '1200px',
        margin: '0 auto',
        padding: '2rem',
        width: '100%'
      }}>
        {children}
      </main>

      {/* Footer */}
      <footer style={{
        backgroundColor: '#f5f5f5',
        padding: '1rem 2rem',
        textAlign: 'center',
        color: '#666',
        fontSize: '0.875rem',
        borderTop: '1px solid #ddd'
      }}>
        <p style={{ margin: 0 }}>
          SES Mail Token Management System &copy; {new Date().getFullYear()}
        </p>
      </footer>
    </div>
  );
};
