import React, { useState } from 'react';
import { Memory } from '../types';
import {
  GitCommit,
  CheckCircle2,
  AlertTriangle,
  Lightbulb,
  ShieldCheck,
  ChevronDown,
  ChevronRight,
  Bookmark,
  Bug,
  Calendar,
} from 'lucide-react';

interface DecisionsAndFailuresProps {
  memories: Memory[];
  onRefresh: () => void;
}

export const DecisionsAndFailures: React.FC<DecisionsAndFailuresProps> = ({
  memories,
}) => {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Separate memories by category
  const decisions = memories.filter(
    (m) => m.memory_type === 'DECISION' || m.memory_type === 'ARCHITECTURE_PATTERN'
  );
  const failures = memories.filter(
    (m) => m.memory_type === 'FAILURE' || m.memory_type === 'LESSON'
  );
  const constraints = memories.filter((m) => m.memory_type === 'CONSTRAINT');

  return (
    <div className="space-y-6">
      {/* Top Banner explaining the cognitive value */}
      <div className="p-4 bg-gradient-to-r from-slate-900 via-slate-900 to-indigo-950/40 rounded-xl border border-slate-800 flex flex-col md:flex-row md:items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
            <Bookmark className="w-4 h-4 text-indigo-400" />
            Architectural Guardrails & Episodic Immune System
          </h3>
          <p className="text-xs text-slate-400 mt-1 max-w-2xl">
            CortexForge preserves historical architectural decisions (ADRs) and episodic failure post-mortems so AI coding agents do not repeatedly violate patterns or re-introduce previously failed solutions.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs font-mono">
          <div className="px-3 py-1.5 bg-slate-950 rounded-lg border border-slate-800 text-slate-300">
            <span className="text-emerald-400 font-bold mr-1">{decisions.length}</span> Decisions
          </div>
          <div className="px-3 py-1.5 bg-slate-950 rounded-lg border border-slate-800 text-slate-300">
            <span className="text-red-400 font-bold mr-1">{failures.length}</span> Failures & Lessons
          </div>
          <div className="px-3 py-1.5 bg-slate-950 rounded-lg border border-slate-800 text-slate-300">
            <span className="text-rose-400 font-bold mr-1">{constraints.length}</span> Constraints
          </div>
        </div>
      </div>

      {/* Two Column Layout: Decisions on Left, Failures & Lessons on Right */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left Column: Architectural Decisions */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-emerald-400" /> Architectural Decisions (ADRs)
            </h4>
            <span className="text-[11px] font-mono text-slate-500">{decisions.length} active</span>
          </div>

          <div className="space-y-3">
            {decisions.length === 0 ? (
              <div className="p-8 text-center bg-slate-900/40 rounded-xl border border-slate-800/80">
                <ShieldCheck className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                <div className="text-xs text-slate-400">No architectural decisions recorded yet.</div>
                <div className="text-[11px] text-slate-500 mt-1">
                  Use `cortex memory create --type DECISION` or MCP tools to establish project decisions.
                </div>
              </div>
            ) : (
              decisions.map((dec) => {
                const isExpanded = expandedId === dec.id;
                return (
                  <div
                    key={dec.id}
                    className={`bg-slate-900/90 border rounded-xl overflow-hidden transition-all ${
                      isExpanded ? 'border-emerald-500/60 shadow-lg shadow-emerald-950/20' : 'border-slate-800 hover:border-slate-700'
                    }`}
                  >
                    <div
                      onClick={() => setExpandedId(isExpanded ? null : dec.id)}
                      className="p-4 cursor-pointer flex items-start justify-between gap-3 select-none"
                    >
                      <div className="flex items-start gap-2.5 flex-1 min-w-0">
                        <div className="mt-0.5 text-slate-500">
                          {isExpanded ? (
                            <ChevronDown className="w-4 h-4 text-emerald-400" />
                          ) : (
                            <ChevronRight className="w-4 h-4" />
                          )}
                        </div>
                        <div className="space-y-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-950/50 text-emerald-400 border border-emerald-800/60 font-semibold">
                              {dec.memory_type}
                            </span>
                            <span className="text-[10px] font-mono text-slate-500">v{dec.version}</span>
                            <span className="text-[10px] font-mono text-emerald-400/80">
                              Conf: {Math.round(dec.confidence * 100)}%
                            </span>
                          </div>
                          <h5 className="text-sm font-semibold text-slate-200 truncate">{dec.title}</h5>
                          <p className="text-xs text-slate-400 line-clamp-2">{dec.summary}</p>
                        </div>
                      </div>
                      <div className="text-right flex-shrink-0">
                        <span className="inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-950/40 text-emerald-400 border border-emerald-800/60">
                          <CheckCircle2 className="w-3 h-3" /> {dec.status}
                        </span>
                      </div>
                    </div>

                    {isExpanded && (
                      <div className="p-4 bg-slate-950/80 border-t border-slate-800 space-y-3 text-xs">
                        <div>
                          <div className="text-[11px] font-mono uppercase tracking-wider text-slate-500 font-semibold mb-1">
                            Rationale & Architecture Contract
                          </div>
                          <div className="p-3 bg-slate-900 rounded border border-slate-800 text-slate-300 font-mono whitespace-pre-wrap leading-relaxed">
                            {dec.content}
                          </div>
                        </div>

                        {dec.evidences && dec.evidences.length > 0 && (
                          <div>
                            <div className="text-[11px] font-mono uppercase tracking-wider text-slate-500 font-semibold mb-1 flex items-center gap-1">
                              <GitCommit className="w-3 h-3 text-indigo-400" /> Evidence Grounding
                            </div>
                            <div className="space-y-1 font-mono">
                              {dec.evidences.map((ev, i) => (
                                <div key={i} className="p-2 bg-slate-900/60 rounded border border-slate-800 text-slate-400 flex items-center justify-between">
                                  <span>{ev.file_path} {ev.line_start ? `(L${ev.line_start})` : ''}</span>
                                  <span className="text-indigo-400 text-[10px]">Conf: {Math.round(ev.confidence * 100)}%</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[11px] font-mono text-slate-500">
                          <span className="flex items-center gap-1">
                            <Calendar className="w-3 h-3" /> {new Date(dec.created_at).toLocaleDateString()}
                          </span>
                          <span>ID: {dec.id.substring(0, 8)}...</span>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right Column: Failures & Lessons */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-2">
              <Bug className="w-4 h-4 text-red-400" /> Failure Post-Mortems & Lessons
            </h4>
            <span className="text-[11px] font-mono text-slate-500">{failures.length} recorded</span>
          </div>

          <div className="space-y-3">
            {failures.length === 0 ? (
              <div className="p-8 text-center bg-slate-900/40 rounded-xl border border-slate-800/80">
                <Lightbulb className="w-8 h-8 text-slate-600 mx-auto mb-2" />
                <div className="text-xs text-slate-400">No episodic failures or lessons recorded yet.</div>
                <div className="text-[11px] text-slate-500 mt-1">
                  When an agent fixes a bug, CortexForge records the root cause and synthesizes durable lessons.
                </div>
              </div>
            ) : (
              failures.map((fail) => {
                const isExpanded = expandedId === fail.id;
                const isLesson = fail.memory_type === 'LESSON';

                return (
                  <div
                    key={fail.id}
                    className={`bg-slate-900/90 border rounded-xl overflow-hidden transition-all ${
                      isExpanded
                        ? isLesson
                          ? 'border-purple-500/60 shadow-lg shadow-purple-950/20'
                          : 'border-red-500/60 shadow-lg shadow-red-950/20'
                        : 'border-slate-800 hover:border-slate-700'
                    }`}
                  >
                    <div
                      onClick={() => setExpandedId(isExpanded ? null : fail.id)}
                      className="p-4 cursor-pointer flex items-start justify-between gap-3 select-none"
                    >
                      <div className="flex items-start gap-2.5 flex-1 min-w-0">
                        <div className="mt-0.5 text-slate-500">
                          {isExpanded ? (
                            <ChevronDown className={`w-4 h-4 ${isLesson ? 'text-purple-400' : 'text-red-400'}`} />
                          ) : (
                            <ChevronRight className="w-4 h-4" />
                          )}
                        </div>
                        <div className="space-y-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span
                              className={`text-[10px] font-mono px-1.5 py-0.5 rounded font-semibold border ${
                                isLesson
                                  ? 'bg-purple-950/50 text-purple-400 border-purple-800/60'
                                  : 'bg-red-950/50 text-red-400 border-red-800/60'
                              }`}
                            >
                              {fail.memory_type}
                            </span>
                            <span className="text-[10px] font-mono text-slate-500">v{fail.version}</span>
                            <span className="text-[10px] font-mono text-slate-400">
                              Imp: {Math.round(fail.importance * 100)}%
                            </span>
                          </div>
                          <h5 className="text-sm font-semibold text-slate-200 truncate">{fail.title}</h5>
                          <p className="text-xs text-slate-400 line-clamp-2">{fail.summary}</p>
                        </div>
                      </div>
                      <div className="text-right flex-shrink-0">
                        <span className="inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                          {isLesson ? <Lightbulb className="w-3 h-3 text-purple-400" /> : <AlertTriangle className="w-3 h-3 text-red-400" />}
                          {fail.status}
                        </span>
                      </div>
                    </div>

                    {isExpanded && (
                      <div className="p-4 bg-slate-950/80 border-t border-slate-800 space-y-3 text-xs">
                        <div>
                          <div className="text-[11px] font-mono uppercase tracking-wider text-slate-500 font-semibold mb-1">
                            {isLesson ? 'Durable Consolidated Rule' : 'Root Cause & Attempted Fix'}
                          </div>
                          <div className="p-3 bg-slate-900 rounded border border-slate-800 text-slate-300 font-mono whitespace-pre-wrap leading-relaxed">
                            {fail.content}
                          </div>
                        </div>

                        {fail.evidences && fail.evidences.length > 0 && (
                          <div>
                            <div className="text-[11px] font-mono uppercase tracking-wider text-slate-500 font-semibold mb-1 flex items-center gap-1">
                              <GitCommit className="w-3 h-3 text-indigo-400" /> Affected Code Sites
                            </div>
                            <div className="space-y-1 font-mono">
                              {fail.evidences.map((ev, i) => (
                                <div key={i} className="p-2 bg-slate-900/60 rounded border border-slate-800 text-slate-400 flex items-center justify-between">
                                  <span>{ev.file_path} {ev.line_start ? `(L${ev.line_start})` : ''}</span>
                                  <span className="text-indigo-400 text-[10px]">Conf: {Math.round(ev.confidence * 100)}%</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between text-[11px] font-mono text-slate-500">
                          <span className="flex items-center gap-1">
                            <Calendar className="w-3 h-3" /> {new Date(fail.created_at).toLocaleDateString()}
                          </span>
                          <span>ID: {fail.id.substring(0, 8)}...</span>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
