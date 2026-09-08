import React from 'react';
import {
  LayoutDashboard,
  Network,
  BrainCircuit,
  Scale,
  AlertTriangle,
  Zap,
  TrendingUp,
  ActivitySquare,
  ShieldCheck,
  History,
} from 'lucide-react';


export type TabType =
  | 'overview'
  | 'architecture'
  | 'invariants'
  | 'memories'
  | 'decisions'
  | 'failures'
  | 'impact'
  | 'snapshots'
  | 'economics'
  | 'evaluation';

interface NavigationProps {
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;
}

const navItems = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'architecture', label: 'Architecture Graph', icon: Network },
  { id: 'invariants', label: 'Architecture Rules & Invariants', icon: Scale },
  { id: 'memories', label: 'Memory Explorer', icon: BrainCircuit },
  { id: 'decisions', label: 'Decisions (L3)', icon: Scale },
  { id: 'failures', label: 'Failures & Anti-Patterns (L4)', icon: AlertTriangle },
  { id: 'impact', label: 'Change Impact', icon: Zap },
  { id: 'snapshots', label: 'Cognitive Snapshots & Replay', icon: History },
  { id: 'economics', label: 'Token Economics', icon: TrendingUp },
  { id: 'evaluation', label: 'Evaluation & Mutation Harness', icon: ActivitySquare },
];


export const Navigation: React.FC<NavigationProps> = ({ activeTab, setActiveTab }) => {
  return (
    <aside className="w-64 border-r border-slate-800 bg-slate-950 flex flex-col h-screen shrink-0">
      <div className="p-5 border-b border-slate-800 flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-brand-500/10 border border-brand-500/30 flex items-center justify-center text-brand-500 font-bold">
          <ShieldCheck className="w-5 h-5" />
        </div>
        <div>
          <h1 className="font-bold text-slate-100 tracking-tight text-base flex items-center gap-1.5">
            CortexForge
            <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-brand-500/20 text-brand-400 border border-brand-500/30 font-semibold">
              v0.1.0
            </span>
          </h1>
          <p className="text-xs text-slate-400 font-medium truncate">Verified Project Memory</p>
        </div>
      </div>

      <nav className="p-3 space-y-1 flex-1 overflow-y-auto">
        <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider px-3 py-2">
          Project Intelligence
        </div>
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id as TabType)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all text-left ${
                isActive
                  ? 'bg-slate-800/80 text-brand-400 border border-slate-700/60 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900/60'
              }`}
            >
              <Icon className={`w-4 h-4 ${isActive ? 'text-brand-400' : 'text-slate-400'}`} />
              <span className="truncate">{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="p-4 border-t border-slate-800 text-xs text-slate-400 font-mono">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-brand-500 animate-pulse" />
          <span>MCP Server: Active (stdio)</span>
        </div>
      </div>
    </aside>
  );
};
