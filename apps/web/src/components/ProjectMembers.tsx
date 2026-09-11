import React, { useState, useEffect } from 'react';
import { ProjectMembership } from '../types';
import {
  fetchProjectMembers,
  addProjectMember,
  updateProjectMemberRole,
  removeProjectMember,
} from '../api';
import {
  Users,
  UserPlus,
  Shield,
  ShieldAlert,
  Trash2,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react';

interface ProjectMembersProps {
  projectId: string;
  projectName: string;
}

const ROLES = ['OWNER', 'ADMIN', 'MEMBER', 'VIEWER'] as const;

export const ProjectMembers: React.FC<ProjectMembersProps> = ({ projectId, projectName }) => {
  const [members, setMembers] = useState<ProjectMembership[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [newUserId, setNewUserId] = useState('');
  const [newRole, setNewRole] = useState<'OWNER' | 'ADMIN' | 'MEMBER' | 'VIEWER'>('MEMBER');
  const [toast, setToast] = useState<{ text: string; type: 'success' | 'error' } | null>(null);

  const showToast = (text: string, type: 'success' | 'error' = 'success') => {
    setToast({ text, type });
    setTimeout(() => setToast(null), 4000);
  };

  const loadMembers = async () => {
    setIsLoading(true);
    try {
      const data = await fetchProjectMembers(projectId);
      setMembers(data);
    } catch {
      showToast('Failed to load project members', 'error');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (projectId) {
      loadMembers();
    }
  }, [projectId]);

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newUserId.trim()) return;

    try {
      const created = await addProjectMember(projectId, newUserId.trim(), newRole);
      if (created) {
        showToast('Member added successfully', 'success');
        setShowAddModal(false);
        setNewUserId('');
        loadMembers();
      } else {
        showToast('Failed to add member. Check user ID and permissions.', 'error');
      }
    } catch {
      showToast('Error adding member', 'error');
    }
  };

  const handleRoleChange = async (userId: string, role: string) => {
    try {
      const updated = await updateProjectMemberRole(projectId, userId, role);
      if (updated) {
        showToast(`Role updated to ${role}`, 'success');
        loadMembers();
      } else {
        showToast('Failed to update member role', 'error');
      }
    } catch {
      showToast('Error updating role', 'error');
    }
  };

  const handleRemoveMember = async (userId: string) => {
    if (!confirm('Are you sure you want to revoke this user\'s access to this project?')) {
      return;
    }

    try {
      const ok = await removeProjectMember(projectId, userId);
      if (ok) {
        showToast('Member removed', 'success');
        loadMembers();
      } else {
        showToast('Failed to remove member. Cannot remove sole OWNER.', 'error');
      }
    } catch {
      showToast('Error removing member', 'error');
    }
  };

  const getRoleBadgeClass = (role: string) => {
    switch (role) {
      case 'OWNER':
        return 'bg-purple-950/60 border-purple-800/60 text-purple-300';
      case 'ADMIN':
        return 'bg-indigo-950/60 border-indigo-800/60 text-indigo-300';
      case 'MEMBER':
        return 'bg-emerald-950/60 border-emerald-800/60 text-emerald-300';
      case 'VIEWER':
      default:
        return 'bg-slate-800/60 border-slate-700/60 text-slate-400';
    }
  };

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6 animate-in fade-in duration-300">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2.5">
            <Users className="w-6 h-6 text-indigo-400" />
            <span>Project Memberships</span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Manage authorized collaborators and role-based permissions for <span className="text-slate-200 font-semibold">{projectName}</span>.
          </p>
        </div>

        <button
          type="button"
          onClick={() => setShowAddModal(true)}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99]"
        >
          <UserPlus className="w-4 h-4" />
          <span>Add Member</span>
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

      {/* Role Guide Banner */}
      <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 grid grid-cols-1 sm:grid-cols-4 gap-3 text-xs">
        <div className="flex items-start gap-2">
          <Shield className="w-4 h-4 text-purple-400 shrink-0 mt-0.5" />
          <div>
            <span className="font-bold text-purple-300">OWNER</span>
            <p className="text-slate-400 text-[11px] mt-0.5">Full control, project deletion, member role assignment.</p>
          </div>
        </div>
        <div className="flex items-start gap-2">
          <Shield className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" />
          <div>
            <span className="font-bold text-indigo-300">ADMIN</span>
            <p className="text-slate-400 text-[11px] mt-0.5">Scans, memory management, adding members.</p>
          </div>
        </div>
        <div className="flex items-start gap-2">
          <Shield className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
          <div>
            <span className="font-bold text-emerald-300">MEMBER</span>
            <p className="text-slate-400 text-[11px] mt-0.5">Read memories, query graph, create claims.</p>
          </div>
        </div>
        <div className="flex items-start gap-2">
          <ShieldAlert className="w-4 h-4 text-slate-400 shrink-0 mt-0.5" />
          <div>
            <span className="font-bold text-slate-300">VIEWER</span>
            <p className="text-slate-400 text-[11px] mt-0.5">Read-only project and memory access.</p>
          </div>
        </div>
      </div>

      {/* Members Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-sm">
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <h2 className="text-xs font-bold text-slate-300 uppercase tracking-wider">Project Access Roster</h2>
          <span className="text-xs text-slate-500 font-mono">{members.length} Members</span>
        </div>

        {isLoading ? (
          <div className="py-12 flex justify-center text-slate-500">
            <RefreshCw className="w-6 h-6 animate-spin" />
          </div>
        ) : members.length === 0 ? (
          <div className="p-12 text-center text-slate-500 text-sm">
            No explicit memberships found. Project owner has implicit root access.
          </div>
        ) : (
          <div className="divide-y divide-slate-800">
            {members.map((member) => (
              <div
                key={member.id}
                className="p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 hover:bg-slate-950/40 transition-colors"
              >
                <div className="flex items-center gap-3.5">
                  <div className="w-10 h-10 rounded-xl bg-slate-800 border border-slate-700/60 flex items-center justify-center text-slate-300 font-mono font-bold text-sm">
                    {member.user_id.slice(0, 2).toUpperCase()}
                  </div>
                  <div>
                    <div className="text-sm font-semibold text-white font-mono">{member.user_id}</div>
                    <div className="text-xs text-slate-500 flex items-center gap-2 mt-0.5">
                      <span>Joined {new Date(member.created_at).toLocaleDateString()}</span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <span
                    className={`text-[10px] font-mono px-2.5 py-1 rounded border uppercase font-semibold ${getRoleBadgeClass(
                      member.role
                    )}`}
                  >
                    {member.role}
                  </span>

                  <select
                    value={member.role}
                    onChange={(e) => handleRoleChange(member.user_id, e.target.value)}
                    className="bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-300 focus:outline-none focus:border-indigo-500"
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>

                  <button
                    type="button"
                    onClick={() => handleRemoveMember(member.user_id)}
                    className="p-2 rounded-lg text-slate-400 hover:text-red-400 hover:bg-red-950/30 transition-colors"
                    title="Remove Member"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add Member Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md overflow-hidden shadow-2xl p-6">
            <h2 className="text-lg font-bold text-white mb-1">Add Project Member</h2>
            <p className="text-xs text-slate-400 mb-5">
              Grant a registered user access to this project with a specific role.
            </p>

            <form onSubmit={handleAddMember} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                  User ID (UUID)
                </label>
                <input
                  type="text"
                  required
                  value={newUserId}
                  onChange={(e) => setNewUserId(e.target.value)}
                  placeholder="e.g. 550e8400-e29b-41d4-a716-446655440000"
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                  Role
                </label>
                <select
                  value={newRole}
                  onChange={(e) => setNewRole(e.target.value as any)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500"
                >
                  <option value="MEMBER">MEMBER (Standard read/write memory access)</option>
                  <option value="ADMIN">ADMIN (Scan, rule management, invite)</option>
                  <option value="VIEWER">VIEWER (Read-only access)</option>
                  <option value="OWNER">OWNER (Full control)</option>
                </select>
              </div>

              <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-800 mt-6">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="px-4 py-2 rounded-xl text-sm font-medium text-slate-400 hover:text-white transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99]"
                >
                  Add Member
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
