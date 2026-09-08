import React, { useState } from 'react';
import { Memory } from '../types';
import { verifyMemory, deprecateMemory } from '../api';
import {
  Search,
  Filter,
  CheckCircle2,
  AlertTriangle,
  Clock,
  ShieldAlert,
  GitCommit,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  RefreshCw,
  Archive,
} from 'lucide-react';

interface MemoryExplorerProps {
  memories: Memory[];
  onRefresh: () => void;
  isLoading: boolean;
}

const TYPE_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  DECISION: { bg: 'bg-emerald-950/40', text: 'text-emerald-400', border: 'border-emerald-800/60' },
  CONSTRAINT: { bg: 'bg-rose-950/40', text: 'text-rose-400', border: 'border-rose-800/60' },
  FAILURE: { bg: 'bg-red-950/40', text: 'text-red-400', border: 'border-red-800/60' },
  LESSON: { bg: 'bg-purple-950/40', text: 'text-purple-400', border: 'border-purple-800/60' },
  ARCHITECTURE_PATTERN: { bg: 'bg-blue-950/40', text: 'text-blue-400', border: 'border-blue-800/60' },
  CONVENTION: { bg: 'bg-teal-950/40', text: 'text-teal-400', border: 'border-teal-800/60' },
  FACT: { bg: 'bg-slate-900', text: 'text-slate-300', border: 'border-slate-700' },
  EPISODE: { bg: 'bg-amber-950/40', text: 'text-amber-400', border: 'border-amber-800/60' },
};

export const MemoryExplorer: React.FC<MemoryExplorerProps> = ({
  memories,
  onRefresh,
  isLoading,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedType, setSelectedType] = useState('ALL');
  const [selectedStatus, setSelectedStatus] = useState('ALL');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [actionInProgress, setActionInProgress] = useState<string | null>(null);

  const memoryTypes = [
    'ALL',
    'DECISION',
    'CONSTRAINT',
    'FAILURE',
    'LESSON',
    'ARCHITECTURE_PATTERN',
    'CONVENTION',
    'FACT',
    'EPISODE',
  ];

  const memoryStatuses = ['ALL', 'ACTIVE', 'STALE', 'CONFLICTED', 'DEPRECATED'];

  const filteredMemories = memories.filter((m) => {
    const matchesSearch =
      m.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.summary.toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.content.toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.id.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesType = selectedType === 'ALL' || m.memory_type === selectedType;
    const matchesStatus = selectedStatus === 'ALL' || m.status === selectedStatus;

    return matchesSearch && matchesType && matchesStatus;
  });

  const handleCopyId = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(id);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleVerify = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setActionInProgress(id);
    await verifyMemory(id);
    setActionInProgress(null);
    onRefresh();
  };

  const handleDeprecate = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm('Are you sure you want to mark this memory as DEPRECATED?')) return;
    setActionInProgress(id);
    await deprecateMemory(id);
    setActionInProgress(null);
    onRefresh();
  };

  return (
    <div className="space-y-6">
      {/* Header & Controls */}
      <div className="flex flex-col gap-4 bg-slate-900/60 p-5 rounded-xl border border-slate-800 shadow-sm backdrop-blur">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input
              type="text"
              placeholder="Search memories by title, concept, summary, or ID..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2.5 bg-slate-950 border border-slate-700/80 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-all font-mono"
            />
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onRefresh}
              disabled={isLoading}
              className="flex items-center gap-2 px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium border border-slate-700 transition"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <div className="text-xs text-slate-400 font-mono pl-2">
              Showing <span className="text-indigo-400 font-semibold">{filteredMemories.length}</span> of {memories.length}
            </div>
          </div>
        </div>

        {/* Filter Pills */}
        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-800/80">
          <span className="text-xs font-medium text-slate-400 flex items-center gap-1.5 mr-2">
            <Filter className="w-3.5 h-3.5" /> Type:
          </span>
          {memoryTypes.map((t) => (
            <button
              key={t}
              onClick={() => setSelectedType(t)}
              className={`text-xs px-2.5 py-1 rounded-md transition font-mono ${
                selectedType === t
                  ? 'bg-indigo-600 text-white font-semibold shadow-sm'
                  : 'bg-slate-800/80 text-slate-400 hover:text-slate-200 hover:bg-slate-700/80 border border-slate-800'
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Status Pills */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-slate-400 mr-2">Status:</span>
          {memoryStatuses.map((s) => (
            <button
              key={s}
              onClick={() => setSelectedStatus(s)}
              className={`text-xs px-2.5 py-1 rounded-md transition font-mono ${
                selectedStatus === s
                  ? 'bg-slate-200 text-slate-900 font-semibold'
                  : 'bg-slate-800/50 text-slate-400 hover:text-slate-200 border border-slate-800'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Memory List */}
      <div className="space-y-3">
        {filteredMemories.length === 0 ? (
          <div className="text-center py-16 bg-slate-900/40 rounded-xl border border-slate-800">
            <AlertTriangle className="w-10 h-10 text-slate-600 mx-auto mb-3" />
            <div className="text-sm font-medium text-slate-300">No project memories found</div>
            <div className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
              No memories matched your search or filters. Try adjusting your query or scanning the repository.
            </div>
          </div>
        ) : (
          filteredMemories.map((mem) => {
            const isExpanded = expandedId === mem.id;
            const style = TYPE_COLORS[mem.memory_type] || {
              bg: 'bg-slate-900',
              text: 'text-slate-300',
              border: 'border-slate-800',
            };

            return (
              <div
                key={mem.id}
                className={`group bg-slate-900/80 border rounded-xl transition-all overflow-hidden ${
                  isExpanded ? 'border-indigo-500/60 shadow-lg shadow-indigo-950/20' : 'border-slate-800/90 hover:border-slate-700'
                }`}
              >
                {/* Header Row */}
                <div
                  onClick={() => setExpandedId(isExpanded ? null : mem.id)}
                  className="p-4 cursor-pointer flex flex-col md:flex-row md:items-center justify-between gap-3 select-none"
                >
                  <div className="flex items-start md:items-center gap-3 flex-1 min-w-0">
                    <div className="mt-1 md:mt-0 text-slate-500">
                      {isExpanded ? (
                        <ChevronDown className="w-4 h-4 text-indigo-400" />
                      ) : (
                        <ChevronRight className="w-4 h-4 group-hover:text-slate-300 transition" />
                      )}
                    </div>

                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`text-[11px] font-mono px-2 py-0.5 rounded border uppercase tracking-wider font-semibold ${style.bg} ${style.text} ${style.border}`}
                      >
                        {mem.memory_type}
                      </span>

                      {mem.status === 'ACTIVE' && (
                        <span className="flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded bg-emerald-950/40 text-emerald-400 border border-emerald-800/60">
                          <CheckCircle2 className="w-3 h-3" /> ACTIVE
                        </span>
                      )}
                      {mem.status === 'STALE' && (
                        <span className="flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded bg-amber-950/40 text-amber-400 border border-amber-800/60">
                          <Clock className="w-3 h-3" /> STALE
                        </span>
                      )}
                      {mem.status === 'CONFLICTED' && (
                        <span className="flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded bg-rose-950/40 text-rose-400 border border-rose-800/60">
                          <ShieldAlert className="w-3 h-3" /> CONFLICTED
                        </span>
                      )}
                      {mem.status === 'DEPRECATED' && (
                        <span className="flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                          <Archive className="w-3 h-3" /> DEPRECATED
                        </span>
                      )}

                      <span className="text-[11px] font-mono text-slate-500">
                        v{mem.version}
                      </span>
                    </div>

                    <h4 className="text-sm font-semibold text-slate-200 truncate">
                      {mem.title}
                    </h4>
                  </div>

                  {/* Right metadata / actions */}
                  <div className="flex items-center gap-4 text-xs font-mono text-slate-400 self-end md:self-auto">
                    <div className="flex items-center gap-3">
                      <div>
                        <span className="text-slate-500 mr-1">Imp:</span>
                        <span className="text-slate-300 font-semibold">{Math.round(mem.importance * 100)}%</span>
                      </div>
                      <div>
                        <span className="text-slate-500 mr-1">Conf:</span>
                        <span className="text-indigo-400 font-semibold">{Math.round(mem.confidence * 100)}%</span>
                      </div>
                    </div>

                    <div className="flex items-center gap-1 border-l border-slate-800 pl-3">
                      <button
                        title="Copy Memory ID"
                        onClick={(e) => handleCopyId(mem.id, e)}
                        className="p-1.5 hover:bg-slate-800 rounded text-slate-400 hover:text-slate-200 transition"
                      >
                        {copiedId === mem.id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                      </button>

                      <button
                        title="Verify Grounding Against Working Tree"
                        onClick={(e) => handleVerify(mem.id, e)}
                        disabled={actionInProgress === mem.id}
                        className="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-indigo-300 hover:text-indigo-200 rounded text-[11px] font-medium transition flex items-center gap-1"
                      >
                        <RefreshCw className={`w-3 h-3 ${actionInProgress === mem.id ? 'animate-spin' : ''}`} />
                        Verify
                      </button>

                      {mem.status !== 'DEPRECATED' && (
                        <button
                          title="Mark Memory as Deprecated"
                          onClick={(e) => handleDeprecate(mem.id, e)}
                          disabled={actionInProgress === mem.id}
                          className="px-2 py-1 hover:bg-rose-950/40 text-slate-400 hover:text-rose-400 rounded text-[11px] font-medium transition"
                        >
                          Deprecate
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                {/* Summary Banner */}
                <div className="px-4 pb-3 text-xs text-slate-400 font-sans">
                  {mem.summary}
                </div>

                {/* Expanded Drawer */}
                {isExpanded && (
                  <div className="p-4 bg-slate-950/70 border-t border-slate-800 space-y-4">
                    {/* Content */}
                    <div>
                      <div className="text-xs font-mono uppercase tracking-wider text-slate-500 mb-1.5 font-semibold">
                        Detailed Context
                      </div>
                      <div className="p-3.5 bg-slate-900 rounded-lg border border-slate-800/80 text-xs text-slate-300 font-mono whitespace-pre-wrap leading-relaxed">
                        {mem.content}
                      </div>
                    </div>

                    {/* Evidence Grounding */}
                    <div>
                      <div className="text-xs font-mono uppercase tracking-wider text-slate-500 mb-2 font-semibold flex items-center gap-1.5">
                        <GitCommit className="w-3.5 h-3.5 text-indigo-400" /> Grounded Evidences ({mem.evidences?.length || 0})
                      </div>
                      {mem.evidences && mem.evidences.length > 0 ? (
                        <div className="space-y-2">
                          {mem.evidences.map((ev, idx) => (
                            <div
                              key={idx}
                              className="flex flex-col sm:flex-row sm:items-center justify-between p-2.5 bg-slate-900/60 rounded-md border border-slate-800 text-xs font-mono gap-2"
                            >
                              <div className="flex items-center gap-2 truncate">
                                <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[10px]">
                                  {ev.source_type}
                                </span>
                                <span className="text-slate-200 font-medium truncate">
                                  {ev.file_path}
                                </span>
                                {ev.line_start && (
                                  <span className="text-slate-500">
                                    L{ev.line_start}{ev.line_end ? `-${ev.line_end}` : ''}
                                  </span>
                                )}
                              </div>
                              <div className="flex items-center gap-3 text-slate-400 self-end sm:self-auto text-[11px]">
                                {ev.commit_sha && (
                                  <span className="text-slate-500">
                                    sha:{ev.commit_sha.substring(0, 7)}
                                  </span>
                                )}
                                <span className="text-indigo-400">
                                  Conf: {Math.round(ev.confidence * 100)}%
                                </span>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="text-xs text-slate-500 italic p-2 bg-slate-900/40 rounded border border-slate-800">
                          No direct code file evidences attached.
                        </div>
                      )}
                    </div>

                    {/* Metadata Footer */}
                    <div className="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-slate-800/80 text-[11px] font-mono text-slate-500">
                      <div>Created by: <span className="text-slate-400">{mem.created_by}</span> ({new Date(mem.created_at).toLocaleString()})</div>
                      <div>
                        Last verified: <span className="text-slate-400">{mem.last_verified_at ? new Date(mem.last_verified_at).toLocaleString() : 'Never'}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
