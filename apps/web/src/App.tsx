import React, { useState, useEffect } from 'react';
import { Navigation, TabType } from './components/Navigation';
import { Overview } from './components/Overview';
import { ArchitectureGraph } from './components/ArchitectureGraph';
import { MemoryExplorer } from './components/MemoryExplorer';
import { DecisionsAndFailures } from './components/DecisionsAndFailures';
import { ChangeImpact } from './components/ChangeImpact';
import { TokenEconomics } from './components/TokenEconomics';
import { EvaluationHarness } from './components/EvaluationHarness';
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
} from 'lucide-react';

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabType>('overview');
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [architecture, setArchitecture] = useState<ArchitectureResponse | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [isLoadingMemories, setIsLoadingMemories] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'info' | 'error' } | null>(null);

  const showToast = (message: string, type: 'success' | 'info' | 'error' = 'info') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  // Initial load
  useEffect(() => {
    async function loadInitial() {
      const projectList = await fetchProjects();
      setProjects(projectList);
      if (projectList.length > 0) {
        const defaultProj = projectList.find((p) => p.name.toLowerCase().includes('vivek')) || projectList[0];
        setSelectedProjectId(defaultProj.id);
      }
    }
    loadInitial();
  }, []);

  // When project changes, load architecture and memories
  useEffect(() => {
    if (!selectedProjectId) return;

    async function loadProjectDetails() {
      setIsLoadingMemories(true);
      const [arch, mems] = await Promise.all([
        fetchArchitecture(selectedProjectId!),
        fetchMemories(selectedProjectId!),
      ]);
      setArchitecture(arch);
      setMemories(mems);
      setIsLoadingMemories(false);
    }

    loadProjectDetails();
  }, [selectedProjectId]);

  const selectedProject = projects.find((p) => p.id === selectedProjectId) || null;

  const handleTriggerScan = async () => {
    if (!selectedProjectId) return;
    setIsScanning(true);
    showToast('Repository AST scan initiated...', 'info');

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
    } else {
      showToast('Scan failed. Ensure directory is accessible.', 'error');
    }
    setIsScanning(false);
  };

  const handleConsolidate = async () => {
    if (!selectedProjectId) return;
    showToast('Consolidating episodic memories into durable lessons...', 'info');

    const res = await triggerConsolidate(selectedProjectId);
    if (res) {
      showToast(
        `Consolidated: ${res.clustered_count || 0} episodes merged into ${res.lessons_created || 0} lessons`,
        'success'
      );
      const mems = await fetchMemories(selectedProjectId);
      setMemories(mems);
    } else {
      showToast('Consolidation encountered an issue', 'error');
    }
  };

  const handleRefreshMemories = async () => {
    if (!selectedProjectId) return;
    setIsLoadingMemories(true);
    const mems = await fetchMemories(selectedProjectId);
    setMemories(mems);
    setIsLoadingMemories(false);
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

      {/* Left Navigation Sidebar */}
      <Navigation activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* Main Content Workspace */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top Header Bar */}
        <header className="h-16 border-b border-slate-800 bg-slate-900/60 backdrop-blur px-6 flex items-center justify-between shrink-0">
          {/* Project Selector */}
          <div className="flex items-center gap-3">
            <FolderGit2 className="w-4 h-4 text-brand-400" />
            <span className="text-xs font-mono text-slate-400">Project:</span>
            <select
              value={selectedProjectId || ''}
              onChange={(e) => setSelectedProjectId(e.target.value)}
              className="bg-slate-950 border border-slate-700 rounded-lg px-3 py-1.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-brand-500 transition cursor-pointer"
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
          </div>

          {/* Action Buttons */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleConsolidate}
              disabled={!selectedProjectId}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-purple-300 rounded-lg text-xs font-mono font-medium border border-slate-700 transition disabled:opacity-40"
              title="Cluster episodic failures into durable rules"
            >
              <Sparkles className="w-3.5 h-3.5 text-purple-400" />
              Consolidate
            </button>

            <button
              onClick={handleTriggerScan}
              disabled={isScanning || !selectedProjectId}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-600 hover:bg-brand-500 text-white rounded-lg text-xs font-mono font-semibold shadow-sm transition disabled:opacity-40"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isScanning ? 'animate-spin' : ''}`} />
              {isScanning ? 'Scanning...' : 'Scan Repo'}
            </button>
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

            {activeTab === 'economics' && <TokenEconomics projectId={selectedProjectId} />}

            {activeTab === 'evaluation' && <EvaluationHarness projectId={selectedProjectId} />}
          </div>
        </main>
      </div>
    </div>
  );
};

export default App;
