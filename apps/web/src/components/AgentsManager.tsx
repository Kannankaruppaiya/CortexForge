import React, { useState, useEffect } from 'react';
import { Agent, AgentCredential, AgentPermission, Project } from '../types';
import {
  fetchAgentCredentials,
  createAgentCredential,
  revokeAgentCredential,
} from '../api';
import {
  Bot,
  Key,
  ShieldCheck,
  Plus,
  Trash2,
  CheckCircle2,
  AlertCircle,
  Copy,
  Check,
  FolderGit2,
  RefreshCw,
} from 'lucide-react';

interface AgentsManagerProps {
  projects: Project[];
}

export const AgentsManager: React.FC<AgentsManagerProps> = ({ projects }) => {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [permissions, setPermissions] = useState<AgentPermission[]>([]);
  const [credentials, setCredentials] = useState<AgentCredential[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingPerms, setIsLoadingPerms] = useState(false);
  const [isLoadingCreds, setIsLoadingCreds] = useState(false);

  // New Agent Modal / Form State
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [agentName, setAgentName] = useState('');
  const [agentType, setAgentType] = useState('claude');
  const [newApiKey, setNewApiKey] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState(false);

  // Key Rotation Modal State
  const [showRotateModal, setShowRotateModal] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [rotatedSecret, setRotatedSecret] = useState<string | null>(null);
  const [copiedRotatedKey, setCopiedRotatedKey] = useState(false);

  const [toast, setToast] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const showToast = (text: string, type: 'success' | 'error' = 'success') => {
    setToast({ text, type });
    setTimeout(() => setToast(null), 4000);
  };

  const loadAgents = async () => {
    setIsLoading(true);
    try {
      const res = await fetch('/api/v1/agents', { credentials: 'include' });
      if (res.ok) {
        const data: Agent[] = await res.json();
        setAgents(data);
        if (data.length > 0 && !selectedAgent) {
          setSelectedAgent(data[0]);
        }
      }
    } catch {
      showToast('Failed to load agents', 'error');
    } finally {
      setIsLoading(false);
    }
  };

  const loadPermissions = async (agentId: string) => {
    setIsLoadingPerms(true);
    try {
      const res = await fetch(`/api/v1/agents/${agentId}/permissions`, { credentials: 'include' });
      if (res.ok) {
        const data: AgentPermission[] = await res.json();
        setPermissions(data);
      }
    } catch {
      showToast('Failed to load agent permissions', 'error');
    } finally {
      setIsLoadingPerms(false);
    }
  };

  const loadCredentials = async (agentId: string) => {
    setIsLoadingCreds(true);
    try {
      const data = await fetchAgentCredentials(agentId);
      setCredentials(data);
    } catch {
      showToast('Failed to load agent credentials', 'error');
    } finally {
      setIsLoadingCreds(false);
    }
  };

  useEffect(() => {
    loadAgents();
  }, []);

  useEffect(() => {
    if (selectedAgent) {
      loadPermissions(selectedAgent.id);
      loadCredentials(selectedAgent.id);
    } else {
      setPermissions([]);
      setCredentials([]);
    }
  }, [selectedAgent]);

  const handleRotateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedAgent) return;
    try {
      const cred = await createAgentCredential(selectedAgent.id, newKeyName.trim() || 'rotated');
      if (cred) {
        setRotatedSecret(cred.secret);
        showToast('New credential generated! Save the secret key.', 'success');
        loadCredentials(selectedAgent.id);
      } else {
        showToast('Failed to generate credential', 'error');
      }
    } catch {
      showToast('Error generating credential', 'error');
    }
  };

  const handleRevokeKey = async (keyId: string) => {
    if (!selectedAgent) return;
    if (!confirm('Are you sure you want to revoke this credential key? It will immediately stop working.')) return;
    try {
      const ok = await revokeAgentCredential(selectedAgent.id, keyId);
      if (ok) {
        showToast('Credential revoked', 'success');
        loadCredentials(selectedAgent.id);
      } else {
        showToast('Failed to revoke credential', 'error');
      }
    } catch {
      showToast('Error revoking credential', 'error');
    }
  };

  const handleCreateAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!agentName.trim()) return;

    try {
      const res = await fetch('/api/v1/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ name: agentName.trim(), type: agentType }),
      });
      const data = await res.json();
      if (res.ok) {
        setNewApiKey(data.api_key);
        setAgents([data.agent, ...agents]);
        setSelectedAgent(data.agent);
        setAgentName('');
        showToast('AI Agent created! Please save the API key safely.', 'success');
      } else {
        showToast(data.detail || 'Failed to create agent', 'error');
      }
    } catch {
      showToast('Network error creating agent', 'error');
    }
  };

  const handleDeleteAgent = async (agentId: string) => {
    if (!confirm('Are you sure you want to delete this AI Agent? Its API key will be permanently invalidated.')) {
      return;
    }

    try {
      const res = await fetch(`/api/v1/agents/${agentId}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (res.ok) {
        showToast('Agent deleted', 'success');
        const remaining = agents.filter((a) => a.id !== agentId);
        setAgents(remaining);
        setSelectedAgent(remaining[0] || null);
      }
    } catch {
      showToast('Failed to delete agent', 'error');
    }
  };

  const handleToggleProjectPermission = async (projectId: string, hasPermission: boolean) => {
    if (!selectedAgent) return;

    if (hasPermission) {
      // Revoke permission
      try {
        const res = await fetch(`/api/v1/agents/${selectedAgent.id}/permissions/${projectId}`, {
          method: 'DELETE',
          credentials: 'include',
        });
        if (res.ok) {
          showToast('Project permission revoked', 'success');
          loadPermissions(selectedAgent.id);
        }
      } catch {
        showToast('Failed to revoke permission', 'error');
      }
    } else {
      // Grant permission
      try {
        const res = await fetch(`/api/v1/agents/${selectedAgent.id}/permissions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            agent_id: selectedAgent.id,
            project_id: projectId,
            scopes: ['read', 'write'],
          }),
        });
        if (res.ok) {
          showToast('Project permission granted', 'success');
          loadPermissions(selectedAgent.id);
        }
      } catch {
        showToast('Failed to grant permission', 'error');
      }
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(true);
    setTimeout(() => setCopiedKey(false), 2500);
  };

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-8 animate-in fade-in duration-300">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            <Bot className="w-6 h-6 text-indigo-400" />
            <span>AI Agents & Explicit Project Authorization</span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            AI Agents are separate principals. Agents must be explicitly granted project permissions to query memory or architecture.
          </p>
        </div>

        <button
          type="button"
          onClick={() => {
            setNewApiKey(null);
            setShowCreateModal(true);
          }}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99]"
        >
          <Plus className="w-4 h-4" />
          <span>Register New Agent</span>
        </button>
      </div>

      {toast && (
        <div
          className={`p-4 rounded-xl border flex items-center gap-3 text-sm ${
            toast.type === 'success'
              ? 'bg-emerald-950/40 border-emerald-800/60 text-emerald-300'
              : 'bg-red-950/40 border-red-800/60 text-red-300'
          }`}
        >
          {toast.type === 'success' ? (
            <CheckCircle2 className="w-5 h-5 shrink-0 text-emerald-400" />
          ) : (
            <AlertCircle className="w-5 h-5 shrink-0 text-red-400" />
          )}
          <span>{toast.text}</span>
        </div>
      )}

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Agents List */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-slate-800">
            <h2 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Registered Agents</h2>
            <span className="text-xs text-slate-500 font-mono">{agents.length} Agents</span>
          </div>

          {isLoading ? (
            <div className="py-8 flex justify-center text-slate-500">
              <RefreshCw className="w-5 h-5 animate-spin" />
            </div>
          ) : agents.length === 0 ? (
            <div className="text-center py-8 text-slate-500 text-xs">
              No AI agents registered yet. Click &quot;Register New Agent&quot; to create Claude, Codex, or custom agent principals.
            </div>
          ) : (
            <div className="space-y-2">
              {agents.map((agent) => {
                const isSelected = selectedAgent?.id === agent.id;
                return (
                  <button
                    key={agent.id}
                    type="button"
                    onClick={() => setSelectedAgent(agent)}
                    className={`w-full text-left p-3.5 rounded-xl border transition-all flex items-center justify-between ${
                      isSelected
                        ? 'bg-indigo-950/40 border-indigo-500/60 text-white shadow-sm'
                        : 'bg-slate-950 border-slate-800/80 text-slate-400 hover:text-slate-200 hover:border-slate-700'
                    }`}
                  >
                    <div className="flex items-center gap-3 truncate">
                      <div
                        className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                          isSelected ? 'bg-indigo-500 text-white' : 'bg-slate-900 text-slate-400'
                        }`}
                      >
                        <Bot className="w-4 h-4" />
                      </div>
                      <div className="truncate">
                        <div className="text-sm font-semibold truncate">{agent.name}</div>
                        <div className="text-[11px] text-slate-500 font-mono capitalize">{agent.type}</div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <span className="w-2 h-2 rounded-full bg-emerald-500" />
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Right: Selected Agent Permissions & Details */}
        <div className="lg:col-span-2 space-y-6">
          {selectedAgent ? (
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 space-y-6">
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-xl font-bold text-white">{selectedAgent.name}</h2>
                    <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-400 border border-indigo-500/30">
                      {selectedAgent.type}
                    </span>
                  </div>
                  <div className="text-xs text-slate-500 font-mono mt-1">ID: {selectedAgent.id}</div>
                </div>

                <button
                  type="button"
                  onClick={() => handleDeleteAgent(selectedAgent.id)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold text-red-400 hover:bg-red-950/40 border border-red-900/60 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>Delete Agent</span>
                </button>
              </div>

              {/* Credentials & Key Rotation Matrix (§6) */}
              <div className="border-t border-slate-800 pt-6">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className="text-sm font-bold text-white flex items-center gap-2">
                      <Key className="w-4 h-4 text-indigo-400" />
                      <span>API Credentials & Key Rotation (§6)</span>
                    </h3>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Rotate secret keys without interrupting active sessions. Revoke deprecated keys individually.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setRotatedSecret(null);
                      setNewKeyName('');
                      setShowRotateModal(true);
                    }}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white transition-all shadow-sm"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Rotate / Add Key</span>
                  </button>
                </div>

                {isLoadingCreds ? (
                  <div className="py-4 flex justify-center text-slate-500">
                    <RefreshCw className="w-5 h-5 animate-spin" />
                  </div>
                ) : credentials.length === 0 ? (
                  <div className="p-4 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-500 text-center">
                    No credentials found. Click &quot;Rotate / Add Key&quot; to generate an API key.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {credentials.map((cred) => {
                      const isRevoked = Boolean(cred.revoked_at);
                      return (
                        <div
                          key={cred.id}
                          className="flex items-center justify-between p-3.5 rounded-xl bg-slate-950 border border-slate-800"
                        >
                          <div className="flex items-center gap-3">
                            <Key className={`w-4 h-4 ${isRevoked ? 'text-slate-600' : 'text-emerald-400'}`} />
                            <div>
                              <div className="text-xs font-mono font-semibold text-white flex items-center gap-2">
                                <span>{cred.key_id}</span>
                                <span className="text-[11px] font-sans text-slate-400">({cred.name})</span>
                                {isRevoked ? (
                                  <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-red-950/60 border border-red-800/60 text-red-400">
                                    REVOKED
                                  </span>
                                ) : (
                                  <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/60 text-emerald-400">
                                    ACTIVE
                                  </span>
                                )}
                              </div>
                              <div className="text-[11px] text-slate-500 mt-0.5">
                                Created {new Date(cred.created_at).toLocaleDateString()}
                                {cred.last_used_at && ` · Last used ${new Date(cred.last_used_at).toLocaleDateString()}`}
                              </div>
                            </div>
                          </div>

                          {!isRevoked && (
                            <button
                              type="button"
                              onClick={() => handleRevokeKey(cred.key_id)}
                              className="px-2.5 py-1 rounded-lg text-xs font-semibold text-red-400 hover:bg-red-950/40 border border-red-900/60 transition-colors"
                            >
                              Revoke
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Explicit Project Permissions Matrix */}
              <div className="border-t border-slate-800 pt-6">
                <div className="mb-4">
                  <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    <ShieldCheck className="w-4 h-4 text-indigo-400" />
                    <span>Project Access Grants (§13)</span>
                  </h3>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Toggle which of your projects this agent principal is authorized to query and modify.
                  </p>
                </div>

                {isLoadingPerms ? (
                  <div className="py-6 flex justify-center text-slate-500">
                    <RefreshCw className="w-5 h-5 animate-spin" />
                  </div>
                ) : projects.length === 0 ? (
                  <div className="p-4 rounded-xl bg-slate-950 border border-slate-800 text-xs text-slate-500 text-center">
                    You have no projects created yet. Create a project first to authorize this agent.
                  </div>
                ) : (
                  <div className="space-y-3">
                    {projects.map((proj) => {
                      const perm = permissions.find((p) => p.project_id === proj.id && !p.revoked_at);
                      const hasPerm = Boolean(perm);

                      return (
                        <div
                          key={proj.id}
                          className="flex items-center justify-between p-4 rounded-xl bg-slate-950 border border-slate-800 hover:border-slate-700 transition-colors"
                        >
                          <div className="flex items-center gap-3">
                            <div className="w-9 h-9 rounded-lg bg-slate-900 border border-slate-800 flex items-center justify-center text-indigo-400">
                              <FolderGit2 className="w-4 h-4" />
                            </div>
                            <div>
                              <div className="text-sm font-semibold text-white">{proj.name}</div>
                              <div className="text-xs text-slate-500 font-mono truncate max-w-sm">
                                {proj.local_path}
                              </div>
                            </div>
                          </div>

                          <div className="flex items-center gap-3">
                            {hasPerm && (
                              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/60 text-emerald-400">
                                Scopes: {perm?.scopes.join(', ')}
                              </span>
                            )}

                            <button
                              type="button"
                              onClick={() => handleToggleProjectPermission(proj.id, hasPerm)}
                              className={`px-3.5 py-1.5 rounded-xl text-xs font-semibold transition-all ${
                                hasPerm
                                  ? 'bg-red-950/40 hover:bg-red-900/40 border border-red-800/60 text-red-300'
                                  : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-sm'
                              }`}
                            >
                              {hasPerm ? 'Revoke Access' : 'Authorize Project'}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-12 text-center text-slate-500 text-sm">
              Select an agent from the list or register a new one to manage its explicit project permissions.
            </div>
          )}
        </div>
      </div>

      {/* Modal: Create Agent */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md overflow-hidden shadow-2xl p-6">
            {!newApiKey ? (
              <div>
                <h2 className="text-lg font-bold text-white mb-1">Register New AI Agent</h2>
                <p className="text-xs text-slate-400 mb-5">
                  Create an isolated agent principal with its own secret API key.
                </p>

                <form onSubmit={handleCreateAgent} className="space-y-4">
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      Agent Name
                    </label>
                    <input
                      type="text"
                      required
                      value={agentName}
                      onChange={(e) => setAgentName(e.target.value)}
                      placeholder="e.g. Claude Code Architect"
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      Agent Type / Model Provider
                    </label>
                    <select
                      value={agentType}
                      onChange={(e) => setAgentType(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    >
                      <option value="claude">Claude (Anthropic)</option>
                      <option value="codex">Codex / OpenAI</option>
                      <option value="gemini">Gemini (Google)</option>
                      <option value="custom">Custom Agent / Stdio Bridge</option>
                    </select>
                  </div>

                  <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-800 mt-6">
                    <button
                      type="button"
                      onClick={() => setShowCreateModal(false)}
                      className="px-4 py-2 rounded-xl text-sm font-medium text-slate-400 hover:text-white transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      className="px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99]"
                    >
                      Generate Agent Key
                    </button>
                  </div>
                </form>
              </div>
            ) : (
              <div>
                <div className="w-12 h-12 rounded-2xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 mb-4">
                  <Key className="w-6 h-6" />
                </div>
                <h2 className="text-lg font-bold text-white mb-1">Agent Created Successfully</h2>
                <p className="text-xs text-slate-400 mb-4">
                  Copy this API key now. It will <span className="text-white font-semibold">never</span> be displayed again.
                </p>

                <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 font-mono text-xs text-indigo-300 break-all select-all mb-4 flex items-center justify-between gap-2">
                  <span>{newApiKey}</span>
                  <button
                    type="button"
                    onClick={() => copyToClipboard(newApiKey)}
                    className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-white shrink-0 transition-colors"
                    title="Copy API Key"
                  >
                    {copiedKey ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                  </button>
                </div>

                <div className="p-3 rounded-xl bg-indigo-950/40 border border-indigo-800/60 text-indigo-300 text-xs mb-6">
                  Use this key in your agent via header <code className="text-white font-mono">Authorization: Bearer cortex_agent_...</code> or environment variable <code className="text-white font-mono">CORTEX_AGENT_KEY</code>.
                </div>

                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="w-full py-2.5 px-4 rounded-xl bg-slate-800 hover:bg-slate-700 text-white font-semibold text-xs transition-colors"
                >
                  Done
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Modal: Rotate Key */}
      {showRotateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md overflow-hidden shadow-2xl p-6">
            {!rotatedSecret ? (
              <div>
                <h2 className="text-lg font-bold text-white mb-1">Rotate Agent Credential</h2>
                <p className="text-xs text-slate-400 mb-5">
                  Generate a new API key for <span className="text-white font-semibold">{selectedAgent?.name}</span>. Existing keys remain valid until revoked.
                </p>

                <form onSubmit={handleRotateKey} className="space-y-4">
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      Key Label / Purpose
                    </label>
                    <input
                      type="text"
                      value={newKeyName}
                      onChange={(e) => setNewKeyName(e.target.value)}
                      placeholder="e.g. production-runner, dev-test"
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-800 mt-6">
                    <button
                      type="button"
                      onClick={() => setShowRotateModal(false)}
                      className="px-4 py-2 rounded-xl text-sm font-medium text-slate-400 hover:text-white transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      className="px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99]"
                    >
                      Generate Key
                    </button>
                  </div>
                </form>
              </div>
            ) : (
              <div>
                <div className="w-12 h-12 rounded-2xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 mb-4">
                  <Key className="w-6 h-6" />
                </div>
                <h2 className="text-lg font-bold text-white mb-1">New Credential Generated</h2>
                <p className="text-xs text-slate-400 mb-4">
                  Copy this secret key immediately. It will <span className="text-white font-semibold">never</span> be displayed again.
                </p>

                <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 font-mono text-xs text-indigo-300 break-all select-all mb-4 flex items-center justify-between gap-2">
                  <span>{rotatedSecret}</span>
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard.writeText(rotatedSecret);
                      setCopiedRotatedKey(true);
                      setTimeout(() => setCopiedRotatedKey(false), 2500);
                    }}
                    className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-white shrink-0 transition-colors"
                    title="Copy Secret"
                  >
                    {copiedRotatedKey ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                  </button>
                </div>

                <button
                  type="button"
                  onClick={() => setShowRotateModal(false)}
                  className="w-full py-2.5 px-4 rounded-xl bg-slate-800 hover:bg-slate-700 text-white font-semibold text-xs transition-colors"
                >
                  Done
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
