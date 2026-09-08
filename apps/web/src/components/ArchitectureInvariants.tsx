import React, { useEffect, useState } from 'react';
import {
  fetchArchitectureRules,
  createArchitectureRule,
  fetchArchitectureViolations,
} from '../api';
import { ArchitectureRule, RuleViolation } from '../types';
import {
  ShieldAlert,
  Plus,
  AlertOctagon,
  CheckCircle2,
  RefreshCw,
  Scale,
} from 'lucide-react';


interface ArchitectureInvariantsProps {
  projectId: string | null;
}

export const ArchitectureInvariants: React.FC<ArchitectureInvariantsProps> = ({
  projectId,
}) => {
  const [rules, setRules] = useState<ArchitectureRule[]>([]);
  const [violations, setViolations] = useState<RuleViolation[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);

  // Form state
  const [ruleName, setRuleName] = useState('');
  const [description, setDescription] = useState('');
  const [srcPattern, setSrcPattern] = useState('');
  const [tgtPattern, setTgtPattern] = useState('');
  const [severity, setSeverity] = useState<'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'>('ERROR');

  const loadData = async () => {
    if (!projectId) return;
    setLoading(true);
    const [rList, vList] = await Promise.all([
      fetchArchitectureRules(projectId),
      fetchArchitectureViolations(projectId),
    ]);
    setRules(rList);
    setViolations(vList);
    setLoading(false);
  };

  useEffect(() => {
    loadData();
  }, [projectId]);

  const handleCreateRule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!projectId || !ruleName || !srcPattern || !tgtPattern) return;

    await createArchitectureRule(projectId, {
      rule_name: ruleName,
      description,
      forbidden_source_pattern: srcPattern,
      forbidden_target_pattern: tgtPattern,
      severity,
    });

    setShowAddModal(false);
    setRuleName('');
    setDescription('');
    setSrcPattern('');
    setTgtPattern('');
    await loadData();
  };

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="p-5 bg-gradient-to-r from-slate-900 via-slate-900 to-indigo-950/40 rounded-xl border border-slate-800 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
            <Scale className="w-4 h-4 text-indigo-400" />
            Architecture Invariant Boundary Engine
          </h3>
          <p className="text-xs text-slate-400 mt-1 max-w-2xl">
            Enforces structural boundary invariants across code graph relationships (e.g. controllers must not access database directly, domain must not import UI). Intercepts violations before merging.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={loadData}
            disabled={loading}
            className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-mono transition"
            title="Refresh Invariants"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => setShowAddModal(true)}
            disabled={!projectId}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-mono font-medium transition shadow-sm"
          >
            <Plus className="w-3.5 h-3.5" />
            Add Boundary Rule
          </button>
        </div>
      </div>

      {/* Rule Violations Section */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-2">
            <AlertOctagon className="w-4 h-4 text-rose-400" />
            Detected Boundary Violations ({violations.length})
          </h4>
          <span className="text-xs font-mono text-slate-500">
            {violations.length === 0 ? 'Clean Boundaries' : 'Action Required'}
          </span>
        </div>

        {violations.length === 0 ? (
          <div className="p-6 bg-slate-900/40 border border-slate-800 rounded-xl flex items-center gap-3 text-xs font-mono text-emerald-400">
            <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
            <span>All architectural boundary invariants satisfied. No forbidden dependencies detected in graph.</span>
          </div>
        ) : (
          <div className="space-y-2">
            {violations.map((v) => (
              <div
                key={v.id}
                className="p-4 bg-rose-950/20 border border-rose-900/50 rounded-xl text-xs space-y-1"
              >
                <div className="flex items-center justify-between font-mono">
                  <span className="px-2 py-0.5 rounded bg-rose-950 text-rose-300 border border-rose-800 text-[10px] font-bold">
                    VIOLATION
                  </span>
                  {v.commit_sha && (
                    <span className="text-slate-500 text-[10px]">
                      Commit: {v.commit_sha.substring(0, 8)}
                    </span>
                  )}
                </div>
                <div className="text-slate-200 font-sans leading-relaxed pt-1">
                  {v.violation_details}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Active Rules List */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-indigo-400" />
            Configured Architecture Rules ({rules.length})
          </h4>
        </div>

        {rules.length === 0 ? (
          <div className="p-8 text-center bg-slate-900/40 rounded-xl border border-slate-800 font-mono text-xs text-slate-500">
            No architecture rules defined for this project yet. Click 'Add Boundary Rule' to define invariants.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {rules.map((r) => (
              <div
                key={r.id}
                className="p-4 bg-slate-900/80 border border-slate-800 rounded-xl space-y-3 font-mono text-xs"
              >
                <div className="flex items-center justify-between">
                  <span className="font-bold text-slate-200">{r.rule_name}</span>
                  <span className={`px-2 py-0.5 rounded border text-[10px] font-semibold ${
                    r.severity === 'CRITICAL' || r.severity === 'ERROR'
                      ? 'bg-rose-950/60 text-rose-300 border-rose-800'
                      : 'bg-amber-950/60 text-amber-300 border-amber-800'
                  }`}>
                    {r.severity}
                  </span>
                </div>
                <p className="text-slate-400 text-[11px] font-sans">
                  {r.description || 'No description provided'}
                </p>
                <div className="p-2.5 bg-slate-950 rounded-lg border border-slate-800 space-y-1 text-[11px]">
                  <div>
                    <span className="text-slate-500">Source Pattern:</span>{' '}
                    <span className="text-indigo-300 font-semibold">{r.forbidden_source_pattern}</span>
                  </div>
                  <div>
                    <span className="text-slate-500">Forbidden Target:</span>{' '}
                    <span className="text-rose-300 font-semibold">{r.forbidden_target_pattern}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add Rule Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm">
          <div className="w-full max-w-lg bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl overflow-hidden p-6 space-y-4">
            <h3 className="text-sm font-bold text-slate-100 flex items-center gap-2">
              <Plus className="w-4 h-4 text-indigo-400" />
              Define Architectural Invariant Boundary Rule
            </h3>
            <form onSubmit={handleCreateRule} className="space-y-4 text-xs font-mono">
              <div>
                <label className="block text-slate-400 mb-1">Rule Name</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. No direct database access from web controllers"
                  value={ruleName}
                  onChange={(e) => setRuleName(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-slate-400 mb-1">Description / Rationale</label>
                <textarea
                  rows={2}
                  placeholder="Web controllers must access database exclusively through domain service abstractions."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-400 mb-1">Forbidden Source Pattern</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. apps/web/*"
                    value={srcPattern}
                    onChange={(e) => setSrcPattern(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500"
                  />
                </div>
                <div>
                  <label className="block text-slate-400 mb-1">Forbidden Target Pattern</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. infra/db/*"
                    value={tgtPattern}
                    onChange={(e) => setTgtPattern(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500"
                  />
                </div>
              </div>

              <div>
                <label className="block text-slate-400 mb-1">Severity</label>
                <select
                  value={severity}
                  onChange={(e) => setSeverity(e.target.value as any)}
                  className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500 cursor-pointer"
                >
                  <option value="CRITICAL">CRITICAL (Blocks agent merges)</option>
                  <option value="ERROR">ERROR (Flagged violation)</option>
                  <option value="WARNING">WARNING (Advisory check)</option>
                  <option value="INFO">INFO</option>
                </select>
              </div>

              <div className="flex items-center justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-semibold transition"
                >
                  Save Invariant Rule
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
