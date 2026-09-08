import React from 'react';
import { ArchitectureResponse, Project } from '../types';
import { RefreshCw, Play, CheckCircle2, Cpu, Database, Terminal, FileCode } from 'lucide-react';

interface OverviewProps {
  project: Project | null;
  arch: ArchitectureResponse | null;
  onTriggerScan: () => void;
  onConsolidate: () => void;
  isScanning: boolean;
}

export const Overview: React.FC<OverviewProps> = ({
  project,
  arch,
  onTriggerScan,
  onConsolidate,
  isScanning,
}) => {
  if (!project) {
    return (
      <div className="p-8 text-center text-slate-400">
        No project selected. Register or scan a repository via CLI first: <code className="text-brand-400">cortex scan .</code>
      </div>
    );
  }

  const loopSteps = [
    { title: 'OBSERVE', desc: 'Git commits & file modifications' },
    { title: 'UNDERSTAND', desc: 'Tree-sitter AST symbol resolution' },
    { title: 'STORE', desc: 'Relational graph & pgvector embeddings' },
    { title: 'VERIFY', desc: 'Active grounding against current code' },
    { title: 'CONSOLIDATE', desc: 'Cluster episodes into durable lessons' },
    { title: 'RETRIEVE', desc: 'Multi-signal hybrid scoring (MMR)' },
    { title: 'ACT', desc: 'Token-budget structured prompt injection' },
  ];

  return (
    <div className="p-8 space-y-8 max-w-7xl mx-auto">
      {/* Top Banner */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-slate-900/60 p-6 rounded-xl border border-slate-800">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-2xl font-bold text-white tracking-tight">{project.name}</h2>
            <span className="text-xs px-2.5 py-1 rounded-full bg-brand-500/10 text-brand-400 border border-brand-500/20 font-mono font-semibold">
              {project.status}
            </span>
          </div>
          <p className="text-sm text-slate-400 mt-1 font-mono">{project.local_path}</p>
          {project.last_indexed_commit && (
            <p className="text-xs text-slate-400 mt-1 font-mono">
              Last Indexed Commit: <span className="text-brand-400">{project.last_indexed_commit.slice(0, 8)}</span>
            </p>
          )}
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={onTriggerScan}
            disabled={isScanning}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-500 text-slate-950 font-semibold text-sm transition-all shadow-md shadow-brand-500/10 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${isScanning ? 'animate-spin' : ''}`} />
            {isScanning ? 'Scanning AST...' : 'Incremental Scan'}
          </button>
          <button
            onClick={onConsolidate}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 font-medium text-sm transition-all"
          >
            <Play className="w-4 h-4 text-brand-400" />
            Consolidate Memories
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-slate-900/40 p-5 rounded-xl border border-slate-800 flex items-center gap-4">
          <div className="p-3 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-400">
            <FileCode className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-white">{arch?.total_files ?? 0}</div>
            <div className="text-xs text-slate-400">Scanned Files</div>
          </div>
        </div>

        <div className="bg-slate-900/40 p-5 rounded-xl border border-slate-800 flex items-center gap-4">
          <div className="p-3 rounded-lg bg-brand-500/10 border border-brand-500/20 text-brand-400">
            <Cpu className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-white">{arch?.total_entities ?? 0}</div>
            <div className="text-xs text-slate-400">AST Code Entities</div>
          </div>
        </div>

        <div className="bg-slate-900/40 p-5 rounded-xl border border-slate-800 flex items-center gap-4">
          <div className="p-3 rounded-lg bg-purple-500/10 border border-purple-500/20 text-purple-400">
            <Database className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-white">{arch?.total_relationships ?? 0}</div>
            <div className="text-xs text-slate-400">Graph Relationships</div>
          </div>
        </div>

        <div className="bg-slate-900/40 p-5 rounded-xl border border-slate-800 flex items-center gap-4">
          <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400">
            <CheckCircle2 className="w-6 h-6" />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-white">{project.memory_count ?? 0}</div>
            <div className="text-xs text-slate-400">Project Memories (L0–L6)</div>
          </div>
        </div>
      </div>

      {/* The Continuous Cognitive Loop Visualizer */}
      <div className="bg-slate-900/40 p-6 rounded-xl border border-slate-800 space-y-4">
        <h3 className="font-bold text-white text-base flex items-center gap-2">
          <span>Continuous Project Cognitive Loop</span>
          <span className="text-xs text-slate-400 font-mono font-normal">Active Cognitive Cycle</span>
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
          {loopSteps.map((step, idx) => (
            <div key={idx} className="bg-slate-950 p-3 rounded-lg border border-slate-800 text-center space-y-1">
              <div className="text-[10px] font-mono text-brand-400 font-bold tracking-wider">
                STEP 0{idx + 1}
              </div>
              <div className="font-bold text-xs text-slate-200">{step.title}</div>
              <div className="text-[11px] text-slate-400 leading-tight">{step.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {/* MCP Quick Connect Snippet */}
      <div className="bg-slate-900/40 p-6 rounded-xl border border-slate-800 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="font-bold text-white text-sm flex items-center gap-2">
            <Terminal className="w-4 h-4 text-brand-400" />
            Connect AI Coding Agent via Model Context Protocol (MCP)
          </h3>
          <span className="text-xs text-slate-400 font-mono">claude_desktop_config.json</span>
        </div>
        <pre className="bg-slate-950 p-4 rounded-lg border border-slate-800 font-mono text-xs text-slate-300 overflow-x-auto">
{`{
  "mcpServers": {
    "cortexforge": {
      "command": "cortex",
      "args": ["mcp"],
      "cwd": "${project.local_path.replace(/\\/g, '/')}"
    }
  }
}`}
        </pre>
      </div>
    </div>
  );
};
