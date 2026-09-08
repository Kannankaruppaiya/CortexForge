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

export interface ArchitectureRule {
  id: string;
  project_id: string;
  rule_name: string;
  description: string;
  scope: string;
  severity: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';
  forbidden_source_pattern: string;
  forbidden_target_pattern: string;
  enforcement_status: string;
  created_at: string;
}

export interface RuleViolation {
  id: string;
  rule_id: string;
  source_entity_id: string;
  target_entity_id: string;
  commit_sha?: string;
  violation_details: string;
  created_at: string;
}

export interface ProvenanceTrace {
  memory_id: string;
  title: string;
  layer: string;
  memory_type: string;
  status: string;
  confidence: number;
  why_cortexforge_believes_this: string;
  evidences: Array<{
    evidence_id: string;
    source_type: string;
    file_path: string;
    line_start?: number;
    line_end?: number;
    commit_sha?: string;
    verification_status: string;
  }>;
  symbols: Array<{
    name: string;
    qualified_name: string;
    file_path: string;
    signature?: string;
  }>;
  files: string[];
  commits: string[];
  versions: Array<{
    version: number;
    old_state?: string;
    new_state?: string;
    reason?: string;
    actor?: string;
    commit_sha?: string;
    created_at?: string;
  }>;
  tests: Array<{
    test_name: string;
    status: string;
    commit_sha?: string;
  }>;
}

export interface CognitiveSnapshot {
  id: string;
  project_id: string;
  commit_sha: string;
  cognitive_generation: number;
  graph_generation: number;
  memory_generation: number;
  index_generation: number;
  retrieval_version: string;
  embedding_version: string;
  snapshot_metadata: Record<string, any>;
  created_at: string;
}

export interface TestCaseResult {
  id: string;
  test_run_id: string;
  test_name: string;
  suite?: string;
  status: 'PASSED' | 'FAILED' | 'SKIPPED' | 'ERROR';
  duration_ms: number;
  error_message?: string;
  failure_signature?: string;
  is_flaky: boolean;
  created_at: string;
}

export interface TestRun {
  id: string;
  project_id: string;
  task_id?: string;
  commit_sha?: string;
  framework: string;
  environment?: string;
  status: 'PASSED' | 'FAILED' | 'ERROR';
  total_tests: number;
  passed_count: number;
  failed_count: number;
  duration_ms: number;
  created_at: string;
  results: TestCaseResult[];
}

export interface FixAttempt {
  id: string;
  failure_episode_id: string;
  commit_sha?: string;
  attempted_fix: string;
  success: boolean;
  why_worked_or_failed?: string;
  created_at: string;
}

export interface FailureEpisode {
  id: string;
  project_id: string;
  task_id?: string;
  test_case_result_id?: string;
  commit_sha?: string;
  failure_signature: string;
  error_class: string;
  error_message: string;
  normalized_trace?: string;
  attempted_approach: string;
  rejected_reason?: string;
  command_or_tool?: string;
  root_cause?: string;
  affected_files: string[];
  affected_symbols: string[];
  created_at: string;
  fix_attempts: FixAttempt[];
}

export interface MutationBenchmarkResult {
  project_id: string;
  results: Array<{
    test_id: string;
    mutation_type: string;
    passed: boolean;
    actual_status: string;
    expected_status: string;
    reanchored_as_expected: boolean;
    invalidated_as_expected: boolean;
    details: string;
  }>;
  all_passed: boolean;
}


