import React, { useEffect, useState } from 'react';
import { fetchProvenance } from '../api';
import { ProvenanceTrace } from '../types';
import {
  X,
  ShieldCheck,
  GitCommit,
  Code2,
  History,
  CheckCircle2,
  FileCode,
  Loader2,
  HelpCircle,
} from 'lucide-react';


interface ProvenanceModalProps {
  memoryId: string | null;
  onClose: () => void;
}

export const ProvenanceModal: React.FC<ProvenanceModalProps> = ({ memoryId, onClose }) => {
  const [trace, setTrace] = useState<ProvenanceTrace | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!memoryId) return;

    let mounted = true;
    async function load() {
      setLoading(true);
      const data = await fetchProvenance(memoryId!);
      if (mounted) {
        setTrace(data);
        setLoading(false);
      }
    }
    load();

    return () => {
      mounted = false;
    };
  }, [memoryId]);

  if (!memoryId) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-3xl max-h-[85vh] bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl flex flex-col overflow-hidden">
        {/* Modal Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between bg-slate-950/60">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-100 flex items-center gap-2">
                Memory Provenance Inspector
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-950/60 text-indigo-300 border border-indigo-800/60">
                  Causal Chain
                </span>
              </h3>
              <p className="text-xs text-slate-400 font-mono truncate max-w-md">
                ID: {memoryId}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 overflow-y-auto space-y-6 flex-1 text-xs">
          {loading ? (
            <div className="py-16 flex flex-col items-center justify-center gap-3 text-slate-400 font-mono">
              <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
              <span>Tracing evidentiary causality graph...</span>
            </div>
          ) : !trace ? (
            <div className="py-12 text-center text-slate-500 font-mono">
              Unable to load provenance report for this memory.
            </div>
          ) : (
            <>
              {/* Memory Header Card */}
              <div className="p-4 bg-slate-950/80 rounded-xl border border-slate-800 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-slate-200 text-sm">{trace.title}</span>
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded bg-indigo-950 text-indigo-300 border border-indigo-800 font-mono text-[10px]">
                      {trace.layer}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700 font-mono text-[10px]">
                      {trace.status}
                    </span>
                    <span className="px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800 font-mono text-[10px]">
                      Conf: {Math.round(trace.confidence * 100)}%
                    </span>
                  </div>
                </div>
              </div>

              {/* Core Question: Why does CortexForge believe this? */}
              <div className="p-4 bg-indigo-950/20 rounded-xl border border-indigo-900/40 space-y-2">
                <div className="text-xs font-semibold text-indigo-300 flex items-center gap-1.5">
                  <HelpCircle className="w-4 h-4 text-indigo-400" />
                  Why does CortexForge believe this?
                </div>
                <p className="text-slate-300 leading-relaxed font-sans">
                  {trace.why_cortexforge_believes_this}
                </p>
              </div>

              {/* Evidences */}
              <div className="space-y-2">
                <div className="font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1.5">
                  <FileCode className="w-3.5 h-3.5 text-indigo-400" />
                  Code Evidences ({trace.evidences.length})
                </div>
                {trace.evidences.length === 0 ? (
                  <div className="text-slate-500 italic p-3 bg-slate-950/40 rounded-lg border border-slate-800">
                    No direct code snippet evidences recorded.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {trace.evidences.map((ev, i) => (
                      <div
                        key={i}
                        className="p-3 bg-slate-950/60 rounded-xl border border-slate-800 flex items-center justify-between font-mono"
                      >
                        <div className="flex items-center gap-2 truncate">
                          <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px]">
                            {ev.source_type}
                          </span>
                          <span className="text-slate-200 font-medium truncate">
                            {ev.file_path}
                            {ev.line_start && `:${ev.line_start}${ev.line_end ? `-${ev.line_end}` : ''}`}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          {ev.commit_sha && (
                            <span className="text-[10px] text-slate-400 bg-slate-800 px-1.5 py-0.5 rounded">
                              {ev.commit_sha.substring(0, 7)}
                            </span>
                          )}
                          <span className="text-[10px] text-emerald-400 bg-emerald-950/40 border border-emerald-800/50 px-1.5 py-0.5 rounded flex items-center gap-1">
                            <CheckCircle2 className="w-2.5 h-2.5" />
                            {ev.verification_status}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Anchored Code Symbols */}
              {trace.symbols && trace.symbols.length > 0 && (
                <div className="space-y-2">
                  <div className="font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1.5">
                    <Code2 className="w-3.5 h-3.5 text-blue-400" />
                    Anchored Code Symbols ({trace.symbols.length})
                  </div>
                  <div className="space-y-2">
                    {trace.symbols.map((sym, i) => (
                      <div
                        key={i}
                        className="p-3 bg-slate-950/60 rounded-xl border border-slate-800 font-mono"
                      >
                        <div className="text-blue-300 font-semibold">{sym.name}</div>
                        <div className="text-slate-400 text-[11px] truncate">{sym.qualified_name}</div>
                        {sym.signature && (
                          <div className="text-slate-500 text-[10px] mt-1 truncate">
                            {sym.signature}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Associated Commits */}
              {trace.commits && trace.commits.length > 0 && (
                <div className="space-y-2">
                  <div className="font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1.5">
                    <GitCommit className="w-3.5 h-3.5 text-purple-400" />
                    Linked Commits ({trace.commits.length})
                  </div>
                  <div className="flex flex-wrap gap-2 font-mono text-[11px]">
                    {trace.commits.map((sha, i) => (
                      <span
                        key={i}
                        className="px-2 py-1 rounded-lg bg-slate-950 border border-slate-800 text-purple-300 flex items-center gap-1.5"
                      >
                        <GitCommit className="w-3 h-3" />
                        {sha.substring(0, 10)}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* State Transition Audit Trail */}
              {trace.versions && trace.versions.length > 0 && (
                <div className="space-y-2">
                  <div className="font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-1.5">
                    <History className="w-3.5 h-3.5 text-amber-400" />
                    State Transitions & Version Changelog ({trace.versions.length})
                  </div>
                  <div className="space-y-2 font-mono">
                    {trace.versions.map((ver, i) => (
                      <div
                        key={i}
                        className="p-3 bg-slate-950/60 rounded-xl border border-slate-800 flex items-center justify-between"
                      >
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-bold text-amber-400">v{ver.version}</span>
                            {ver.old_state && ver.new_state && (
                              <span className="text-slate-400 text-[10px]">
                                {ver.old_state} → <strong className="text-slate-200">{ver.new_state}</strong>
                              </span>
                            )}
                          </div>
                          <div className="text-slate-300 text-[11px] mt-0.5">
                            {ver.reason || 'Content revised'}
                          </div>
                        </div>
                        {ver.actor && (
                          <div className="text-[10px] text-slate-500">
                            actor: {ver.actor}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* Modal Footer */}
        <div className="p-4 border-t border-slate-800 bg-slate-950/60 flex items-center justify-between font-mono text-xs text-slate-500">
          <span>Grounding Invariant: Deterministic Causal Provenance</span>
          <button
            onClick={onClose}
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg transition"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
