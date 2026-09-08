export interface Project {
  id: string;
  name: string;
  local_path: string;
  default_branch: string;
  language?: string;
  status: string;
  last_indexed_commit?: string;
  created_at: string;
  updated_at: string;
  file_count?: number;
  entity_count?: number;
  memory_count?: number;
}

export interface ComponentSummary {
  name: string;
  qualified_name: string;
  entity_type: string;
  file_path: string;
  line_range: [number, number];
  signature?: string;
  dependencies: string[];
  dependents: string[];
}

export interface ModuleSummary {
  module_path: string;
  file_count: number;
  entity_count: number;
  top_level_components: ComponentSummary[];
}

export interface ArchitectureResponse {
  project_id: string;
  project_name: string;
  total_files: number;
  total_entities: number;
  total_relationships: number;
  languages: string[];
  modules: ModuleSummary[];
  primary_apis: ComponentSummary[];
  primary_models: ComponentSummary[];
}

export interface MemoryEvidence {
  id: string;
  source_type: string;
  file_path: string;
  symbol_id?: string;
  commit_sha?: string;
  line_start?: number;
  line_end?: number;
  confidence: number;
  ast_fingerprint?: string;
  snippet_hash?: string;
}

export interface Memory {
  id: string;
  project_id: string;
  layer?: 'L0' | 'L1' | 'L2' | 'L3' | 'L4' | 'L5' | 'L6' | string;
  memory_type: string;
  title: string;
  content: string;
  summary: string;
  status: 'ACTIVE' | 'STALE' | 'CONFLICTED' | 'SUPERSEDED' | 'DEPRECATED' | 'UNVERIFIED' | 'ARCHIVED';
  confidence: number;
  importance: number;
  freshness_score?: number;
  source_type: string;
  source_reference?: string;
  source_commit?: string;
  created_by: string;
  version: number;
  supersedes_id?: string;
  superseded_by_id?: string;
  conflict_group?: string;
  created_at: string;
  updated_at: string;
  last_verified_at?: string;
  evidences?: MemoryEvidence[];
}

export interface ChangeImpactReport {
  project_id: string;
  modified_files: string[];
  directly_changed_entities: string[];
  affected_dependents: string[];
  memories_flagged_stale: string[];
  critical_constraints: string[];
  warnings: string[];
}

