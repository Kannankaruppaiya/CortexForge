import React, { useState } from 'react';
import { checkImpact } from '../api';
import { ChangeImpactReport } from '../types';
import {
  Flame,
  AlertTriangle,
  FileCode,
  Users,
  ShieldAlert,
  Clock,
  Sparkles,
  ArrowRight,
} from 'lucide-react';

interface ChangeImpactProps {
  projectId: string;
}

export const ChangeImpact: React.FC<ChangeImpactProps> = ({ projectId }) => {
  const [fileInput, setFileInput] = useState('src/cortexforge/core/db.py\nsrc/cortexforge/core/models.py');
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [report, setReport] = useState<ChangeImpactReport | null>(null);

  const handleAnalyze = async () => {
    const files = fileInput
      .split('\n')
      .map((f) => f.trim())
      .filter((f) => f.length > 0);

    if (files.length === 0) return;

    setIsAnalyzing(true);
    const res = await checkImpact(projectId, files);
    setReport(res);
    setIsAnalyzing(false);
  };

  const getRiskLevel = (r: ChangeImpactReport) => {
    const score =
      r.critical_constraints.length * 3 +
      r.warnings.length * 2 +
      r.memories_flagged_stale.length * 1.5 +
      r.affected_dependents.length;

    if (score >= 10 || r.critical_constraints.length > 0) {
      return { level: 'HIGH RISK', color: 'text-red-400', bg: 'bg-red-950/40', border: 'border-red-800' };
    }
    if (score >= 4) {
      return { level: 'MEDIUM RISK', color: 'text-amber-400', bg: 'bg-amber-950/40', border: 'border-amber-800' };
    }
    return { level: 'LOW RISK', color: 'text-emerald-400', bg: 'bg-emerald-950/40', border: 'border-emerald-800' };
  };

  return (
    <div className="space-y-6">
      {/* Header card */}
      <div className="p-5 bg-slate-900/80 rounded-xl border border-slate-800 backdrop-blur">
        <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
          <Flame className="w-4 h-4 text-orange-400" />
          Semantic Change Impact & Blast Radius Simulator
        </h3>
        <p className="text-xs text-slate-400 mt-1 max-w-2xl">
          Simulate prospective code edits before applying them. CortexForge traverses the dependency graph to predict caller invalidation, flagged stale memories, and violations of active architectural constraints.
        </p>

        <div className="mt-4 space-y-2">
          <label className="block text-xs font-mono text-slate-400 uppercase tracking-wider">
            Files to Modify (one relative path per line):
          </label>
          <textarea
            rows={3}
            value={fileInput}
            onChange={(e) => setFileInput(e.target.value)}
            placeholder="src/path/to/file.py&#10;src/another/module.py"
            className="w-full p-3 bg-slate-950 border border-slate-700/80 rounded-lg text-xs font-mono text-slate-200 placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition leading-relaxed"
          />

          <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
            <div className="flex items-center gap-2 text-xs font-mono text-slate-500">
              <span>Quick Presets:</span>
              <button
                type="button"
                onClick={() => setFileInput('src/cortexforge/core/db.py\nsrc/cortexforge/core/models.py')}
                className="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 rounded text-slate-300 transition"
              >
                Core DB
              </button>
              <button
                type="button"
                onClick={() => setFileInput('src/cortexforge/code_intelligence/scanner.py')}
                className="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 rounded text-slate-300 transition"
              >
                Scanner
              </button>
              <button
                type="button"
                onClick={() => setFileInput('src/cortexforge/memory/service.py')}
                className="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 rounded text-slate-300 transition"
              >
                Memory Engine
              </button>
            </div>

            <button
              type="button"
              onClick={handleAnalyze}
              disabled={isAnalyzing}
              className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-indigo-950/40 transition disabled:opacity-50"
            >
              <Sparkles className={`w-3.5 h-3.5 ${isAnalyzing ? 'animate-spin' : ''}`} />
              {isAnalyzing ? 'Analyzing Blast Radius...' : 'Simulate Change Impact'}
            </button>
          </div>
        </div>
      </div>

      {/* Analysis Results */}
      {report && (
        <div className="space-y-6">
          {/* Top Risk Scoreboard */}
          {(() => {
            const risk = getRiskLevel(report);
            return (
              <div className={`p-4 rounded-xl border flex flex-col sm:flex-row sm:items-center justify-between gap-4 ${risk.bg} ${risk.border}`}>
                <div className="flex items-center gap-3">
                  <AlertTriangle className={`w-6 h-6 ${risk.color}`} />
                  <div>
                    <div className="text-xs font-mono uppercase tracking-wider text-slate-400 font-semibold">
                      Predicted Change Risk
                    </div>
                    <div className={`text-base font-bold font-mono ${risk.color}`}>
                      {risk.level}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-4 text-xs font-mono text-slate-300">
                  <div>
                    <span className="text-slate-500 mr-1">Direct Entities:</span>
                    <span className="font-bold text-slate-200">{report.directly_changed_entities.length}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 mr-1">Blast Radius (Callers):</span>
                    <span className="font-bold text-amber-400">{report.affected_dependents.length}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 mr-1">Stale Memories:</span>
                    <span className="font-bold text-rose-400">{report.memories_flagged_stale.length}</span>
                  </div>
                </div>
              </div>
            );
          })()}

          {/* Critical Warnings / Constraints if any */}
          {report.critical_constraints.length > 0 && (
            <div className="p-4 bg-rose-950/30 rounded-xl border border-rose-800/80 space-y-2">
              <h4 className="text-xs font-mono uppercase tracking-wider text-rose-400 font-bold flex items-center gap-2">
                <ShieldAlert className="w-4 h-4" /> Architectural Constraints at Risk
              </h4>
              <ul className="space-y-1.5 text-xs text-rose-300 font-mono">
                {report.critical_constraints.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 bg-rose-950/50 p-2 rounded border border-rose-900/60">
                    <ArrowRight className="w-3.5 h-3.5 mt-0.5 text-rose-400 flex-shrink-0" />
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.warnings.length > 0 && (
            <div className="p-4 bg-amber-950/30 rounded-xl border border-amber-800/80 space-y-2">
              <h4 className="text-xs font-mono uppercase tracking-wider text-amber-400 font-bold flex items-center gap-2">
                <AlertTriangle className="w-4 h-4" /> Known Failure Intersections
              </h4>
              <ul className="space-y-1.5 text-xs text-amber-300 font-mono">
                {report.warnings.map((w, i) => (
                  <li key={i} className="flex items-start gap-2 bg-amber-950/50 p-2 rounded border border-amber-900/60">
                    <ArrowRight className="w-3.5 h-3.5 mt-0.5 text-amber-400 flex-shrink-0" />
                    <span>{w}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 3-Column Detailed Breakdown */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {/* Directly Changed Entities */}
            <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-3">
              <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-2">
                <FileCode className="w-4 h-4 text-indigo-400" /> Direct Entities ({report.directly_changed_entities.length})
              </h4>
              <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1">
                {report.directly_changed_entities.length === 0 ? (
                  <div className="text-xs text-slate-500 italic">No AST entities matched in changed files.</div>
                ) : (
                  report.directly_changed_entities.map((e, idx) => (
                    <div key={idx} className="p-2 bg-slate-950 rounded border border-slate-800/80 text-xs font-mono text-slate-300 truncate">
                      {e}
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Affected Dependents (Blast Radius) */}
            <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-3">
              <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-2">
                <Users className="w-4 h-4 text-amber-400" /> Callers in Blast Radius ({report.affected_dependents.length})
              </h4>
              <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1">
                {report.affected_dependents.length === 0 ? (
                  <div className="text-xs text-slate-500 italic">No upstream callers identified.</div>
                ) : (
                  report.affected_dependents.map((d, idx) => (
                    <div key={idx} className="p-2 bg-slate-950 rounded border border-slate-800/80 text-xs font-mono text-amber-300 truncate">
                      {d}
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Flagged Stale Memories */}
            <div className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 space-y-3">
              <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-2">
                <Clock className="w-4 h-4 text-rose-400" /> Stale Memory Flags ({report.memories_flagged_stale.length})
              </h4>
              <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1">
                {report.memories_flagged_stale.length === 0 ? (
                  <div className="text-xs text-slate-500 italic">No existing memories invalidated by this change.</div>
                ) : (
                  report.memories_flagged_stale.map((m, idx) => (
                    <div key={idx} className="p-2 bg-slate-950 rounded border border-slate-800/80 text-xs font-mono text-rose-300 truncate">
                      {m}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
