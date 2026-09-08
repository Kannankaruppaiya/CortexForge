import React, { useEffect, useState } from 'react';
import { fetchSnapshots, takeSnapshot, replaySnapshot } from '../api';
import { CognitiveSnapshot } from '../types';
import {
  Camera,
  History,
  RotateCcw,
  GitCommit,
  Layers,
  Loader2,
  Play,
} from 'lucide-react';


interface CognitiveSnapshotsProps {
  projectId: string | null;
}

export const CognitiveSnapshots: React.FC<CognitiveSnapshotsProps> = ({ projectId }) => {
  const [snapshots, setSnapshots] = useState<CognitiveSnapshot[]>([]);
  const [loading, setLoading] = useState(false);
  const [commitShaInput, setCommitShaInput] = useState('');
  const [isTaking, setIsTaking] = useState(false);
  const [replayResult, setReplayResult] = useState<any | null>(null);
  const [replayingCommit, setReplayingCommit] = useState<string | null>(null);

  const loadSnapshots = async () => {
    if (!projectId) return;
    setLoading(true);
    const list = await fetchSnapshots(projectId);
    setSnapshots(list);
    setLoading(false);
  };

  useEffect(() => {
    loadSnapshots();
  }, [projectId]);

  const handleTakeSnapshot = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!projectId || !commitShaInput.trim()) return;

    setIsTaking(true);
    await takeSnapshot(projectId, commitShaInput.trim());
    setCommitShaInput('');
    setIsTaking(false);
    await loadSnapshots();
  };

  const handleReplay = async (sha: string) => {
    if (!projectId) return;
    setReplayingCommit(sha);
    const result = await replaySnapshot(projectId, sha);
    setReplayResult(result);
    setReplayingCommit(null);
  };

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="p-5 bg-gradient-to-r from-slate-900 via-slate-900 to-indigo-950/40 rounded-xl border border-slate-800 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
            <History className="w-4 h-4 text-indigo-400" />
            Project Cognitive Snapshots & Deterministic Replay
          </h3>
          <p className="text-xs text-slate-400 mt-1 max-w-2xl">
            Answers: "What did CortexForge believe at commit X?" Records immutable snapshot generations of project truth and provides deterministic context replay for benchmark regression debugging.
          </p>
        </div>

        <form onSubmit={handleTakeSnapshot} className="flex items-center gap-2 text-xs font-mono">
          <input
            type="text"
            placeholder="Commit SHA (e.g. c0ffee1)"
            value={commitShaInput}
            onChange={(e) => setCommitShaInput(e.target.value)}
            className="px-3 py-1.5 bg-slate-950 border border-slate-700 rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500 w-48"
          />
          <button
            type="submit"
            disabled={isTaking || !commitShaInput.trim() || !projectId}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition disabled:opacity-40"
          >
            {isTaking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Camera className="w-3.5 h-3.5" />}
            Take Snapshot
          </button>
        </form>
      </div>

      {/* Replay Result Panel */}
      {replayResult && (
        <div className="p-5 bg-slate-900 border border-indigo-500/60 rounded-xl shadow-xl space-y-3 font-mono text-xs animate-in fade-in">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center gap-2">
              <RotateCcw className="w-4 h-4 text-indigo-400" />
              <span className="font-bold text-slate-100 text-sm">
                Deterministic Replay Result at Commit {replayResult.commit_sha}
              </span>
            </div>
            <button
              onClick={() => setReplayResult(null)}
              className="text-slate-400 hover:text-slate-200"
            >
              Dismiss
            </button>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[11px]">
            <div className="p-3 bg-slate-950 rounded-lg border border-slate-800">
              <div className="text-slate-500">Cognitive Generation</div>
              <div className="text-indigo-400 font-bold mt-0.5">{replayResult.snapshot_generation}</div>
            </div>
            <div className="p-3 bg-slate-950 rounded-lg border border-slate-800">
              <div className="text-slate-500">Active Memories</div>
              <div className="text-emerald-400 font-bold mt-0.5">{replayResult.active_memories_count}</div>
            </div>
            <div className="p-3 bg-slate-950 rounded-lg border border-slate-800">
              <div className="text-slate-500">Selected Memories</div>
              <div className="text-blue-400 font-bold mt-0.5">{replayResult.selected_memories?.length || 0}</div>
            </div>
            <div className="p-3 bg-slate-950 rounded-lg border border-slate-800">
              <div className="text-slate-500">Replay Timestamp</div>
              <div className="text-slate-300 truncate mt-0.5">{replayResult.replay_timestamp}</div>
            </div>
          </div>

          <div>
            <div className="text-slate-400 font-semibold mb-1">Reconstructed Context Prompt:</div>
            <pre className="p-3 bg-slate-950 rounded-lg border border-slate-800 text-slate-300 text-[11px] overflow-x-auto whitespace-pre-wrap max-h-64 leading-relaxed font-mono">
              {replayResult.replayed_context}
            </pre>
          </div>
        </div>
      )}

      {/* Snapshots Table */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-semibold flex items-center gap-2">
            <Layers className="w-4 h-4 text-indigo-400" />
            Recorded Snapshot Generations ({snapshots.length})
          </h4>
        </div>

        {loading ? (
          <div className="py-12 text-center text-slate-400 font-mono text-xs flex items-center justify-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin text-indigo-400" />
            Loading cognitive snapshot history...
          </div>
        ) : snapshots.length === 0 ? (
          <div className="p-8 text-center bg-slate-900/40 rounded-xl border border-slate-800 font-mono text-xs text-slate-500">
            No cognitive snapshots taken yet. Capture a snapshot to preserve project state at the current commit.
          </div>
        ) : (
          <div className="space-y-2 font-mono text-xs">
            {snapshots.map((s) => (
              <div
                key={s.id}
                className="p-4 bg-slate-900/80 border border-slate-800 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-4 hover:border-slate-700 transition"
              >
                <div className="flex items-center gap-4 min-w-0">
                  <div className="w-8 h-8 rounded-lg bg-indigo-950/60 border border-indigo-800/80 flex items-center justify-center text-indigo-400 font-bold shrink-0">
                    G{s.cognitive_generation}
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-slate-200">Commit:</span>
                      <span className="text-indigo-300 font-semibold flex items-center gap-1">
                        <GitCommit className="w-3 h-3" />
                        {s.commit_sha}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-[11px] text-slate-400 mt-1">
                      <span>Memory Gen: {s.memory_generation}</span>
                      <span>•</span>
                      <span>Graph Gen: {s.graph_generation}</span>
                      <span>•</span>
                      <span>Retrieval: {s.retrieval_version}</span>
                      <span>•</span>
                      <span>{new Date(s.created_at).toLocaleString()}</span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3 self-end sm:self-auto">
                  <button
                    onClick={() => handleReplay(s.commit_sha)}
                    disabled={replayingCommit === s.commit_sha}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-indigo-300 border border-slate-700 rounded-lg transition disabled:opacity-40"
                  >
                    {replayingCommit === s.commit_sha ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Play className="w-3.5 h-3.5" />
                    )}
                    Deterministic Replay
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
