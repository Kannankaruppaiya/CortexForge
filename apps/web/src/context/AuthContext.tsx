import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { User } from '../types';

interface AuthContextType {
  user: User | null;
  authStatus: 'loading' | 'authenticated' | 'unauthenticated';
  githubConnected: boolean;
  hasPassword: boolean;
  loginWithPassword: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  register: (email: string, password: string, displayName?: string) => Promise<{ success: boolean; error?: string }>;
  requestOTP: (email: string) => Promise<{ success: boolean; message?: string; debugOtp?: string; error?: string }>;
  verifyOTP: (email: string, otp: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const API_BASE = '/api/v1';

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [authStatus, setAuthStatus] = useState<'loading' | 'authenticated' | 'unauthenticated'>('loading');
  const [githubConnected, setGithubConnected] = useState(false);
  const [hasPassword, setHasPassword] = useState(false);

  const refreshUser = async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/me`, {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        setUser(data.user);
        setGithubConnected(data.github_connected);
        setHasPassword(data.has_password);
        setAuthStatus('authenticated');
      } else {
        setUser(null);
        setAuthStatus('unauthenticated');
      }
    } catch {
      setUser(null);
      setAuthStatus('unauthenticated');
    }
  };

  useEffect(() => {
    refreshUser();
  }, []);

  const loginWithPassword = async (email: string, password: string) => {
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) {
        return { success: false, error: data.detail || 'Failed to sign in' };
      }
      setUser(data.user);
      setAuthStatus('authenticated');
      await refreshUser();
      return { success: true };
    } catch (err: any) {
      return { success: false, error: err.message || 'Network error' };
    }
  };

  const register = async (email: string, password: string, displayName?: string) => {
    try {
      const res = await fetch(`${API_BASE}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email, password, display_name: displayName }),
      });
      const data = await res.json();
      if (!res.ok) {
        return { success: false, error: data.detail || 'Registration failed' };
      }
      setUser(data.user);
      setAuthStatus('authenticated');
      await refreshUser();
      return { success: true };
    } catch (err: any) {
      return { success: false, error: err.message || 'Network error' };
    }
  };

  const requestOTP = async (email: string) => {
    try {
      const res = await fetch(`${API_BASE}/auth/otp/request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email }),
      });
      const data = await res.json();
      if (!res.ok) {
        return { success: false, error: data.detail || 'Failed to send OTP' };
      }
      return { success: true, message: data.message, debugOtp: data.debug_otp };
    } catch (err: any) {
      return { success: false, error: err.message || 'Network error' };
    }
  };

  const verifyOTP = async (email: string, otp: string) => {
    try {
      const res = await fetch(`${API_BASE}/auth/otp/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email, otp }),
      });
      const data = await res.json();
      if (!res.ok) {
        return { success: false, error: data.detail || 'Failed to verify OTP' };
      }
      setUser(data.user);
      setAuthStatus('authenticated');
      await refreshUser();
      return { success: true };
    } catch (err: any) {
      return { success: false, error: err.message || 'Network error' };
    }
  };

  const logout = async () => {
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } finally {
      setUser(null);
      setAuthStatus('unauthenticated');
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        authStatus,
        githubConnected,
        hasPassword,
        loginWithPassword,
        register,
        requestOTP,
        verifyOTP,
        logout,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
