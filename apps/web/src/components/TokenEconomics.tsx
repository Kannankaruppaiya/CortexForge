import React, { useEffect, useState } from 'react';
import {
  Coins,
  TrendingDown,
  Cpu,
  Layers,
  Zap,
  DollarSign,
  FileCheck,
} from 'lucide-react';
import { fetchTokenEconomics } from '../api';

interface LayerBreakdown {
  name: string;
  tokens: number;
  pct: number;
  color: string;
}

interface BudgetProfile {
  totalTokens: number;
  label: string;
  description: string;
  layers: LayerBreakdown[];
}

interface TokenEconomicsProps {
  projectId?: string | null;
}

export const TokenEconomics: React.FC<TokenEconomicsProps> = ({ projectId }) => {
  const [selectedBudget, setSelectedBudget] = useState<'small' | 'medium' | 'large'>('medium');
  const [liveData, setLiveData] = useState<any>(null);
  const [_isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let isMounted = true;
    setIsLoading(true);
    setError(null);
    fetchTokenEconomics(projectId)
      .then((data) => {
        if (isMounted && data) {
          setLiveData(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (isMounted) {
          setError(err?.message || 'Failed to fetch token economics metrics.');
          setLiveData(null);
        }
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, [projectId]);

  const referenceModelProfiles: Record<'small' | 'medium' | 'large', BudgetProfile> = {
    small: {
      totalTokens: 2400,
      label: 'Small Budget (Fast / Latency-Optimized)',
      description: 'Optimized for quick bug fixes and targeted symbol lookups.',
      layers: [
        { name: 'L0 Project Identity & Framework', tokens: 180, pct: 7.5, color: 'bg-indigo-500' },
        { name: 'L1 Primary Architecture Graph', tokens: 620, pct: 25.8, color: 'bg-blue-500' },
        { name: 'L2 Code Conventions & Standards', tokens: 300, pct: 12.5, color: 'bg-teal-500' },
        { name: 'L3 Active Architectural Decisions', tokens: 550, pct: 22.9, color: 'bg-emerald-500' },
        { name: 'L4 Failure Post-Mortems', tokens: 450, pct: 18.8, color: 'bg-red-500' },
        { name: 'L5 Durable Lessons Learned', tokens: 300, pct: 12.5, color: 'bg-purple-500' },
      ],
    },
    medium: {
      totalTokens: 8200,
      label: 'Medium Budget (Standard Balanced Task)',
      description: 'Standard working context for feature additions and refactoring.',
      layers: [
        { name: 'L0 Project Identity & Framework', tokens: 350, pct: 4.3, color: 'bg-indigo-500' },
        { name: 'L1 Primary Architecture Graph', tokens: 2400, pct: 29.3, color: 'bg-blue-500' },
        { name: 'L2 Code Conventions & Standards', tokens: 950, pct: 11.6, color: 'bg-teal-500' },
        { name: 'L3 Active Architectural Decisions', tokens: 1850, pct: 22.6, color: 'bg-emerald-500' },
        { name: 'L4 Failure Post-Mortems', tokens: 1450, pct: 17.7, color: 'bg-red-500' },
        { name: 'L5 Durable Lessons Learned', tokens: 1200, pct: 14.6, color: 'bg-purple-500' },
      ],
    },
    large: {
      totalTokens: 16500,
      label: 'Large Budget (Deep Cross-Subsystem Audit)',
      description: 'Maximum depth for complex multi-module redesigns and audits.',
      layers: [
        { name: 'L0 Project Identity & Framework', tokens: 500, pct: 3.0, color: 'bg-indigo-500' },
        { name: 'L1 Primary Architecture Graph', tokens: 5200, pct: 31.5, color: 'bg-blue-500' },
        { name: 'L2 Code Conventions & Standards', tokens: 1800, pct: 10.9, color: 'bg-teal-500' },
        { name: 'L3 Active Architectural Decisions', tokens: 3800, pct: 23.0, color: 'bg-emerald-500' },
        { name: 'L4 Failure Post-Mortems', tokens: 2800, pct: 17.0, color: 'bg-red-500' },
        { name: 'L5 Durable Lessons Learned', tokens: 2400, pct: 14.5, color: 'bg-purple-500' },
      ],
    },
  };

  const [showEstimation, setShowEstimation] = useState(false);
  const profiles = liveData?.profiles || (showEstimation ? referenceModelProfiles : null);
  const activeProfile = profiles ? profiles[selectedBudget] : null;

  const hasLiveData = Boolean(liveData && liveData.savings_pct !== undefined);
  const isMeasured = Boolean(liveData && liveData.has_measured_data);
  const savingsPct = hasLiveData ? liveData.savings_pct : (showEstimation ? 88.5 : null);
  const avgCortexTokens = hasLiveData ? liveData.avg_context_tokens_cortex : (showEstimation ? 2850 : null);
  const avgBaseTokens = hasLiveData ? liveData.avg_context_tokens_baseline : (showEstimation ? 24500 : null);
  const filesCortex = hasLiveData ? liveData.files_explored_cortex : (showEstimation ? 1.2 : null);
  const filesBase = hasLiveData ? liveData.files_explored_baseline : (showEstimation ? 14.6 : null);
  const toolsCortex = hasLiveData ? liveData.tool_calls_cortex : (showEstimation ? 1.4 : null);
  const toolsBase = hasLiveData ? liveData.tool_calls_baseline : (showEstimation ? 6.8 : null);
  const costCortex = hasLiveData ? liveData.cost_per_1k_cortex : (showEstimation ? 4.20 : null);
  const costBase = hasLiveData ? liveData.cost_per_1k_baseline : (showEstimation ? 36.75 : null);
  const costSaved = hasLiveData ? liveData.cost_saved_per_1k : (showEstimation ? 32.55 : null);

  return (
    <div className="space-y-6">
      {error && (
        <div className="p-4 bg-red-950/40 border border-red-800 rounded-xl text-xs font-mono text-red-300">
          ⚠️ <strong>API Error:</strong> {error}
        </div>
      )}

      {/* Overview Banner */}
      <div className="p-5 bg-slate-900/80 rounded-xl border border-slate-800 backdrop-blur flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
            <Coins className="w-4 h-4 text-amber-400" />
            Token Economics & Context Efficiency Scoreboard
          </h3>
          <p className="text-xs text-slate-400 mt-1 max-w-2xl">
            CortexForge reduces context pollution by transforming raw repository discovery into a pre-digested, verified Project Cognitive Model.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {hasLiveData ? (
            <span className={`px-2.5 py-1 text-xs font-mono font-bold rounded-lg flex items-center gap-1.5 ${
              isMeasured
                ? 'bg-emerald-950/80 border border-emerald-800 text-emerald-300'
                : 'bg-indigo-950/80 border border-indigo-800 text-indigo-300'
            }`}>
              <TrendingDown className="w-3.5 h-3.5" />
              {isMeasured ? `${savingsPct}% Live Measured Savings` : `${savingsPct}% Modeled Estimate`}
            </span>
          ) : showEstimation ? (
            <span className="px-2.5 py-1 bg-amber-950/60 border border-amber-800/80 text-amber-300 text-xs font-mono rounded-lg flex items-center gap-1.5">
              Reference Simulation (~88.5% Est.)
            </span>
          ) : (
            <span className="px-2.5 py-1 bg-slate-800 border border-slate-700 text-slate-400 text-xs font-mono rounded-lg">
              No Benchmark Data Yet
            </span>
          )}
        </div>
      </div>

      {/* Notice when viewing offline estimation or empty state */}
      {!hasLiveData && (
        <div className={`p-4 rounded-xl border flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs font-mono ${
          showEstimation
            ? 'bg-amber-950/30 border-amber-800/60 text-amber-300'
            : 'bg-slate-900/60 border-slate-800 text-slate-400'
        }`}>
          <div>
            {showEstimation ? (
              <span>⚠️ <strong>OFFLINE REFERENCE SIMULATION:</strong> Displaying theoretical profile limits from <code>retrieval/composer.py</code>, not empirical measurements.</span>
            ) : (
              <span>ℹ️ <strong>EMPTY STATE:</strong> No benchmark runs recorded for this project yet. Run <code>cortex benchmark</code> to generate live empirical scorecards.</span>
            )}
          </div>
          <button
            onClick={() => setShowEstimation(!showEstimation)}
            className="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded border border-slate-700 whitespace-nowrap transition"
          >
            {showEstimation ? 'Switch to Truthful Empty View' : 'Preview Reference Simulation Model'}
          </button>
        </div>
      )}

      {/* Comparative Head-to-Head Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="p-4 bg-slate-900/60 border border-slate-800 rounded-xl space-y-2">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span>Avg Context Tokens</span>
            <Cpu className="w-4 h-4 text-indigo-400" />
          </div>
          <div className="flex items-baseline gap-2">
            {avgCortexTokens !== null ? (
              <>
                <span className="text-2xl font-bold font-mono text-emerald-400">{avgCortexTokens.toLocaleString()}</span>
                {avgBaseTokens && <span className="text-xs font-mono text-slate-500 line-through">{avgBaseTokens.toLocaleString()}</span>}
              </>
            ) : (
              <span className="text-xl font-bold font-mono text-slate-500 italic">Not measured</span>
            )}
          </div>
          <div className="text-[11px] text-slate-500 font-mono">
            {savingsPct !== null ? `${savingsPct}% reduction per agent turn` : 'Run benchmark to measure'}
          </div>
        </div>

        <div className="p-4 bg-slate-900/60 border border-slate-800 rounded-xl space-y-2">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span>Files Explored</span>
            <FileCheck className="w-4 h-4 text-blue-400" />
          </div>
          <div className="flex items-baseline gap-2">
            {filesCortex !== null ? (
              <>
                <span className="text-2xl font-bold font-mono text-emerald-400">{filesCortex}</span>
                {filesBase && <span className="text-xs font-mono text-slate-500 line-through">{filesBase}</span>}
              </>
            ) : (
              <span className="text-xl font-bold font-mono text-slate-500 italic">Not measured</span>
            )}
          </div>
          <div className="text-[11px] text-slate-500 font-mono">
            {hasLiveData ? `${liveData.files_reduction_pct}% fewer files re-read` : (showEstimation ? '91.8% estimated reduction' : 'Run benchmark to measure')}
          </div>
        </div>

        <div className="p-4 bg-slate-900/60 border border-slate-800 rounded-xl space-y-2">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span>Tool Calls per Task</span>
            <Zap className="w-4 h-4 text-amber-400" />
          </div>
          <div className="flex items-baseline gap-2">
            {toolsCortex !== null ? (
              <>
                <span className="text-2xl font-bold font-mono text-emerald-400">{toolsCortex}</span>
                {toolsBase && <span className="text-xs font-mono text-slate-500 line-through">{toolsBase}</span>}
              </>
            ) : (
              <span className="text-xl font-bold font-mono text-slate-500 italic">Not measured</span>
            )}
          </div>
          <div className="text-[11px] text-slate-500 font-mono">
            {hasLiveData ? `${liveData.tool_calls_reduction_pct}% fewer exploratory calls` : (showEstimation ? '79.4% estimated reduction' : 'Run benchmark to measure')}
          </div>
        </div>

        <div className="p-4 bg-slate-900/60 border border-slate-800 rounded-xl space-y-2">
          <div className="flex items-center justify-between text-xs font-mono text-slate-400">
            <span>Estimated Cost / 1k Tasks</span>
            <DollarSign className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="flex items-baseline gap-2">
            {costCortex !== null ? (
              <>
                <span className="text-2xl font-bold font-mono text-emerald-400">${costCortex.toFixed(2)}</span>
                {costBase && <span className="text-xs font-mono text-slate-500 line-through">${costBase.toFixed(2)}</span>}
              </>
            ) : (
              <span className="text-xl font-bold font-mono text-slate-500 italic">Not measured</span>
            )}
          </div>
          <div className="text-[11px] text-slate-500 font-mono">
            {costSaved !== null ? `$${costSaved.toFixed(2)} saved per 1k runs` : 'Run benchmark to measure'}
          </div>
        </div>
      </div>

      {/* Structured Context Composer Breakdown */}
      <div className="p-5 bg-slate-900/70 border border-slate-800 rounded-xl space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h4 className="text-xs font-mono uppercase tracking-wider text-slate-300 font-semibold flex items-center gap-2">
              <Layers className="w-4 h-4 text-indigo-400" />
              Structured Context Composition by Cognitive Layer
            </h4>
            <p className="text-xs text-slate-500 mt-0.5">
              Simulate how CortexForge's Token Budget Composer fills the prompt budget across L0–L5.
            </p>
          </div>

          {/* Budget Profile Selector */}
          <div className="flex items-center gap-1.5 p-1 bg-slate-950 rounded-lg border border-slate-800 font-mono text-xs">
            {(['small', 'medium', 'large'] as const).map((b) => (
              <button
                key={b}
                onClick={() => setSelectedBudget(b)}
                className={`px-3 py-1 rounded transition uppercase font-semibold ${
                  selectedBudget === b
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {b}
              </button>
            ))}
          </div>
        </div>

        {/* Profile summary */}
        <div className="p-3 bg-slate-950/60 rounded-lg border border-slate-800 flex items-center justify-between text-xs font-mono text-slate-400">
          <div>
            <span className="text-slate-200 font-semibold">{activeProfile.label}:</span>{' '}
            {activeProfile.description}
          </div>
          <div className="text-indigo-400 font-bold">
            Total: {activeProfile.totalTokens.toLocaleString()} tokens
          </div>
        </div>

        {/* Segmented Progress Bar */}
        <div className="space-y-2">
          <div className="w-full h-3 bg-slate-950 rounded-full overflow-hidden flex border border-slate-800">
            {activeProfile.layers.map((l: LayerBreakdown, i: number) => (
              <div
                key={i}
                style={{ width: `${l.pct}%` }}
                className={`${l.color} transition-all duration-300 hover:opacity-80`}
                title={`${l.name}: ${l.tokens} tokens (${l.pct}%)`}
              />
            ))}
          </div>
        </div>

        {/* Breakdown Table */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {activeProfile.layers.map((l: LayerBreakdown, idx: number) => (
            <div
              key={idx}
              className="p-3 bg-slate-950/70 border border-slate-800/80 rounded-lg flex items-center justify-between text-xs font-mono"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className={`w-2.5 h-2.5 rounded-full ${l.color} flex-shrink-0`} />
                <span className="text-slate-300 truncate">{l.name}</span>
              </div>
              <div className="flex items-center gap-2 text-right flex-shrink-0">
                <span className="text-slate-200 font-semibold">{l.tokens} t</span>
                <span className="text-slate-500 text-[11px]">({l.pct}%)</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
