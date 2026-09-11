import React, { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { UserSession } from '../types';
import {
  User,
  ShieldCheck,
  Github,
  KeyRound,
  Laptop,
  Smartphone,
  LogOut,
  AlertCircle,
  CheckCircle2,
  RefreshCw,
  Trash2,
} from 'lucide-react';

export const AccountSettings: React.FC = () => {
  const { user, githubConnected, hasPassword, logout, refreshUser } = useAuth();

  const [sessions, setSessions] = useState<UserSession[]>([]);
  const [isLoadingSessions, setIsLoadingSessions] = useState(false);

  // Change password state
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const showMsg = (text: string, type: 'success' | 'error') => {
    setMessage({ text, type });
    setTimeout(() => setMessage(null), 5000);
  };

  const loadSessions = async () => {
    setIsLoadingSessions(true);
    try {
      const res = await fetch('/api/v1/auth/sessions', { credentials: 'include' });
      if (res.ok) {
        const data = await res.json();
        setSessions(data);
      }
    } catch {
      // ignore
    } finally {
      setIsLoadingSessions(false);
    }
  };

  useEffect(() => {
    loadSessions();
  }, []);

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentPassword || !newPassword) {
      showMsg('Please fill out all password fields.', 'error');
      return;
    }
    if (newPassword !== confirmPassword) {
      showMsg('New passwords do not match.', 'error');
      return;
    }
    if (newPassword.length < 8) {
      showMsg('New password must be at least 8 characters long.', 'error');
      return;
    }

    setIsChangingPassword(true);
    try {
      const res = await fetch('/api/v1/auth/password/change', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        showMsg('Password changed successfully.', 'success');
        setCurrentPassword('');
        setNewPassword('');
        setConfirmPassword('');
        await refreshUser();
        await loadSessions();
      } else {
        showMsg(data.detail || 'Failed to change password.', 'error');
      }
    } catch {
      showMsg('Network error updating password.', 'error');
    } finally {
      setIsChangingPassword(false);
    }
  };

  const handleRevokeSession = async (sessionId: string) => {
    try {
      const res = await fetch(`/api/v1/auth/sessions/${sessionId}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (res.ok) {
        showMsg('Session revoked.', 'success');
        setSessions(sessions.filter((s) => s.id !== sessionId));
      }
    } catch {
      showMsg('Failed to revoke session.', 'error');
    }
  };

  const handleConnectGitHub = async () => {
    try {
      const res = await fetch('/api/v1/auth/github/authorize');
      const data = await res.json();
      if (data.authorize_url) {
        window.location.href = data.authorize_url;
      }
    } catch {
      showMsg('Failed to initiate GitHub connection.', 'error');
    }
  };

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-8 animate-in fade-in duration-300">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white tracking-tight">Account & Security</h1>
        <p className="text-sm text-slate-400 mt-1">
          Manage your verified individual developer identity, credentials, and active sessions.
        </p>
      </div>

      {message && (
        <div
          className={`p-4 rounded-xl border flex items-center gap-3 text-sm ${
            message.type === 'success'
              ? 'bg-emerald-950/40 border-emerald-800/60 text-emerald-300'
              : 'bg-red-950/40 border-red-800/60 text-red-300'
          }`}
        >
          {message.type === 'success' ? (
            <CheckCircle2 className="w-5 h-5 shrink-0 text-emerald-400" />
          ) : (
            <AlertCircle className="w-5 h-5 shrink-0 text-red-400" />
          )}
          <span>{message.text}</span>
        </div>
      )}

      {/* Profile & Identity Card */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-sm">
        <h2 className="text-base font-bold text-white flex items-center gap-2 mb-4">
          <User className="w-5 h-5 text-indigo-400" />
          <span>User Profile</span>
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">
              Display Name
            </label>
            <div className="text-sm font-medium text-slate-200">
              {user?.display_name || 'Individual Developer'}
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">
              Email Address
            </label>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-slate-200">{user?.email}</span>
              {user?.email_verified_at ? (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 px-2 py-0.5 rounded-full">
                  <ShieldCheck className="w-3 h-3" />
                  <span>Verified</span>
                </span>
              ) : (
                <span className="text-[11px] font-medium bg-amber-950/60 border border-amber-800/60 text-amber-400 px-2 py-0.5 rounded-full">
                  Pending Verification
                </span>
              )}
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">
              Account ID (Server-Derived)
            </label>
            <div className="text-xs font-mono text-slate-400 select-all">{user?.id}</div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">
              Account Status
            </label>
            <div className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-400">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span>{user?.status || 'ACTIVE'}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Connected Identities Card */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-sm">
        <h2 className="text-base font-bold text-white flex items-center gap-2 mb-4">
          <Github className="w-5 h-5 text-indigo-400" />
          <span>Connected External Identities</span>
        </h2>

        <div className="flex items-center justify-between p-4 rounded-xl bg-slate-950 border border-slate-800">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-center text-white">
              <Github className="w-5 h-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-white">GitHub</div>
              <div className="text-xs text-slate-400">
                {githubConnected ? 'Linked to your GitHub account' : 'Not connected'}
              </div>
            </div>
          </div>

          {githubConnected ? (
            <span className="text-xs font-medium bg-emerald-950/60 border border-emerald-800/60 text-emerald-400 px-3 py-1 rounded-lg flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span>Connected</span>
            </span>
          ) : (
            <button
              type="button"
              onClick={handleConnectGitHub}
              className="text-xs font-semibold px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-white transition-colors"
            >
              Connect GitHub
            </button>
          )}
        </div>
      </div>

      {/* Password Management Card */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-sm">
        <h2 className="text-base font-bold text-white flex items-center gap-2 mb-4">
          <KeyRound className="w-5 h-5 text-indigo-400" />
          <span>Password Security</span>
        </h2>

        <div className="mb-4 text-xs text-slate-400">
          Status:{' '}
          <span className="font-semibold text-slate-200">
            {hasPassword ? 'Password is set (Scrypt / Argon2id secured)' : 'No password set (OTP / GitHub login only)'}
          </span>
        </div>

        <form onSubmit={handleChangePassword} className="space-y-4 max-w-md">
          {hasPassword && (
            <div>
              <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                Current Password
              </label>
              <input
                type="password"
                required
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                placeholder="••••••••••••"
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
              />
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              New Password (min 8 chars)
            </label>
            <input
              type="password"
              required
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="••••••••••••"
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              Confirm New Password
            </label>
            <input
              type="password"
              required
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="••••••••••••"
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
            />
          </div>

          <button
            type="submit"
            disabled={isChangingPassword}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99] disabled:opacity-60"
          >
            {isChangingPassword ? <RefreshCw className="w-4 h-4 animate-spin" /> : <span>Update Password</span>}
          </button>
        </form>
      </div>

      {/* Active Sessions Card */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-sm">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Laptop className="w-5 h-5 text-indigo-400" />
            <span>Active Sessions</span>
          </h2>
          <button
            type="button"
            onClick={loadSessions}
            disabled={isLoadingSessions}
            className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors flex items-center gap-1"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoadingSessions ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>

        <div className="space-y-3">
          {sessions.map((sess) => (
            <div
              key={sess.id}
              className="flex items-center justify-between p-4 rounded-xl bg-slate-950 border border-slate-800"
            >
              <div className="flex items-center gap-3">
                <div className="w-9 h-9 rounded-lg bg-slate-900 border border-slate-800 flex items-center justify-center text-slate-400">
                  {sess.user_agent?.toLowerCase().includes('mobile') ? (
                    <Smartphone className="w-4 h-4" />
                  ) : (
                    <Laptop className="w-4 h-4" />
                  )}
                </div>
                <div>
                  <div className="text-xs font-semibold text-white flex items-center gap-2">
                    <span>{sess.user_agent || 'Unknown Browser'}</span>
                    {sess.is_current && (
                      <span className="text-[10px] font-mono font-semibold px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">
                        Current Session
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                    IP: {sess.ip_address || '127.0.0.1'} • Created: {new Date(sess.created_at).toLocaleDateString()}
                  </div>
                </div>
              </div>

              {!sess.is_current && (
                <button
                  type="button"
                  onClick={() => handleRevokeSession(sess.id)}
                  className="p-2 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-950/30 transition-colors"
                  title="Revoke session"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Danger Zone */}
      <div className="bg-slate-900 border border-red-950/60 rounded-2xl p-6 shadow-sm">
        <h2 className="text-base font-bold text-red-400 flex items-center gap-2 mb-2">
          <LogOut className="w-5 h-5 text-red-400" />
          <span>Sign Out</span>
        </h2>
        <p className="text-xs text-slate-400 mb-4">
          Terminate your active authenticated session on this browser.
        </p>

        <button
          type="button"
          onClick={logout}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-red-950/60 hover:bg-red-900/60 border border-red-800/60 text-red-300 hover:text-white font-semibold text-xs transition-colors"
        >
          <LogOut className="w-4 h-4" />
          <span>Sign Out of CortexForge</span>
        </button>
      </div>
    </div>
  );
};
