import { AgentCredential, ArchitectureResponse, ChangeImpactReport, Memory, Project, ProjectMembership } from './types';

const API_BASE = '/api/v1';

export class ApiError extends Error {
  status: number;
  data: any;

  constructor(status: number, message: string, data?: any) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  return fetch(url, {
    ...options,
    credentials: 'include',
  });
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let errorDetail = `Request failed with status ${res.status}`;
    let bodyData: any = null;
    try {
      bodyData = await res.json();
      if (bodyData && typeof bodyData === 'object') {
        errorDetail = bodyData.detail || bodyData.message || errorDetail;
      }
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, errorDetail, bodyData);
  }
  return await res.json();
}

export async function fetchProjects(): Promise<Project[]> {
  const res = await authFetch(`${API_BASE}/projects`);
  return await handleResponse<Project[]>(res);
}

export async function fetchArchitecture(projectId: string): Promise<ArchitectureResponse> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/architecture`);
  return await handleResponse<ArchitectureResponse>(res);
}

export async function fetchMemories(
  projectId: string,
  type?: string,
  status?: string
): Promise<Memory[]> {
  const params = new URLSearchParams();
  if (type && type !== 'ALL') params.set('type', type);
  if (status && status !== 'ALL') params.set('status', status);
  const res = await authFetch(`${API_BASE}/projects/${projectId}/memories?${params.toString()}`);
  return await handleResponse<Memory[]>(res);
}

export async function triggerScan(projectId: string): Promise<boolean> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/scan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ incremental: true }),
  });
  if (!res.ok) {
    const errorText = await res.text().catch(() => '');
    throw new ApiError(res.status, errorText || `Scan failed with status ${res.status}`);
  }
  return true;
}

export async function verifyMemory(memoryId: string): Promise<boolean> {
  const res = await authFetch(`${API_BASE}/memories/${memoryId}/verify`, { method: 'POST' });
  if (!res.ok) {
    throw new ApiError(res.status, `Memory verification failed with status ${res.status}`);
  }
  return true;
}

export async function deprecateMemory(memoryId: string): Promise<boolean> {
  const res = await authFetch(`${API_BASE}/memories/${memoryId}/deprecate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: 'Manual deprecation via web dashboard' }),
  });
  if (!res.ok) {
    throw new ApiError(res.status, `Memory deprecation failed with status ${res.status}`);
  }
  return true;
}

export async function triggerConsolidate(projectId: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/consolidate`, { method: 'POST' });
  return await handleResponse<any>(res);
}

export async function checkImpact(
  projectId: string,
  modifiedFiles: string[]
): Promise<ChangeImpactReport> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/impact`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ modified_files: modifiedFiles, mark_stale: false }),
  });
  return await handleResponse<ChangeImpactReport>(res);
}

export async function runBenchmark(projectId: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/benchmark`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  return await handleResponse<any>(res);
}

export async function fetchTokenEconomics(projectId: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/economics`);
  return await handleResponse<any>(res);
}

export async function fetchArchitectureRules(projectId: string): Promise<any[]> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/architecture/rules`);
  return await handleResponse<any[]>(res);
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
  const res = await authFetch(`${API_BASE}/projects/${projectId}/architecture/rules`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(rule),
  });
  return await handleResponse<any>(res);
}

export async function fetchArchitectureViolations(projectId: string): Promise<any[]> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/architecture/violations`);
  return await handleResponse<any[]>(res);
}

export async function fetchProvenance(memoryId: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/memories/${memoryId}/provenance`);
  return await handleResponse<any>(res);
}

export async function fetchSnapshots(projectId: string): Promise<any[]> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/snapshots`);
  return await handleResponse<any[]>(res);
}

export async function takeSnapshot(projectId: string, commitSha: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/snapshots?commit_sha=${encodeURIComponent(commitSha)}`, {
    method: 'POST',
  });
  return await handleResponse<any>(res);
}

export async function replaySnapshot(projectId: string, commitSha: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/snapshots/${encodeURIComponent(commitSha)}/replay`, {
    method: 'POST',
  });
  return await handleResponse<any>(res);
}

export async function fetchTestRuns(projectId: string): Promise<any[]> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/tests`);
  return await handleResponse<any[]>(res);
}

export async function fetchFailureEpisodes(projectId: string): Promise<any[]> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/failures`);
  return await handleResponse<any[]>(res);
}

export async function runMutationBenchmark(projectId: string): Promise<any> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/mutations/benchmark`, {
    method: 'POST',
  });
  return await handleResponse<any>(res);
}

export async function fetchAgentCredentials(agentId: string): Promise<AgentCredential[]> {
  const res = await authFetch(`${API_BASE}/agents/${agentId}/credentials`);
  return await handleResponse<AgentCredential[]>(res);
}

export async function createAgentCredential(
  agentId: string,
  name = 'default',
  expiresInDays?: number
): Promise<{ key_id: string; secret: string; name: string }> {
  const res = await authFetch(`${API_BASE}/agents/${agentId}/credentials`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, expires_in_days: expiresInDays }),
  });
  return await handleResponse<{ key_id: string; secret: string; name: string }>(res);
}

export async function revokeAgentCredential(agentId: string, keyId: string): Promise<boolean> {
  const res = await authFetch(`${API_BASE}/agents/${agentId}/credentials/${keyId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    throw new ApiError(res.status, `Revoke credential failed with status ${res.status}`);
  }
  return true;
}

export async function fetchProjectMembers(projectId: string): Promise<ProjectMembership[]> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/members`);
  return await handleResponse<ProjectMembership[]>(res);
}

export async function addProjectMember(
  projectId: string,
  userId: string,
  role = 'MEMBER'
): Promise<ProjectMembership> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/members`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, role }),
  });
  return await handleResponse<ProjectMembership>(res);
}

export async function updateProjectMemberRole(
  projectId: string,
  userId: string,
  role: string
): Promise<ProjectMembership> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/members/${userId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  });
  return await handleResponse<ProjectMembership>(res);
}

export async function removeProjectMember(projectId: string, userId: string): Promise<boolean> {
  const res = await authFetch(`${API_BASE}/projects/${projectId}/members/${userId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    throw new ApiError(res.status, `Remove member failed with status ${res.status}`);
  }
  return true;
}
