import React, { useState, useEffect, useRef } from 'react';
import {
  FolderGit2,
  Folder,
  FolderOpen,
  Github,
  Globe,
  X,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  Search,
  ShieldCheck,
  Layers,
  ArrowRight,
  Lock,
} from 'lucide-react';
import { Project } from '../types';
import { useAuth } from '../context/AuthContext';

interface NewProjectModalProps {
  isOpen: boolean;
  onClose: () => void;
  onProjectCreated: (project: Project) => void;
}

type ProjectSourceType = 'LOCAL' | 'GITHUB' | 'GIT_URL';

interface LocalValidationResult {
  valid: boolean;
  is_git: boolean;
  path: string;
  default_branch?: string;
  detected_language?: string;
  languages?: Record<string, number>;
  error?: string;
}

interface DirectoryEntry {
  name: string;
  path: string;
  is_dir: boolean;
  is_git: boolean;
}

interface DirectoryBrowseResponse {
  current_path: string;
  parent_path: string | null;
  workspace_root: string;
  directories: DirectoryEntry[];
  is_windows?: boolean;
  is_drive_root?: boolean;
}

interface GitHubRepoItem {
  id: string;
  name: string;
  full_name: string;
  owner: string;
  default_branch: string;
  description?: string;
  private: boolean;
  clone_url: string;
  language?: string;
}

interface JobStatusData {
  id: string;
  status: 'PENDING' | 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED';
  progress?: number;
  result?: {
    files_scanned?: number;
    entities_found?: number;
    entities_extracted?: number;
    relationships_built?: number;
    relationships_extracted?: number;
    memories_created?: number;
    [key: string]: any;
  };
  error?: string;
}

export const NewProjectModal: React.FC<NewProjectModalProps> = ({
  isOpen,
  onClose,
  onProjectCreated,
}) => {
  const { user, githubConnected } = useAuth();

  const [sourceType, setSourceType] = useState<ProjectSourceType>('LOCAL');
  const [name, setName] = useState('');
  const [defaultBranch, setDefaultBranch] = useState('main');
  const [language, setLanguage] = useState('');

  // Local Repo State
  const [localPath, setLocalPath] = useState('');
  const [isValidatingPath, setIsValidatingPath] = useState(false);
  const [validationResult, setValidationResult] = useState<LocalValidationResult | null>(null);
  const [showFolderBrowser, setShowFolderBrowser] = useState(false);
  const [browseData, setBrowseData] = useState<DirectoryBrowseResponse | null>(null);
  const [isLoadingDirectories, setIsLoadingDirectories] = useState(false);
  // Existing project detected at path
  const [existingProject, setExistingProject] = useState<{ id: string; name: string } | null>(null);

  // GitHub Flow State
  const [ghRepos, setGhRepos] = useState<GitHubRepoItem[]>([]);
  const [isLoadingGhRepos, setIsLoadingGhRepos] = useState(false);
  const [ghSearchQuery, setGhSearchQuery] = useState('');
  const [selectedGhRepo, setSelectedGhRepo] = useState<GitHubRepoItem | null>(null);
  const [ghBranches, setGhBranches] = useState<string[]>([]);
  const [isLoadingBranches, setIsLoadingBranches] = useState(false);
  const [customGhRepo, setCustomGhRepo] = useState('');

  // Git URL Flow State
  const [cloneUrl, setCloneUrl] = useState('');
  const [authType, setAuthType] = useState<'PUBLIC' | 'TOKEN'>('PUBLIC');
  const [gitToken, setGitToken] = useState('');

  // Form Submission & Background Job State
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [createdProject, setCreatedProject] = useState<Project | null>(null);
  const [jobData, setJobData] = useState<JobStatusData | null>(null);
  const isJobSuccess = jobData?.status === 'COMPLETED' || jobData?.status === 'SUCCEEDED';

  const pollIntervalRef = useRef<any>(null);

  // Fetch GitHub repos if connected and GitHub tab is selected
  useEffect(() => {
    if (isOpen && sourceType === 'GITHUB' && (githubConnected || user?.github_login)) {
      fetchGitHubRepositories();
    }
  }, [isOpen, sourceType, githubConnected, user?.github_login]);

  // Reset modal state on open
  useEffect(() => {
    if (isOpen) {
      setSourceType('LOCAL');
      setName('');
      setLocalPath('');
      setValidationResult(null);
      setExistingProject(null);
      setSelectedGhRepo(null);
      setCloneUrl('');
      setFormError(null);
      setActiveJobId(null);
      setCreatedProject(null);
      setJobData(null);
      setShowFolderBrowser(false);
      setBrowseData(null);
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    } else {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    }
  }, [isOpen]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, []);

  if (!isOpen) return null;

  // Validate local path with backend
  const handleValidateLocalPath = async (pathToTest?: string) => {
    const targetPath = (pathToTest !== undefined ? pathToTest : localPath).trim();
    if (!targetPath) return;

    setIsValidatingPath(true);
    setFormError(null);
    setExistingProject(null);
    try {
      const res = await fetch('/api/v1/projects/validate-local', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ local_path: targetPath }),
      });
      const data: LocalValidationResult & {
        existing_project_id?: string;
        existing_project_name?: string;
      } = await res.json();
      setValidationResult(data);

      // Existing project at this path — surface inline info, not a form error
      if (data.existing_project_id) {
        setExistingProject({ id: data.existing_project_id, name: data.existing_project_name || 'Existing Project' });
        return;
      }

      if (data.valid) {
        // If user entered relative path (e.g. "." or "./"), update input with full canonical path
        if (
          data.path &&
          (targetPath === '.' ||
            targetPath === '..' ||
            targetPath.startsWith('./') ||
            targetPath.startsWith('.\\') ||
            (!targetPath.includes('/') && !targetPath.includes('\\')))
        ) {
          setLocalPath(data.path);
        }
        if (data.default_branch && (!defaultBranch || defaultBranch === 'main')) {
          setDefaultBranch(data.default_branch);
        }
        if (data.detected_language && !language) {
          setLanguage(data.detected_language);
        }
        const effectivePath = data.path || targetPath;
        if (!name.trim() || name === '.') {
          const folder = effectivePath.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
          if (folder && folder !== '.') setName(folder);
        }
      } else if (data.error) {
        setFormError(data.error);
      }
    } catch {
      setFormError('Failed to validate local repository path.');
    } finally {
      setIsValidatingPath(false);
    }
  };

  const fetchDirectories = async (targetPath?: string | null) => {
    setIsLoadingDirectories(true);
    try {
      const url = targetPath
        ? `/api/v1/projects/browse-directories?path=${encodeURIComponent(targetPath)}`
        : '/api/v1/projects/browse-directories';
      const res = await fetch(url, { credentials: 'include' });
      if (res.ok) {
        const data: DirectoryBrowseResponse = await res.json();
        setBrowseData(data);
      }
    } catch (err) {
      console.error('Failed to browse directories', err);
    } finally {
      setIsLoadingDirectories(false);
    }
  };

  // Open the in-browser folder explorer directly (no native OS picker attempt)
  const handleOpenFolderBrowser = () => {
    setShowFolderBrowser(true);
    if (!browseData) fetchDirectories(localPath || null);
  };

  const handleSelectDirectory = (dirPath: string, dirName: string) => {
    setLocalPath(dirPath);
    if (!name.trim() || name === '.') {
      const folderName = dirName || dirPath.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
      if (folderName && folderName !== '.') {
        setName(folderName);
      }
    }
    handleValidateLocalPath(dirPath);
    setShowFolderBrowser(false);
  };

  const fetchGitHubRepositories = async () => {
    setIsLoadingGhRepos(true);
    try {
      const res = await fetch('/api/v1/github/repositories', {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        if (data.repositories) {
          setGhRepos(data.repositories);
        }
      }
    } catch (err) {
      console.warn('Could not fetch GitHub repositories:', err);
    } finally {
      setIsLoadingGhRepos(false);
    }
  };

  const handleSelectGitHubRepo = async (repo: GitHubRepoItem) => {
    setSelectedGhRepo(repo);
    setName(repo.name);
    setDefaultBranch(repo.default_branch || 'main');
    if (repo.language) setLanguage(repo.language);

    setIsLoadingBranches(true);
    try {
      const res = await fetch(
        `/api/v1/github/repositories/branches?owner=${encodeURIComponent(
          repo.owner
        )}&repo=${encodeURIComponent(repo.name)}`,
        { credentials: 'include' }
      );
      if (res.ok) {
        const bData = await res.json();
        if (bData.branches && bData.branches.length > 0) {
          setGhBranches(bData.branches);
          if (bData.default_branch) setDefaultBranch(bData.default_branch);
        }
      }
    } catch {
      setGhBranches([repo.default_branch || 'main']);
    } finally {
      setIsLoadingBranches(false);
    }
  };

  // Job status polling
  const startPollingJob = (jobId: string, proj: Project) => {
    setActiveJobId(jobId);
    setCreatedProject(proj);

    const checkJob = async () => {
      try {
        const res = await fetch(`/api/v1/jobs/${jobId}`, {
          credentials: 'include',
        });
        if (res.ok) {
          const data: JobStatusData = await res.json();
          setJobData(data);
          if (
            data.status === 'COMPLETED' ||
            data.status === 'SUCCEEDED' ||
            data.status === 'FAILED' ||
            data.status === 'CANCELLED'
          ) {
            if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
          }
        }
      } catch (err) {
        console.warn('Job polling check error:', err);
      }
    };

    checkJob();
    pollIntervalRef.current = setInterval(checkJob, 1200);
  };

  // Form submission
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (!name.trim()) {
      setFormError('Project name is required.');
      return;
    }

    const payload: any = {
      name: name.trim(),
      source_type: sourceType,
      default_branch: defaultBranch.trim() || 'main',
      language: language.trim() || undefined,
    };

    if (sourceType === 'LOCAL') {
      if (!localPath.trim()) {
        setFormError('Local repository path is required.');
        return;
      }
      payload.local_path = localPath.trim();
    } else if (sourceType === 'GITHUB') {
      if (selectedGhRepo) {
        payload.github_repository_id = selectedGhRepo.id;
        payload.github_owner = selectedGhRepo.owner;
        payload.github_repo = selectedGhRepo.name;
        payload.clone_url = selectedGhRepo.clone_url;
      } else if (customGhRepo.trim()) {
        const parts = customGhRepo.trim().split('/');
        if (parts.length === 2) {
          payload.github_owner = parts[0];
          payload.github_repo = parts[1];
          payload.clone_url = `https://github.com/${parts[0]}/${parts[1]}.git`;
        } else {
          setFormError('Enter GitHub repository in owner/repo format.');
          return;
        }
      } else {
        setFormError('Please select or specify a GitHub repository.');
        return;
      }
    } else if (sourceType === 'GIT_URL') {
      if (!cloneUrl.trim()) {
        setFormError('Git clone URL is required.');
        return;
      }
      let finalUrl = cloneUrl.trim();
      if (authType === 'TOKEN' && gitToken.trim()) {
        if (finalUrl.startsWith('https://')) {
          finalUrl = `https://${encodeURIComponent(gitToken.trim())}@${finalUrl.replace('https://', '')}`;
        }
      }
      payload.clone_url = finalUrl;
      payload.repository_url = cloneUrl.trim();
    }

    setIsSubmitting(true);
    try {
      const res = await fetch('/api/v1/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        setFormError(data.detail || 'Failed to create project');
        setIsSubmitting(false);
        return;
      }

      const newProj: Project = data;
      const initialJobId = data.initial_job_id;

      if (initialJobId) {
        startPollingJob(initialJobId, newProj);
      } else {
        onProjectCreated(newProj);
        onClose();
      }
    } catch (err: any) {
      setFormError(err.message || 'Error communicating with server');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRetryScan = async () => {
    if (!createdProject) return;
    try {
      const res = await fetch(`/api/v1/jobs/projects/${createdProject.id}/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ incremental: false }),
      });
      if (res.ok) {
        const j = await res.json();
        startPollingJob(j.id, createdProject);
      }
    } catch {
      setFormError('Failed to trigger retry scan.');
    }
  };

  const filteredGhRepos = ghRepos.filter(
    (r) =>
      r.name.toLowerCase().includes(ghSearchQuery.toLowerCase()) ||
      r.full_name.toLowerCase().includes(ghSearchQuery.toLowerCase())
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 md:p-6 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200 overflow-y-auto">
      <div className="bg-slate-900 border border-slate-800/90 rounded-2xl w-full max-w-xl shadow-2xl transition-all max-h-[90vh] flex flex-col overflow-hidden my-auto">
        {/* Modal Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 shrink-0 bg-slate-900">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
              <FolderGit2 className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-white">Create New Project</h2>
              <p className="text-xs text-slate-400">
                Add a repository to your verified individual memory brain
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* In-Modal Job Progress View */}
        {activeJobId ? (
          <div className="p-6 space-y-5 overflow-y-auto flex-1 custom-scrollbar">
            <div className="text-center space-y-2">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">
                {sourceType === 'LOCAL' && <FolderGit2 className="w-3.5 h-3.5" />}
                {sourceType === 'GITHUB' && <Github className="w-3.5 h-3.5" />}
                {sourceType === 'GIT_URL' && <Globe className="w-3.5 h-3.5" />}
                <span>{sourceType} INGESTION</span>
              </div>
              <h3 className="text-lg font-bold text-white">
                {createdProject?.name || 'Processing Project'}
              </h3>
              <p className="text-xs text-slate-400">
                {isJobSuccess
                  ? 'Knowledge graph synthesized and memory verified'
                  : jobData?.status === 'FAILED'
                  ? 'Ingestion or scanning encountered an issue'
                  : 'Synthesizing AST, extracting dependencies, and linking memory evidence...'}
              </p>
            </div>

            {/* Step Indicators */}
            <div className="space-y-3 bg-slate-950 p-4 rounded-xl border border-slate-800">
              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2 text-slate-300">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  <span>Repository connected & validated</span>
                </div>
                <span className="text-emerald-400 font-mono text-[11px]">Ready</span>
              </div>

              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2 text-slate-300">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  <span>Project created in database</span>
                </div>
                <span className="text-emerald-400 font-mono text-[11px]">Saved</span>
              </div>

              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2 text-slate-300">
                  {isJobSuccess ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  ) : jobData?.status === 'FAILED' ? (
                    <AlertCircle className="w-4 h-4 text-rose-400" />
                  ) : (
                    <RefreshCw className="w-4 h-4 text-indigo-400 animate-spin" />
                  )}
                  <span>AST parsing & symbol extraction</span>
                </div>
                <span className="text-slate-400 font-mono text-[11px]">
                  {(jobData?.result?.entities_extracted ?? jobData?.result?.entities_found)
                    ? `${jobData?.result?.entities_extracted ?? jobData?.result?.entities_found} symbols`
                    : isJobSuccess
                    ? 'Done'
                    : 'Running'}
                </span>
              </div>

              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2 text-slate-300">
                  {isJobSuccess ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  ) : jobData?.status === 'FAILED' ? (
                    <AlertCircle className="w-4 h-4 text-rose-400" />
                  ) : (
                    <Layers className="w-4 h-4 text-slate-500" />
                  )}
                  <span>Cognitive state & memory evidence</span>
                </div>
                <span className="text-slate-400 font-mono text-[11px]">
                  {jobData?.result?.memories_created
                    ? `${jobData.result.memories_created} memories`
                    : isJobSuccess
                    ? 'Linked'
                    : 'Queued'}
                </span>
              </div>
            </div>

            {/* Results Card on Success */}
            {isJobSuccess && (
              <div className="bg-emerald-950/30 border border-emerald-800/40 rounded-xl p-4">
                <div className="flex items-center gap-2 text-emerald-400 text-sm font-semibold mb-3">
                  <CheckCircle2 className="w-4 h-4" />
                  <span>CortexForge is ready</span>
                </div>
                <div className="grid grid-cols-4 gap-2 text-center">
                  <div className="bg-slate-900/80 p-2.5 rounded-lg border border-slate-800">
                    <div className="text-[11px] text-slate-400">Files</div>
                    <div className="text-base font-bold text-white">
                      {jobData?.result?.files_scanned ?? 0}
                    </div>
                  </div>
                  <div className="bg-slate-900/80 p-2.5 rounded-lg border border-slate-800">
                    <div className="text-[11px] text-slate-400">Symbols</div>
                    <div className="text-base font-bold text-indigo-400">
                      {jobData?.result?.entities_extracted ?? jobData?.result?.entities_found ?? 0}
                    </div>
                  </div>
                  <div className="bg-slate-900/80 p-2.5 rounded-lg border border-slate-800">
                    <div className="text-[11px] text-slate-400">Relations</div>
                    <div className="text-base font-bold text-cyan-400">
                      {jobData?.result?.relationships_extracted ?? jobData?.result?.relationships_built ?? 0}
                    </div>
                  </div>
                  <div className="bg-slate-900/80 p-2.5 rounded-lg border border-slate-800">
                    <div className="text-[11px] text-slate-400">Memories</div>
                    <div className="text-base font-bold text-emerald-400">
                      {jobData?.result?.memories_created ?? 0}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Error Message on Failure */}
            {jobData?.status === 'FAILED' && (
              <div className="bg-rose-950/40 border border-rose-800/50 rounded-xl p-4 text-rose-300 text-xs space-y-2">
                <div className="flex items-center gap-2 font-semibold text-rose-400">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  <span>Ingestion failed</span>
                </div>
                <p className="text-slate-300 font-mono text-[11px]">
                  {jobData.error || 'An unexpected error occurred during repository ingestion.'}
                </p>
              </div>
            )}

            {/* Action Buttons */}
            <div className="flex items-center justify-end gap-3 pt-2">
              {jobData?.status === 'FAILED' ? (
                <>
                  <button
                    type="button"
                    onClick={onClose}
                    className="px-4 py-2 rounded-xl text-sm font-medium text-slate-400 hover:text-white"
                  >
                    Close
                  </button>
                  <button
                    type="button"
                    onClick={handleRetryScan}
                    className="flex items-center gap-2 px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all"
                  >
                    <RefreshCw className="w-4 h-4" />
                    <span>Retry Scan</span>
                  </button>
                </>
              ) : isJobSuccess ? (
                <button
                  type="button"
                  onClick={() => {
                    if (createdProject) onProjectCreated(createdProject);
                    onClose();
                  }}
                  className="w-full flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-lg shadow-indigo-600/30"
                >
                  <span>Open Project</span>
                  <ArrowRight className="w-4 h-4" />
                </button>
              ) : (
                <div className="flex items-center gap-2 text-xs text-slate-500 font-mono">
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  <span>Processing...</span>
                </div>
              )}
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="flex flex-col flex-1 min-h-0 overflow-hidden">
            {/* Source Selector Tabs */}
            <div className="px-6 pt-4 pb-3 shrink-0 bg-slate-900 border-b border-slate-800/40">
              <label className="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2">
                Repository Source
              </label>
              <div className="grid grid-cols-3 gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setSourceType('LOCAL');
                    setFormError(null);
                  }}
                  className={`flex items-center justify-center gap-2 p-2.5 rounded-xl border text-xs font-semibold transition-all ${
                    sourceType === 'LOCAL'
                      ? 'bg-indigo-600/20 border-indigo-500 text-indigo-300 shadow-md shadow-indigo-500/10'
                      : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-300 hover:border-slate-700'
                  }`}
                >
                  <FolderGit2 className="w-4 h-4" />
                  <span>Local Repo</span>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setSourceType('GITHUB');
                    setFormError(null);
                  }}
                  className={`flex items-center justify-center gap-2 p-2.5 rounded-xl border text-xs font-semibold transition-all ${
                    sourceType === 'GITHUB'
                      ? 'bg-indigo-600/20 border-indigo-500 text-indigo-300 shadow-md shadow-indigo-500/10'
                      : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-300 hover:border-slate-700'
                  }`}
                >
                  <Github className="w-4 h-4" />
                  <span>GitHub</span>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setSourceType('GIT_URL');
                    setFormError(null);
                  }}
                  className={`flex items-center justify-center gap-2 p-2.5 rounded-xl border text-xs font-semibold transition-all ${
                    sourceType === 'GIT_URL'
                      ? 'bg-indigo-600/20 border-indigo-500 text-indigo-300 shadow-md shadow-indigo-500/10'
                      : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-300 hover:border-slate-700'
                  }`}
                >
                  <Globe className="w-4 h-4" />
                  <span>Git URL</span>
                </button>
              </div>
            </div>

            {/* Scrollable Form Body */}
            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4 custom-scrollbar">
              {formError && (
                <div className="p-3.5 rounded-xl bg-rose-950/40 border border-rose-800/60 text-rose-300 text-xs flex items-center gap-2">
                  <AlertCircle className="w-4 h-4 shrink-0 text-rose-400" />
                  <span>{formError}</span>
                </div>
              )}

              {/* 1. LOCAL REPO FLOW */}
              {/* 1. LOCAL REPO FLOW */}
              {sourceType === 'LOCAL' && (
                <>
                  <div className="space-y-1">
                    <div className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                      Local Repository
                    </div>
                    <p className="text-xs text-slate-400">
                      Select the repository folder from your computer.
                    </p>
                  </div>

                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
                        Repository Folder <span className="text-indigo-400">*</span>
                      </label>
                      <div className="flex items-center gap-2">
                        {isValidatingPath && (
                          <span className="text-[11px] text-indigo-400 flex items-center gap-1 font-mono">
                            <RefreshCw className="w-3 h-3 animate-spin" />
                            <span>Validating...</span>
                          </span>
                        )}
                        <button
                          type="button"
                          onClick={() => handleValidateLocalPath()}
                          disabled={isValidatingPath || !localPath.trim()}
                          className="text-[11px] text-slate-400 hover:text-slate-200 disabled:opacity-50 flex items-center gap-1 transition-colors px-2 py-0.5 rounded bg-slate-800/80 border border-slate-700/60"
                          title="Re-validate repository path"
                        >
                          <ShieldCheck className="w-3 h-3 text-indigo-400" />
                          <span>Check</span>
                        </button>
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <input
                        type="text"
                        required
                        value={localPath}
                        onChange={(e) => {
                          setLocalPath(e.target.value);
                          setValidationResult(null);
                        }}
                        onBlur={() => handleValidateLocalPath()}
                        placeholder="e.g. C:\Projects\MyRepo or /home/user/projects/my-repo"
                        className="flex-1 bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm font-mono text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                      <button
                        type="button"
                        onClick={handleOpenFolderBrowser}
                        className="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-semibold flex items-center gap-2 transition-all shrink-0 shadow-md shadow-indigo-600/20 active:scale-[0.98]"
                        title="Browse folders on your computer"
                      >
                        <FolderOpen className="w-4 h-4" />
                        <span>Browse Folder</span>
                      </button>
                    </div>

                    {/* Interactive Folder Browser Explorer Card (Fallback / Secondary) */}
                    {showFolderBrowser && (
                      <div className="mt-3 bg-slate-950 border border-indigo-500/40 rounded-xl p-3.5 space-y-3 shadow-xl">
                        <div className="flex items-center justify-between pb-2 border-b border-slate-800">
                          <div className="flex items-center gap-2 text-xs font-semibold text-slate-200">
                            <FolderOpen className="w-4 h-4 text-indigo-400" />
                            <span>Browse Host Folders</span>
                          </div>
                          <div className="flex items-center gap-1.5">
                            {browseData?.parent_path && (
                              <button
                                type="button"
                                onClick={() => fetchDirectories(browseData.parent_path)}
                                className="text-[11px] bg-slate-900 hover:bg-slate-800 border border-slate-700 px-2 py-0.5 rounded text-slate-300 transition-colors flex items-center gap-1"
                                title="Go up to parent directory"
                              >
                                <span>⬆️</span>
                                <span>Up</span>
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => fetchDirectories(null)}
                              className="text-[11px] bg-slate-900 hover:bg-slate-800 border border-slate-700 px-2 py-0.5 rounded text-slate-300 transition-colors flex items-center gap-1"
                              title="Go to user home folder"
                            >
                              <span>🏠</span>
                              <span>Home</span>
                            </button>
                            {(browseData?.is_windows ?? true) && (
                              <button
                                type="button"
                                onClick={() => fetchDirectories('DRIVES')}
                                className="text-[11px] bg-slate-900 hover:bg-slate-800 border border-slate-700 px-2 py-0.5 rounded text-slate-300 transition-colors flex items-center gap-1"
                                title="View all drive roots (e.g. C:\, D:\)"
                              >
                                <span>💽</span>
                                <span>Drives</span>
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => fetchDirectories(browseData?.current_path)}
                              className="text-[11px] text-slate-400 hover:text-white p-1"
                              title="Refresh directory list"
                            >
                              <RefreshCw className={`w-3.5 h-3.5 ${isLoadingDirectories ? 'animate-spin' : ''}`} />
                            </button>
                            <button
                              type="button"
                              onClick={() => setShowFolderBrowser(false)}
                              className="text-[11px] text-slate-500 hover:text-slate-300 ml-1 p-1"
                              title="Close folder explorer"
                            >
                              <X className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>

                        {/* Current Path Indicator */}
                        <div className="text-[11px] font-mono text-slate-400 bg-slate-900/80 px-2.5 py-1.5 rounded-lg border border-slate-800/80 truncate">
                          📁 {browseData?.current_path === 'DRIVES' ? 'Logical Drives' : (browseData?.current_path || 'Loading host filesystem...')}
                        </div>

                        {/* Directory List */}
                        <div className="max-h-48 overflow-y-auto space-y-1 pr-1 custom-scrollbar">
                          {isLoadingDirectories ? (
                            <div className="flex items-center justify-center py-6 text-xs text-slate-500 gap-2 font-mono">
                              <RefreshCw className="w-4 h-4 animate-spin" />
                              <span>Scanning directories...</span>
                            </div>
                          ) : browseData?.directories && browseData.directories.length > 0 ? (
                            browseData.directories.map((dir) => (
                              <div
                                key={dir.path}
                                className="flex items-center justify-between p-2 rounded-lg bg-slate-900/50 hover:bg-slate-900 border border-transparent hover:border-slate-800 text-xs transition-colors group"
                              >
                                <button
                                  type="button"
                                  onClick={() => fetchDirectories(dir.path)}
                                  className="flex items-center gap-2 text-left truncate flex-1 hover:text-indigo-300 transition-colors"
                                  title={`Open ${dir.name}`}
                                >
                                  <Folder className="w-4 h-4 text-amber-400/80 shrink-0" />
                                  <span className="font-mono text-slate-200 group-hover:text-white truncate">
                                    {dir.name}
                                  </span>
                                  {dir.is_git && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-950/70 border border-emerald-800/60 text-emerald-400 font-sans font-semibold shrink-0">
                                      Git
                                    </span>
                                  )}
                                </button>
                                <div className="flex items-center gap-1.5 ml-2">
                                  <button
                                    type="button"
                                    onClick={() => handleSelectDirectory(dir.path, dir.name)}
                                    className="px-2.5 py-1 rounded-md bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-[11px] transition-all shadow-sm shadow-indigo-600/20"
                                  >
                                    Select
                                  </button>
                                </div>
                              </div>
                            ))
                          ) : (
                            <div className="py-5 text-center text-xs text-slate-500">
                              No subdirectories found in this folder.
                            </div>
                          )}
                        </div>

                        {/* Select Current Folder Button */}
                        {browseData?.current_path && browseData.current_path !== 'DRIVES' && (
                          <div className="pt-2 border-t border-slate-800/80 flex justify-end">
                            <button
                              type="button"
                              onClick={() => {
                                const folderName = browseData.current_path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || '';
                                handleSelectDirectory(browseData.current_path, folderName);
                              }}
                              className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold transition-colors flex items-center gap-1.5"
                            >
                              <span>Select Current Folder</span>
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Existing Project Detected */}
                  {existingProject && (
                    <div className="bg-amber-950/30 border border-amber-700/50 rounded-xl p-3.5 flex items-start gap-3">
                      <FolderGit2 className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-semibold text-amber-300 mb-0.5">
                          Already registered as <span className="font-mono text-amber-200">{existingProject.name}</span>
                        </div>
                        <p className="text-[11px] text-slate-400">
                          This folder is already a CortexForge project. Open it from the sidebar or your project list.
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={onClose}
                        className="shrink-0 px-3 py-1.5 rounded-lg bg-amber-700/40 hover:bg-amber-700/70 text-amber-300 text-xs font-semibold transition-colors whitespace-nowrap"
                      >
                        Got it
                      </button>
                    </div>
                  )}

                  {/* Local Path Status & Language Area */}
                  {validationResult && !existingProject && (
                    <div className="bg-slate-950/70 border border-slate-800/90 rounded-xl p-3.5 space-y-3 shadow-inner">
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                          Repository Status
                        </span>
                        {validationResult.is_git && (
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-semibold flex items-center gap-1">
                            <CheckCircle2 className="w-3 h-3" />
                            Git Initialized
                          </span>
                        )}
                      </div>

                      <div className="grid grid-cols-3 gap-2 text-xs">
                        <div className="flex items-center gap-1.5 p-2 rounded-lg bg-slate-900/60 border border-slate-800/60">
                          {validationResult.valid ? (
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                          ) : (
                            <AlertCircle className="w-3.5 h-3.5 text-rose-400 shrink-0" />
                          )}
                          <span
                            className={`text-[11px] truncate ${
                              validationResult.valid ? 'text-slate-300' : 'text-rose-300'
                            }`}
                          >
                            {validationResult.valid ? 'Folder Exists' : 'Invalid Path'}
                          </span>
                        </div>

                        <div className="flex items-center gap-1.5 p-2 rounded-lg bg-slate-900/60 border border-slate-800/60">
                          {validationResult.is_git ? (
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                          ) : (
                            <AlertCircle className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                          )}
                          <span
                            className={`text-[11px] truncate ${
                              validationResult.is_git ? 'text-slate-300' : 'text-amber-300'
                            }`}
                          >
                            {validationResult.is_git ? 'Git Repo' : 'No Git'}
                          </span>
                        </div>

                        <div className="flex items-center gap-1.5 p-2 rounded-lg bg-slate-900/60 border border-slate-800/60">
                          {validationResult.valid ? (
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                          ) : (
                            <AlertCircle className="w-3.5 h-3.5 text-rose-400 shrink-0" />
                          )}
                          <span
                            className={`text-[11px] truncate ${
                              validationResult.valid ? 'text-slate-300' : 'text-rose-300'
                            }`}
                          >
                            {validationResult.valid ? 'Accessible' : 'No Access'}
                          </span>
                        </div>
                      </div>

                      {validationResult.languages &&
                        Object.keys(validationResult.languages).length > 0 && (
                          <div className="pt-2.5 border-t border-slate-800/80">
                            <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
                              Detected Languages
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                              {Object.entries(validationResult.languages).map(([lang, pct]) => (
                                <span
                                  key={lang}
                                  className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono bg-indigo-500/10 border border-indigo-500/25 text-indigo-300"
                                >
                                  <span className="w-1.5 h-1.5 rounded-full bg-indigo-400" />
                                  <span>{lang}</span>
                                  <span className="text-slate-400 text-[10px]">{pct}%</span>
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                    </div>
                  )}

                  {/* Project Name & Branch Inputs */}
                  <div className="space-y-3">
                    <div>
                      <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                        Project Name *
                      </label>
                      <input
                        type="text"
                        required
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="e.g. CortexForge"
                        className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                          Default Branch
                        </label>
                        <input
                          type="text"
                          value={defaultBranch}
                          onChange={(e) => setDefaultBranch(e.target.value)}
                          placeholder="main"
                          className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                          Primary Language
                        </label>
                        <input
                          type="text"
                          value={language}
                          onChange={(e) => setLanguage(e.target.value)}
                          placeholder="Auto-detect"
                          className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                        />
                      </div>
                    </div>
                  </div>

                  {/* Explicit User Consent (§15) */}
                  <div className="p-3 rounded-xl bg-indigo-950/20 border border-indigo-500/20 text-[11px] text-slate-400 flex items-start gap-2.5">
                    <div className="w-6 h-6 rounded-md bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center shrink-0 mt-0.5 text-indigo-400">
                      <Lock className="w-3.5 h-3.5" />
                    </div>
                    <div className="space-y-0.5 min-w-0 flex-1">
                      <div className="font-semibold text-slate-200 text-xs">
                        Repository Analysis & Indexing
                      </div>
                      <p className="text-slate-400 text-[11px] leading-relaxed">
                        CortexForge will parse AST symbols, map dependencies, and synthesize verified memory for <code className="text-indigo-300 font-mono text-[10px] bg-slate-950/60 px-1.5 py-0.5 rounded border border-indigo-500/20">{localPath || 'selected repository'}</code>.
                      </p>
                    </div>
                  </div>
                </>
              )}

              {/* 2. GITHUB REPOSITORY FLOW */}
              {sourceType === 'GITHUB' && (
                <>
                  {/* GitHub Connection Status */}
                  <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      <div className="w-8 h-8 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center text-white">
                        <Github className="w-4 h-4" />
                      </div>
                      <div>
                        <div className="text-xs font-semibold text-white">GitHub Account</div>
                        {githubConnected || user?.github_login ? (
                          <div className="flex items-center gap-1.5 text-xs text-emerald-400 font-mono">
                            <CheckCircle2 className="w-3.5 h-3.5" />
                            <span>Connected as @{user?.github_login || 'user'}</span>
                          </div>
                        ) : (
                          <div className="text-xs text-slate-400">Not connected</div>
                        )}
                      </div>
                    </div>
                    {!githubConnected && !user?.github_login && (
                      <a
                        href="/api/v1/auth/github"
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium transition-colors"
                      >
                        <Github className="w-3.5 h-3.5" />
                        <span>Connect GitHub</span>
                      </a>
                    )}
                  </div>

                  {/* Repository Picker */}
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      Select Repository *
                    </label>
                    <div className="relative mb-2">
                      <Search className="w-4 h-4 text-slate-500 absolute left-3 top-3" />
                      <input
                        type="text"
                        value={ghSearchQuery}
                        onChange={(e) => setGhSearchQuery(e.target.value)}
                        placeholder="Search your GitHub repositories..."
                        className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-9 pr-3.5 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                    </div>

                    {/* Repository list container */}
                    <div className="max-h-40 overflow-y-auto bg-slate-950 border border-slate-800 rounded-xl divide-y divide-slate-800/60">
                      {isLoadingGhRepos ? (
                        <div className="p-4 text-center text-xs text-slate-500 flex items-center justify-center gap-2">
                          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                          <span>Fetching repositories from GitHub...</span>
                        </div>
                      ) : filteredGhRepos.length > 0 ? (
                        filteredGhRepos.map((repo) => {
                          const isSelected = selectedGhRepo?.id === repo.id;
                          return (
                            <button
                              type="button"
                              key={repo.id}
                              onClick={() => handleSelectGitHubRepo(repo)}
                              className={`w-full text-left p-3 hover:bg-slate-900 flex items-center justify-between transition-colors ${
                                isSelected ? 'bg-indigo-950/40 border-l-2 border-indigo-500' : ''
                              }`}
                            >
                              <div className="min-w-0 pr-2">
                                <div className="flex items-center gap-2">
                                  <span className="text-xs font-semibold text-white truncate">
                                    {repo.name}
                                  </span>
                                  {repo.private && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 flex items-center gap-0.5">
                                      <Lock className="w-2.5 h-2.5" />
                                      <span>Private</span>
                                    </span>
                                  )}
                                  {repo.language && (
                                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-950/60 text-indigo-300 font-mono">
                                      {repo.language}
                                    </span>
                                  )}
                                </div>
                                {repo.description && (
                                  <p className="text-[11px] text-slate-400 truncate mt-0.5">
                                    {repo.description}
                                  </p>
                                )}
                              </div>
                              {isSelected ? (
                                <CheckCircle2 className="w-4 h-4 text-indigo-400 shrink-0" />
                              ) : (
                                <span className="text-[11px] text-slate-500 font-mono shrink-0">
                                  {repo.default_branch}
                                </span>
                              )}
                            </button>
                          );
                        })
                      ) : (
                        <div className="p-4 text-center text-xs text-slate-500">
                          {githubConnected || user?.github_login
                            ? 'No repositories found matching query. You can also specify an owner/repo below.'
                            : 'Connect your GitHub account above to view your repositories.'}
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Fallback owner/repo entry */}
                  {!selectedGhRepo && (
                    <div>
                      <label className="block text-[11px] text-slate-400 mb-1">
                        Or enter repository manually (owner/repo):
                      </label>
                      <input
                        type="text"
                        value={customGhRepo}
                        onChange={(e) => {
                          setCustomGhRepo(e.target.value);
                          const inferredName = e.target.value.split('/')[1];
                          if (inferredName) setName(inferredName);
                        }}
                        placeholder="owner/repository"
                        className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                    </div>
                  )}

                  {/* Project Name & Branch */}
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                        Project Name *
                      </label>
                      <input
                        type="text"
                        required
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="e.g. MyRepo"
                        className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                    </div>

                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                          Branch
                        </label>
                        {isLoadingBranches && (
                          <RefreshCw className="w-3 h-3 text-indigo-400 animate-spin" />
                        )}
                      </div>
                      {ghBranches.length > 0 ? (
                        <select
                          value={defaultBranch}
                          onChange={(e) => setDefaultBranch(e.target.value)}
                          className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                        >
                          {ghBranches.map((b) => (
                            <option key={b} value={b}>
                              {b}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type="text"
                          value={defaultBranch}
                          onChange={(e) => setDefaultBranch(e.target.value)}
                          placeholder="main"
                          className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                        />
                      )}
                    </div>
                  </div>

                  <p className="text-[11px] text-slate-400">
                    Cloned securely into an isolated CortexForge-managed workspace.
                  </p>
                </>
              )}

              {/* 3. GIT URL FLOW */}
              {sourceType === 'GIT_URL' && (
                <>
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      Git Clone URL *
                    </label>
                    <input
                      type="url"
                      required
                      value={cloneUrl}
                      onChange={(e) => {
                        const val = e.target.value;
                        setCloneUrl(val);
                        if (!name && val) {
                          const inferred = val.split('/').pop()?.replace('.git', '');
                          if (inferred) setName(inferred);
                        }
                      }}
                      placeholder="https://github.com/owner/repository.git"
                      className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm font-mono text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                    />
                    <p className="text-[11px] text-slate-400 mt-1">
                      Supports public or authenticated HTTPS Git repositories.
                    </p>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                        Project Name *
                      </label>
                      <input
                        type="text"
                        required
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="e.g. My Project"
                        className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                        Branch
                      </label>
                      <input
                        type="text"
                        value={defaultBranch}
                        onChange={(e) => setDefaultBranch(e.target.value)}
                        placeholder="main"
                        className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                    </div>
                  </div>

                  {/* Authentication Mode */}
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                      Authentication
                    </label>
                    <div className="grid grid-cols-2 gap-2 mb-2">
                      <button
                        type="button"
                        onClick={() => setAuthType('PUBLIC')}
                        className={`p-2.5 rounded-xl border text-xs font-medium transition-all ${
                          authType === 'PUBLIC'
                            ? 'bg-indigo-600/20 border-indigo-500 text-indigo-300'
                            : 'bg-slate-950 border-slate-800 text-slate-400'
                        }`}
                      >
                        Public Repository
                      </button>
                      <button
                        type="button"
                        onClick={() => setAuthType('TOKEN')}
                        className={`p-2.5 rounded-xl border text-xs font-medium transition-all ${
                          authType === 'TOKEN'
                            ? 'bg-indigo-600/20 border-indigo-500 text-indigo-300'
                            : 'bg-slate-950 border-slate-800 text-slate-400'
                        }`}
                      >
                        Private (Token)
                      </button>
                    </div>

                    {authType === 'TOKEN' && (
                      <input
                        type="password"
                        value={gitToken}
                        onChange={(e) => setGitToken(e.target.value)}
                        placeholder="Personal Access Token (ghp_...)"
                        className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-colors"
                      />
                    )}
                  </div>

                  <p className="text-[11px] text-slate-400">
                    CortexForge will clone the repository into an isolated managed workspace.
                  </p>
                </>
              )}

                </div>

                {/* Modal Actions (Sticky Pinned Footer) */}
                <div className="flex items-center justify-end gap-3 px-6 py-3.5 border-t border-slate-800 shrink-0 bg-slate-900/95 backdrop-blur">
                  <button
                    type="button"
                    onClick={onClose}
                    className="px-4 py-2 rounded-xl text-sm font-medium text-slate-400 hover:text-white transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-sm transition-all shadow-md shadow-indigo-600/20 active:scale-[0.99] disabled:opacity-60"
                  >
                    {isSubmitting ? (
                      <RefreshCw className="w-4 h-4 animate-spin" />
                    ) : (
                      <span>
                        {sourceType === 'LOCAL'
                          ? 'Create & Scan'
                          : sourceType === 'GITHUB'
                          ? 'Import & Scan'
                          : 'Clone & Scan'}
                      </span>
                    )}
                  </button>
                </div>
              </form>
            )}
      </div>
    </div>
  );
};
