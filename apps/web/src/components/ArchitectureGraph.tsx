import React, { useState } from 'react';
import { ArchitectureResponse, ComponentSummary } from '../types';
import { Layers, ArrowRight, X } from 'lucide-react';

interface ArchitectureGraphProps {
  arch: ArchitectureResponse | null;
}

const getEntityTypeBadgeStyle = (type: string | undefined | null): string => {
  switch (type?.toLowerCase()) {
    case 'class':
      return 'bg-blue-500/10 text-blue-400 border border-blue-500/25';
    case 'function':
      return 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/25';
    case 'method':
      return 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/25';
    case 'interface':
      return 'bg-purple-500/10 text-purple-400 border border-purple-500/25';
    case 'model':
      return 'bg-amber-500/10 text-amber-400 border border-amber-500/25';
    case 'module':
      return 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/25';
    case 'config':
      return 'bg-orange-500/10 text-orange-400 border border-orange-500/25';
    case 'config_key':
      return 'bg-sky-500/10 text-sky-400 border border-sky-500/25';
    case 'api':
      return 'bg-rose-500/10 text-rose-400 border border-rose-500/25';
    case 'test':
      return 'bg-lime-500/10 text-lime-400 border border-lime-500/25';
    case 'variable':
      return 'bg-violet-500/10 text-violet-400 border border-violet-500/25';
    default:
      return 'bg-slate-500/10 text-slate-400 border border-slate-500/25';
  }
};

export const ArchitectureGraph: React.FC<ArchitectureGraphProps> = ({ arch }) => {
  const [selectedComponent, setSelectedComponent] = useState<ComponentSummary | null>(null);

  if (!arch || !arch.modules.length) {
    return (
      <div className="p-8 text-center text-slate-400">
        No architecture data available. Run scan to extract code entities.
      </div>
    );
  }

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6 relative">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white tracking-tight">Project Architecture Map</h2>
          <p className="text-xs text-slate-400 font-mono">
            {arch.total_files} files • {arch.total_entities} symbols • {arch.total_relationships} relations • {arch.languages.join(', ')}
          </p>
        </div>
      </div>

      {/* Module Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {arch.modules.map((mod) => (
          <div
            key={mod.module_path}
            className="bg-slate-900/40 rounded-xl border border-slate-800 p-5 space-y-4 hover:border-slate-700 transition-all shadow-sm"
          >
            <div className="flex items-center justify-between border-b border-slate-800/80 pb-3 gap-2">
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <Layers className="w-4 h-4 text-brand-400 shrink-0" />
                <span className="font-bold text-sm text-slate-100 font-mono truncate" title={mod.module_path}>
                  {mod.module_path}
                </span>
              </div>
              <span className="text-[11px] text-slate-400 font-mono shrink-0 whitespace-nowrap">
                {mod.file_count} {mod.file_count === 1 ? 'file' : 'files'} • {mod.entity_count} {mod.entity_count === 1 ? 'symbol' : 'symbols'}
              </span>
            </div>

            <div className="space-y-2">
              {mod.top_level_components.length > 0 ? (
                mod.top_level_components.map((comp) => (
                  <div
                    key={comp.qualified_name}
                    onClick={() => setSelectedComponent(comp)}
                    className="p-2.5 rounded-lg bg-slate-950/60 border border-slate-800/70 hover:border-brand-500/50 hover:bg-slate-900 cursor-pointer transition-all flex items-center justify-between group"
                  >
                    <div className="min-w-0 flex-1 pr-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className={`text-[10px] uppercase font-mono px-1.5 py-0.5 rounded font-semibold shrink-0 ${getEntityTypeBadgeStyle(comp.entity_type)}`}>
                          {comp.entity_type || 'symbol'}
                        </span>
                        <span className="font-semibold text-xs text-slate-200 group-hover:text-brand-400 transition-colors truncate" title={comp.name}>
                          {comp.name}
                        </span>
                      </div>
                      <div className="text-[10px] text-slate-400 font-mono truncate mt-0.5" title={`${comp.file_path}:${comp.line_range[0]}-${comp.line_range[1]}`}>
                        {comp.file_path}:{comp.line_range[0]}-{comp.line_range[1]}
                      </div>
                    </div>
                    <ArrowRight className="w-3.5 h-3.5 text-slate-400 group-hover:text-brand-400 transition-colors shrink-0" />
                  </div>
                ))
              ) : (
                <div className="py-4 text-center text-xs text-slate-500 italic">
                  No top-level code components indexed
                </div>
              )}

              {mod.entity_count > mod.top_level_components.length && (
                <div className="text-[10px] text-slate-500 font-mono text-center pt-1">
                  + {mod.entity_count - mod.top_level_components.length} more {mod.entity_count - mod.top_level_components.length === 1 ? 'entity' : 'entities'} in module
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Detail Drawer */}
      {selectedComponent && (
        <div className="fixed inset-y-0 right-0 w-96 bg-slate-900 border-l border-slate-800 shadow-2xl p-6 z-50 overflow-y-auto space-y-6">
          <div className="flex items-center justify-between border-b border-slate-800 pb-4">
            <div className="min-w-0 flex-1 pr-2">
              <span className={`text-[10px] uppercase font-mono px-2 py-0.5 rounded font-semibold inline-block mb-1.5 ${getEntityTypeBadgeStyle(selectedComponent.entity_type)}`}>
                {selectedComponent.entity_type || 'symbol'}
              </span>
              <h3 className="font-bold text-base text-white truncate" title={selectedComponent.name}>
                {selectedComponent.name}
              </h3>
            </div>
            <button
              onClick={() => setSelectedComponent(null)}
              className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 shrink-0"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="space-y-4 text-xs font-mono">
            <div>
              <div className="text-slate-400 mb-1">Qualified Identifier</div>
              <div className="bg-slate-950 p-2.5 rounded border border-slate-800 text-slate-300 break-all">
                {selectedComponent.qualified_name}
              </div>
            </div>

            <div>
              <div className="text-slate-400 mb-1">File Location</div>
              <div className="bg-slate-950 p-2.5 rounded border border-slate-800 text-slate-300">
                {selectedComponent.file_path} (lines {selectedComponent.line_range[0]}-{selectedComponent.line_range[1]})
              </div>
            </div>

            {selectedComponent.signature && (
              <div>
                <div className="text-slate-400 mb-1">AST Signature</div>
                <pre className="bg-slate-950 p-2.5 rounded border border-slate-800 text-brand-300 overflow-x-auto">
                  {selectedComponent.signature}
                </pre>
              </div>
            )}

            <div>
              <div className="text-slate-400 mb-1">Downstream Dependencies ({selectedComponent.dependencies.length})</div>
              {selectedComponent.dependencies.length ? (
                <div className="space-y-1">
                  {selectedComponent.dependencies.map((dep, i) => (
                    <div key={i} className="p-1.5 rounded bg-slate-950 border border-slate-800 text-slate-300">
                      → {dep}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-slate-400 italic">No external module dependencies</div>
              )}
            </div>

            <div>
              <div className="text-slate-400 mb-1">Upstream Callers & Consumers ({selectedComponent.dependents.length})</div>
              {selectedComponent.dependents.length ? (
                <div className="space-y-1">
                  {selectedComponent.dependents.map((dep, i) => (
                    <div key={i} className="p-1.5 rounded bg-slate-950 border border-slate-800 text-slate-300">
                      ← {dep}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-slate-400 italic">No incoming consumers indexed</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
