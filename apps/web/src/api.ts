import { ArchitectureResponse, ChangeImpactReport, Memory, Project } from './types';

const API_BASE = '/api/v1';

export async function fetchProjects(): Promise<Project[]> {
  try {
    const res = await fetch(`${API_BASE}/projects`);
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export async function fetchArchitecture(projectId: string): Promise<ArchitectureResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/architecture`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchMemories(
  projectId: string,
  type?: string,
  status?: string
): Promise<Memory[]> {
  try {
    const params = new URLSearchParams();
    if (type && type !== 'ALL') params.set('type', type);
    if (status && status !== 'ALL') params.set('status', status);
    const res = await fetch(`${API_BASE}/projects/${projectId}/memories?${params.toString()}`);
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export async function triggerScan(projectId: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/scan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ incremental: true }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function verifyMemory(memoryId: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/memories/${memoryId}/verify`, { method: 'POST' });
    return res.ok;
  } catch {
    return false;
  }
}

export async function deprecateMemory(memoryId: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/memories/${memoryId}/deprecate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'Manual deprecation via web dashboard' }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function triggerConsolidate(projectId: string): Promise<any> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/consolidate`, { method: 'POST' });
    return await res.json();
  } catch {
    return null;
  }
}

export async function checkImpact(
  projectId: string,
  modifiedFiles: string[]
): Promise<ChangeImpactReport | null> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/impact`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ modified_files: modifiedFiles, mark_stale: false }),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function runBenchmark(projectId: string): Promise<any> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/benchmark`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchTokenEconomics(projectId: string): Promise<any> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/economics`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchArchitectureRules(projectId: string): Promise<any[]> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/architecture/rules`);
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export async function createArchitectureRule(
  projectId: string,
  rule: {
    rule_name: string;
    description: string;
    forbidden_source_pattern: string;
    forbidden_target_pattern: string;
    severity?: string;
  }
): Promise<any> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/architecture/rules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rule),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchArchitectureViolations(projectId: string): Promise<any[]> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/architecture/violations`);
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export async function fetchProvenance(memoryId: string): Promise<any> {
  try {
    const res = await fetch(`${API_BASE}/memories/${memoryId}/provenance`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchSnapshots(projectId: string): Promise<any[]> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/snapshots`);
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export async function takeSnapshot(projectId: string, commitSha: string): Promise<any> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/snapshots?commit_sha=${encodeURIComponent(commitSha)}`, {
      method: 'POST',
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function replaySnapshot(projectId: string, commitSha: string): Promise<any> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/snapshots/${encodeURIComponent(commitSha)}/replay`, {
      method: 'POST',
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function fetchTestRuns(projectId: string): Promise<any[]> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/tests`);
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export async function fetchFailureEpisodes(projectId: string): Promise<any[]> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/failures`);
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export async function runMutationBenchmark(projectId: string): Promise<any> {
  try {
    const res = await fetch(`${API_BASE}/projects/${projectId}/mutations/benchmark`, {
      method: 'POST',
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

