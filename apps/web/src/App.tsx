import React, { useState, useEffect, useRef } from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import { LoginPage } from './components/LoginPage';
import { NewProjectModal } from './components/NewProjectModal';
import { AccountSettings } from './components/AccountSettings';
import { AgentsManager } from './components/AgentsManager';
import { Navigation, TabType } from './components/Navigation';
import { Overview } from './components/Overview';
import { ArchitectureGraph } from './components/ArchitectureGraph';
import { MemoryExplorer } from './components/MemoryExplorer';
import { DecisionsAndFailures } from './components/DecisionsAndFailures';
import { ChangeImpact } from './components/ChangeImpact';
import { TokenEconomics } from './components/TokenEconomics';
import { EvaluationHarness } from './components/EvaluationHarness';
import { ArchitectureInvariants } from './components/ArchitectureInvariants';
import { CognitiveSnapshots } from './components/CognitiveSnapshots';
import { ProjectMembers } from './components/ProjectMembers';

import {
  fetchProjects,
  fetchArchitecture,
  fetchMemories,
  triggerScan,
  triggerConsolidate,
} from './api';
import { ArchitectureResponse, Memory, Project } from './types';
import {
  FolderGit2,
  RefreshCw,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  X,
  Plus,
  ChevronDown,
  LogOut,
  Settings,
  Bot,
  Brain,
  ShieldCheck,
} from 'lucide-react';

const AppDashboard: React.FC = () => {
  const { user, logout } = useAuth();

  const [activeTab, setActiveTab] = useState<TabType>('overview');
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [architecture, setArchitecture] = useState<ArchitectureResponse | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [isLoadingMemories, setIsLoadingMemories] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'info' | 'error' } | null>(null);

  // New Project Modal & User Dropdown state
  const [isNewProjectOpen, setIsNewProjectOpen] = useState(false);
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

  const showToast = (message: string, type: 'success' | 'info' | 'error' = 'info') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  // Close user dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setIsUserMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Initial load of authenticated user's projects
  const loadProjects = async () => {
    try {
      const projectList = await fetchProjects();
      setProjects(projectList);
      if (projectList.length > 0 && (!selectedProjectId || !projectList.some((p) => p.id === selectedProjectId))) {
        setSelectedProjectId(projectList[0].id);
      }
    } catch (err: any) {
      showToast(err?.message || 'Failed to load projects.', 'error');
    }
  };

  useEffect(() => {
    loadProjects();
  }, []);

  // When project changes, load architecture and memories
  useEffect(() => {
    if (!selectedProjectId) {
      setArchitecture(null);
      setMemories([]);
      return;
    }

    async function loadProjectDetails() {
      setIsLoadingMemories(true);
      try {
        const [arch, mems] = await Promise.all([
          fetchArchitecture(selectedProjectId!),
          fetchMemories(selectedProjectId!),
        ]);
        setArchitecture(arch);
        setMemories(mems);
      } catch (err: any) {
        showToast(err?.message || 'Failed to load project details.', 'error');
      } finally {
        setIsLoadingMemories(false);
      }
    }

    loadProjectDetails();
  }, [selectedProjectId]);

  const selectedProject = projects.find((p) => p.id === selectedProjectId) || null;

  const handleTriggerScan = async () => {
    if (!selectedProjectId) return;
    setIsScanning(true);
    showToast('Repository AST scan initiated...', 'info');

    try {
      const ok = await triggerScan(selectedProjectId);
      if (ok) {
        showToast('Scan complete: code graph and entities synchronized', 'success');
        const [arch, mems, updatedProjects] = await Promise.all([
          fetchArchitecture(selectedProjectId),
          fetchMemories(selectedProjectId),
          fetchProjects(),
        ]);
        setArchitecture(arch);
        setMemories(mems);
        setProjects(updatedProjects);
      }
    } catch (err: any) {
      showToast(err?.message || 'Scan failed. Ensure directory is accessible.', 'error');
    } finally {
      setIsScanning(false);
    }
  };

  const handleConsolidate = async () => {
    if (!selectedProjectId) return;
    showToast('Consolidating episodic memories into durable lessons...', 'info');

    try {
      const res = await triggerConsolidate(selectedProjectId);
      if (res) {
        showToast(
          `Consolidated: ${res.clustered_count || 0} episodes merged into ${res.lessons_created || 0} lessons`,
          'success'
        );
        const mems = await fetchMemories(selectedProjectId);
        setMemories(mems);
      }
    } catch (err: any) {
      showToast(err?.message || 'Consolidation encountered an issue', 'error');
    }
  };

  const handleRefreshMemories = async () => {
    if (!selectedProjectId) return;
    setIsLoadingMemories(true);
    try {
      const mems = await fetchMemories(selectedProjectId);
      setMemories(mems);
    } catch (err: any) {
      showToast(err?.message || 'Failed to refresh memories.', 'error');
    } finally {
      setIsLoadingMemories(false);
    }
  };

  const handleProjectCreated = (newProj: Project) => {
    setProjects((prev) => [newProj, ...prev]);
    setSelectedProjectId(newProj.id);
    showToast(`Project '${newProj.name}' created and initial scan queued.`, 'success');
  };

  return (
    <div className="flex h-screen bg-slate-950 text-slate-100 font-sans overflow-hidden">
      {/* Toast Notification */}
      {toast && (
        <div
          className={`fixed bottom-5 right-5 z-50 flex items-center gap-2.5 px-4 py-3 rounded-xl shadow-2xl border text-xs font-mono transition-all animate-bounce ${
            toast.type === 'success'
              ? 'bg-emerald-950 text-emerald-300 border-emerald-700'
              : toast.type === 'error'
              ? 'bg-rose-950 text-rose-300 border-rose-700'
              : 'bg-slate-900 text-slate-200 border-slate-700'
          }`}
        >
          {toast.type === 'success' && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
          {toast.type === 'error' && <AlertCircle className="w-4 h-4 text-rose-400" />}
          <span>{toast.message}</span>
          <button onClick={() => setToast(null)} className="ml-2 hover:opacity-75">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* New Project Modal */}
      <NewProjectModal
        isOpen={isNewProjectOpen}
        onClose={() => setIsNewProjectOpen(false)}
        onProjectCreated={handleProjectCreated}
      />

      {/* Left Navigation Sidebar */}
      <Navigation activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* Main Content Workspace */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top Header Bar */}
        <header className="h-16 border-b border-slate-800 bg-slate-900/60 backdrop-blur px-6 flex items-center justify-between shrink-0">
          {/* Project Selector & + New Project Button */}
          <div className="flex items-center gap-3">
            <FolderGit2 className="w-4 h-4 text-brand-400" />
            <span className="text-xs font-mono text-slate-400">Project:</span>
            <select
              value={selectedProjectId || ''}
              onChange={(e) => setSelectedProjectId(e.target.value)}
              className="bg-slate-950 border border-slate-700 rounded-lg px-3 py-1.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-brand-500 transition cursor-pointer max-w-xs"
            >
              {projects.length === 0 ? (
                <option value="">No projects registered</option>
              ) : (
                projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.language || 'polyglot'})
                  </option>
                ))
              )}
            </select>

            <button
              type="button"
              onClick={() => setIsNewProjectOpen(true)}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-500/40 text-indigo-300 font-mono text-xs transition-colors"
              title="Add a new repository to your CortexForge"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>New Project</span>
            </button>
          </div>

          {/* Action Buttons & User Profile Menu */}
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <button
                onClick={handleConsolidate}
                disabled={!selectedProjectId}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-purple-300 rounded-lg text-xs font-mono font-medium border border-slate-700 transition disabled:opacity-40"
                title="Cluster episodic failures into durable rules"
              >
                <Sparkles className="w-3.5 h-3.5 text-purple-400" />
                <span>Consolidate</span>
              </button>

              <button
                onClick={handleTriggerScan}
                disabled={isScanning || !selectedProjectId}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-600 hover:bg-brand-500 text-white rounded-lg text-xs font-mono font-semibold shadow-sm transition disabled:opacity-40"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isScanning ? 'animate-spin' : ''}`} />
                <span>{isScanning ? 'Scanning...' : 'Scan Repo'}</span>
              </button>
            </div>

            {/* User Profile Dropdown (§14) */}
            <div className="relative" ref={userMenuRef}>
              <button
                type="button"
                onClick={() => setIsUserMenuOpen(!isUserMenuOpen)}
                className="flex items-center gap-2.5 pl-3 pr-2 py-1.5 rounded-xl bg-slate-950 hover:bg-slate-800 border border-slate-800 transition-colors"
              >
                <div className="w-6 h-6 rounded-lg bg-indigo-500/20 border border-indigo-500/40 flex items-center justify-center text-indigo-400 font-bold text-xs">
                  {user?.display_name ? user.display_name[0].toUpperCase() : 'U'}
                </div>
                <span className="text-xs font-medium text-slate-200 truncate max-w-[120px]">
                  {user?.display_name || user?.email?.split('@')[0] || 'User Profile'}
                </span>
                <ChevronDown className="w-3.5 h-3.5 text-slate-400" />
              </button>

              {isUserMenuOpen && (
                <div className="absolute right-0 mt-2 w-64 bg-slate-900 border border-slate-800 rounded-2xl p-2 shadow-2xl z-50 animate-in fade-in slide-in-from-top-2 duration-150">
                  <div className="p-3 border-b border-slate-800/80 mb-1">
                    <div className="text-xs font-bold text-white truncate">{user?.display_name || 'Individual Developer'}</div>
                    <div className="text-[11px] text-slate-400 font-mono truncate">{user?.email}</div>
                    {user?.email_verified_at && (
                      <div className="inline-flex items-center gap-1 text-[10px] text-emerald-400 font-medium mt-1">
                        <ShieldCheck className="w-3 h-3" />
                        <span>Verified Account</span>
                      </div>
                    )}
                  </div>

                  <button
                    type="button"
                    onClick={() => {
                      setActiveTab('settings');
                      setIsUserMenuOpen(false);
                    }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-800 transition-colors text-left"
                  >
                    <Settings className="w-4 h-4 text-slate-400" />
                    <span>Account & Security</span>
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setActiveTab('agents');
                      setIsUserMenuOpen(false);
                    }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-800 transition-colors text-left"
                  >
                    <Bot className="w-4 h-4 text-slate-400" />
                    <span>AI Agents Management</span>
                  </button>

                  <div className="border-t border-slate-800/80 my-1" />

                  <button
                    type="button"
                    onClick={() => {
                      setIsUserMenuOpen(false);
                      logout();
                    }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-semibold text-red-400 hover:text-red-300 hover:bg-red-950/40 transition-colors text-left"
                  >
                    <LogOut className="w-4 h-4 text-red-400" />
                    <span>Sign Out</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        {/* Scrollable Viewport */}
        <main className="flex-1 overflow-y-auto p-6">
          <div className="max-w-7xl mx-auto">
            {activeTab === 'overview' && (
              <Overview
                project={selectedProject}
                arch={architecture}
                onTriggerScan={handleTriggerScan}
                onConsolidate={handleConsolidate}
                isScanning={isScanning}
              />
            )}

            {activeTab === 'architecture' && (
              <ArchitectureGraph arch={architecture} />
            )}

            {activeTab === 'invariants' && (
              <ArchitectureInvariants projectId={selectedProjectId} />
            )}

            {activeTab === 'memories' && (
              <MemoryExplorer
                memories={memories}
                onRefresh={handleRefreshMemories}
                isLoading={isLoadingMemories}
              />
            )}

            {activeTab === 'decisions' && (
              <DecisionsAndFailures
                memories={memories}
                onRefresh={handleRefreshMemories}
              />
            )}

            {activeTab === 'failures' && (
              <DecisionsAndFailures
                memories={memories}
                onRefresh={handleRefreshMemories}
              />
            )}

            {activeTab === 'impact' && (
              <ChangeImpact projectId={selectedProjectId || ''} arch={architecture} />
            )}

            {activeTab === 'snapshots' && (
              <CognitiveSnapshots projectId={selectedProjectId} />
            )}

            {activeTab === 'economics' && <TokenEconomics projectId={selectedProjectId} />}

            {activeTab === 'evaluation' && <EvaluationHarness projectId={selectedProjectId} />}

            {activeTab === 'agents' && <AgentsManager projects={projects} />}

            {activeTab === 'members' && (
              <ProjectMembers
                projectId={selectedProjectId || ''}
                projectName={selectedProject?.name || 'Current Project'}
              />
            )}

            {activeTab === 'settings' && <AccountSettings />}
          </div>
        </main>
      </div>
    </div>
  );
};

const AppRoot: React.FC = () => {
  const { authStatus } = useAuth();

  if (authStatus === 'loading') {
    return (
      <div className="min-h-screen w-full bg-slate-950 flex flex-col items-center justify-center text-slate-100">
        <div className="relative mb-4">
          <div className="w-16 h-16 rounded-2xl bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center text-indigo-400 animate-pulse">
            <Brain className="w-9 h-9" />
          </div>
          <span className="w-3 h-3 rounded-full bg-indigo-500 absolute -top-1 -right-1 animate-ping" />
        </div>
        <div className="text-sm font-semibold text-white tracking-wide">Initializing CortexForge Brain...</div>
        <div className="text-xs text-slate-500 font-mono mt-1">Verifying individual user session</div>
      </div>
    );
  }

  if (authStatus === 'unauthenticated') {
    return <LoginPage />;
  }

  return <AppDashboard />;
};

export const App: React.FC = () => {
  return (
    <AuthProvider>
      <AppRoot />
    </AuthProvider>
  );
};

export default App;
