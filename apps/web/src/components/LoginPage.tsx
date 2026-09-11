import React, { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  Brain,
  Github,
  Mail,
  Lock,
  User as UserIcon,
  ShieldCheck,
  KeyRound,
  AlertCircle,
  CheckCircle2,
  RefreshCw,
} from 'lucide-react';

type AuthView = 'signin' | 'signup' | 'otp' | 'forgot';

export const LoginPage: React.FC = () => {
  const { loginWithPassword, register, requestOTP, verifyOTP } = useAuth();

  const [view, setView] = useState<AuthView>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [otp, setOtp] = useState('');
  const [otpSent, setOtpSent] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);

  // Forgot password state
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [resetSent, setResetSent] = useState(false);

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    let timer: any;
    if (resendCooldown > 0) {
      timer = setTimeout(() => setResendCooldown(resendCooldown - 1), 1000);
    }
    return () => clearTimeout(timer);
  }, [resendCooldown]);

  const clearMessages = () => {
    setError(null);
    setSuccessMsg(null);
  };

  const handleGitHubLogin = async () => {
    try {
      setIsLoading(true);
      clearMessages();
      const res = await fetch('/api/v1/auth/github?format=json');
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setError(errData.detail || 'GitHub authorization unavailable. Please check server configuration.');
        return;
      }
      const data = await res.json();
      if (data.authorize_url) {
        window.location.href = data.authorize_url;
      } else {
        window.location.href = '/api/v1/auth/github';
      }
    } catch {
      setError('Failed to initiate GitHub authentication.');
    } finally {
      setIsLoading(false);
    }
  };

  const handlePasswordSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    clearMessages();
    if (!email || !password) {
      setError('Please provide both email and password.');
      return;
    }
    setIsLoading(true);
    const res = await loginWithPassword(email, password);
    setIsLoading(false);
    if (!res.success) {
      setError(res.error || 'Invalid credentials');
    }
  };

  const handleSignUp = async (e: React.FormEvent) => {
    e.preventDefault();
    clearMessages();
    if (!email || !password) {
      setError('Please provide email and password.');
      return;
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters long.');
      return;
    }
    setIsLoading(true);
    const res = await register(email, password, name);
    setIsLoading(false);
    if (!res.success) {
      setError(res.error || 'Registration failed');
    }
  };

  const handleSendOTP = async (e: React.FormEvent) => {
    e.preventDefault();
    clearMessages();
    if (!email) {
      setError('Please enter your email address.');
      return;
    }
    setIsLoading(true);
    const res = await requestOTP(email);
    setIsLoading(false);
    if (res.success) {
      setOtpSent(true);
      setResendCooldown(60);
      setSuccessMsg(res.message || 'Verification code sent to your email.');
      if (res.debugOtp) {
        // Shown for test/dev convenience
        setSuccessMsg(`Test Mode OTP: ${res.debugOtp}`);
      }
    } else {
      setError(res.error || 'Failed to send verification code.');
    }
  };

  const handleVerifyOTP = async (e: React.FormEvent) => {
    e.preventDefault();
    clearMessages();
    if (!otp || otp.length !== 6) {
      setError('Please enter the 6-digit verification code.');
      return;
    }
    setIsLoading(true);
    const res = await verifyOTP(email, otp);
    setIsLoading(false);
    if (!res.success) {
      setError(res.error || 'Invalid verification code.');
    }
  };

  const handleRequestPasswordReset = async (e: React.FormEvent) => {
    e.preventDefault();
    clearMessages();
    if (!email) {
      setError('Please enter your email address.');
      return;
    }
    setIsLoading(true);
    try {
      const res = await fetch('/api/v1/auth/password/reset-request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();
      setResetSent(true);
      setSuccessMsg(data.message || 'Password reset link sent.');
      if (data.debug_token) {
        setResetToken(data.debug_token);
        setSuccessMsg(`Test Mode Reset Token: ${data.debug_token}`);
      }
    } catch {
      setError('Failed to request password reset.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleConfirmPasswordReset = async (e: React.FormEvent) => {
    e.preventDefault();
    clearMessages();
    if (!resetToken || !newPassword) {
      setError('Please provide both the reset token and your new password.');
      return;
    }
    if (newPassword.length < 8) {
      setError('Password must be at least 8 characters long.');
      return;
    }
    setIsLoading(true);
    try {
      const res = await fetch('/api/v1/auth/password/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken, new_password: newPassword }),
      });
      const data = await res.json();
      if (res.ok) {
        setSuccessMsg('Password updated successfully! Please sign in.');
        setView('signin');
        setPassword('');
      } else {
        setError(data.detail || 'Failed to reset password.');
      }
    } catch {
      setError('Network error resetting password.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full bg-slate-950 text-slate-100 flex items-center justify-center p-4 relative overflow-hidden font-sans">
      {/* Subtle background ambient gradients */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute bottom-10 right-10 w-72 h-72 bg-violet-600/10 rounded-full blur-2xl pointer-events-none" />

      <div className="w-full max-w-md relative z-10">
        {/* Brand Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-tr from-indigo-500/20 to-violet-500/20 border border-indigo-500/30 text-indigo-400 mb-4 shadow-lg shadow-indigo-500/10">
            <Brain className="w-8 h-8" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center justify-center gap-2">
            CortexForge
            <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-semibold">
              Cognitive Core
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1 font-medium">Your AI Project Brain</p>
        </div>

        {/* Card Container */}
        <div className="bg-slate-900/80 backdrop-blur-xl border border-slate-800 rounded-2xl p-6 sm:p-8 shadow-2xl shadow-black/60">
          {/* Alerts */}
          {error && (
            <div className="mb-6 p-3 rounded-xl bg-red-950/40 border border-red-800/60 text-red-300 text-xs flex items-center gap-2.5">
              <AlertCircle className="w-4 h-4 shrink-0 text-red-400" />
              <span>{error}</span>
            </div>
          )}

          {successMsg && (
            <div className="mb-6 p-3 rounded-xl bg-emerald-950/40 border border-emerald-800/60 text-emerald-300 text-xs flex items-center gap-2.5">
              <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-400" />
              <span>{successMsg}</span>
            </div>
          )}

          {/* VIEW: SIGN IN */}
          {view === 'signin' && (
            <div>
              {/* GitHub Login */}
              <button
                type="button"
                onClick={handleGitHubLogin}
                disabled={isLoading}
                className="w-full flex items-center justify-center gap-3 py-2.5 px-4 rounded-xl bg-slate-800 hover:bg-slate-750 border border-slate-700 hover:border-slate-600 text-white font-medium text-sm transition-all shadow-sm active:scale-[0.99] disabled:opacity-60"
              >
                <Github className="w-4 h-4" />
                <span>Continue with GitHub</span>
              </button>

              {/* Divider */}
              <div className="flex items-center my-5">
                <div className="flex-1 border-t border-slate-800" />
                <span className="px-3 text-xs uppercase tracking-wider text-slate-500 font-mono">OR</span>
                <div className="flex-1 border-t border-slate-800" />
              </div>

              {/* Password Form */}
              <form onSubmit={handlePasswordSignIn} className="space-y-4">
                <div>
                  <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                    Email
                  </label>
                  <div className="relative">
                    <Mail className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="developer@example.com"
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-10 pr-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                      Password
                    </label>
                    <button
                      type="button"
                      onClick={() => {
                        clearMessages();
                        setView('forgot');
                      }}
                      className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
                    >
                      Forgot password?
                    </button>
                  </div>
                  <div className="relative">
                    <Lock className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      type="password"
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••••••"
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-10 pr-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={isLoading}
                  className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99] disabled:opacity-60 mt-2"
                >
                  {isLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <span>Sign In</span>}
                </button>
              </form>

              {/* Passwordless OTP alternative */}
              <div className="mt-5 pt-5 border-t border-slate-800/80">
                <button
                  type="button"
                  onClick={() => {
                    clearMessages();
                    setView('otp');
                  }}
                  className="w-full flex items-center justify-center gap-2 py-2 px-4 rounded-xl bg-slate-950 hover:bg-slate-800/60 border border-slate-800 text-slate-300 hover:text-white font-medium text-xs transition-colors"
                >
                  <KeyRound className="w-3.5 h-3.5 text-indigo-400" />
                  <span>Continue with Email OTP</span>
                </button>
              </div>

              {/* Switch to SignUp */}
              <div className="mt-6 text-center text-xs text-slate-400">
                Don't have an account?{' '}
                <button
                  type="button"
                  onClick={() => {
                    clearMessages();
                    setView('signup');
                  }}
                  className="text-indigo-400 hover:text-indigo-300 font-semibold transition-colors"
                >
                  Create account
                </button>
              </div>
            </div>
          )}

          {/* VIEW: SIGN UP */}
          {view === 'signup' && (
            <div>
              <div className="mb-5">
                <h2 className="text-lg font-bold text-white">Create your CortexForge account</h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Individual developer cognitive memory layer for your AI agents.
                </p>
              </div>

              {/* GitHub OAuth - Single Unified Flow */}
              <button
                type="button"
                onClick={handleGitHubLogin}
                disabled={isLoading}
                className="w-full flex items-center justify-center gap-3 py-2.5 px-4 rounded-xl bg-slate-800 hover:bg-slate-750 border border-slate-700 hover:border-slate-600 text-white font-medium text-sm transition-all shadow-sm active:scale-[0.99] disabled:opacity-60 mb-5"
              >
                <Github className="w-4 h-4" />
                <span>Continue with GitHub</span>
              </button>

              {/* Divider */}
              <div className="flex items-center my-5">
                <div className="flex-1 border-t border-slate-800" />
                <span className="px-3 text-xs uppercase tracking-wider text-slate-500 font-mono">OR</span>
                <div className="flex-1 border-t border-slate-800" />
              </div>

              <form onSubmit={handleSignUp} className="space-y-4">
                <div>
                  <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                    Name
                  </label>
                  <div className="relative">
                    <UserIcon className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="Alex Developer"
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-10 pr-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                    Email
                  </label>
                  <div className="relative">
                    <Mail className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      type="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="developer@example.com"
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-10 pr-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                    Password (min 8 characters)
                  </label>
                  <div className="relative">
                    <Lock className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      type="password"
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••••••"
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-10 pr-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={isLoading}
                  className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99] disabled:opacity-60 mt-2"
                >
                  {isLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <span>Create Account</span>}
                </button>
              </form>

              <div className="mt-6 text-center text-xs text-slate-400">
                Already have an account?{' '}
                <button
                  type="button"
                  onClick={() => {
                    clearMessages();
                    setView('signin');
                  }}
                  className="text-indigo-400 hover:text-indigo-300 font-semibold transition-colors"
                >
                  Sign in
                </button>
              </div>
            </div>
          )}

          {/* VIEW: EMAIL OTP */}
          {view === 'otp' && (
            <div>
              <div className="mb-5">
                <h2 className="text-lg font-bold text-white">Passwordless Email Login</h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  {!otpSent
                    ? "Enter your email to receive a secure 6-digit one-time code."
                    : `Enter the 6-digit verification code sent to ${email}.`}
                </p>
              </div>

              {!otpSent ? (
                <form onSubmit={handleSendOTP} className="space-y-4">
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      Email
                    </label>
                    <div className="relative">
                      <Mail className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                      <input
                        type="email"
                        required
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        placeholder="user@example.com"
                        className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-10 pr-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                    </div>
                  </div>

                  <button
                    type="submit"
                    disabled={isLoading}
                    className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99] disabled:opacity-60"
                  >
                    {isLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <span>Send OTP</span>}
                  </button>
                </form>
              ) : (
                <form onSubmit={handleVerifyOTP} className="space-y-4">
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      Enter Verification Code
                    </label>
                    <input
                      type="text"
                      maxLength={6}
                      autoFocus
                      required
                      value={otp}
                      onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))}
                      placeholder="000000"
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl text-center font-mono text-xl tracking-[0.5em] py-3 text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={isLoading || otp.length !== 6}
                    className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99] disabled:opacity-60"
                  >
                    {isLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <span>Verify</span>}
                  </button>

                  <div className="flex items-center justify-between text-xs text-slate-400 pt-2">
                    <button
                      type="button"
                      disabled={resendCooldown > 0 || isLoading}
                      onClick={handleSendOTP}
                      className="text-indigo-400 hover:text-indigo-300 disabled:opacity-40 transition-colors font-medium"
                    >
                      {resendCooldown > 0 ? `Resend code in ${resendCooldown}s` : 'Resend code'}
                    </button>

                    <button
                      type="button"
                      onClick={() => {
                        setOtpSent(false);
                        setOtp('');
                      }}
                      className="text-slate-400 hover:text-slate-300 transition-colors"
                    >
                      Change email
                    </button>
                  </div>
                </form>
              )}

              <div className="mt-6 text-center text-xs text-slate-400 border-t border-slate-800/80 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    clearMessages();
                    setView('signin');
                  }}
                  className="text-indigo-400 hover:text-indigo-300 font-semibold transition-colors"
                >
                  ← Back to password sign in
                </button>
              </div>
            </div>
          )}

          {/* VIEW: FORGOT PASSWORD */}
          {view === 'forgot' && (
            <div>
              <div className="mb-5">
                <h2 className="text-lg font-bold text-white">Reset Password</h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  {!resetSent
                    ? "Enter your account email to receive a password reset token."
                    : "Enter your single-use reset token and new password."}
                </p>
              </div>

              {!resetSent ? (
                <form onSubmit={handleRequestPasswordReset} className="space-y-4">
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      Email
                    </label>
                    <div className="relative">
                      <Mail className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                      <input
                        type="email"
                        required
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        placeholder="developer@example.com"
                        className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-10 pr-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                    </div>
                  </div>

                  <button
                    type="submit"
                    disabled={isLoading}
                    className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99] disabled:opacity-60"
                  >
                    {isLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <span>Send Reset Instructions</span>}
                  </button>
                </form>
              ) : (
                <form onSubmit={handleConfirmPasswordReset} className="space-y-4">
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      Reset Token
                    </label>
                    <input
                      type="text"
                      required
                      value={resetToken}
                      onChange={(e) => setResetToken(e.target.value)}
                      placeholder="Paste your reset token"
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm font-mono text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      New Password (min 8 chars)
                    </label>
                    <div className="relative">
                      <Lock className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                      <input
                        type="password"
                        required
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                        placeholder="••••••••••••"
                        className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-10 pr-3 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                    </div>
                  </div>

                  <button
                    type="submit"
                    disabled={isLoading}
                    className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99] disabled:opacity-60"
                  >
                    {isLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <span>Update Password</span>}
                  </button>
                </form>
              )}

              <div className="mt-6 text-center text-xs text-slate-400 border-t border-slate-800/80 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    clearMessages();
                    setView('signin');
                  }}
                  className="text-indigo-400 hover:text-indigo-300 font-semibold transition-colors"
                >
                  ← Back to sign in
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Security / Individual-User-First Footer */}
        <div className="mt-8 text-center text-xs text-slate-500 flex items-center justify-center gap-1.5 font-medium">
          <ShieldCheck className="w-4 h-4 text-slate-400" />
          <span>Individual-User Security • Zero Organization Bloat • Scrypt Encrypted</span>
        </div>
      </div>
    </div>
  );
};
