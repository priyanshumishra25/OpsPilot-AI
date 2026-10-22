export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown>; trace_id: string | null };
}

export interface Workspace { id: string; slug: string; name: string; role: string; access_scopes: string[] }

export interface Me {
  user_id: string;
  email: string;
  display_name: string;
  active_workspace_id: string;
  role: string;
  permissions: string[];
  access_scopes: string[];
  workspaces: Workspace[];
}

export interface Job {
  id: string;
  document_id: string;
  version_id: string;
  status: "queued" | "running" | "retry_scheduled" | "succeeded" | "failed" | "dead";
  stage: string;
  attempts: number;
  max_attempts: number;
  error: string | null;
  error_class: string | null;
  timings_ms: Record<string, number>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Version {
  id: string;
  version_number: number;
  content_hash: string;
  mime_type: string;
  original_filename: string;
  size_bytes: number;
  status: string;
  chunk_count: number;
  error: string | null;
  created_at: string;
  indexed_at: string | null;
}

export interface DocumentSummary {
  id: string;
  title: string;
  source_type: string;
  source_uri: string;
  access_scope: string;
  status: "pending" | "indexed" | "failed" | "deleting";
  is_demo_seed: boolean;
  current_version: Version | null;
  latest_job: Job | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentDetail extends DocumentSummary { versions: Version[]; jobs: Job[] }

export interface Chunk {
  id: string;
  document_id: string;
  document_title: string;
  version_id: string;
  ordinal: number;
  section_path: string[];
  content: string;
  page_number: number | null;
  char_start: number;
  char_end: number;
  source_uri: string;
}

export interface EvidenceItem {
  chunk_id: string;
  document_id: string;
  version_id: string;
  document_title: string;
  source_type: string;
  source_uri: string;
  section_path: string[];
  page_number: number | null;
  dense_rank: number | null;
  lexical_rank: number | null;
  fused_score: number;
  rerank_score: number | null;
  preview: string;
}

export interface RetrievalDiagnostics {
  query_class: string;
  lexical_query: string;
  strategy: string;
  candidates_considered: number;
  returned: number;
  timings_ms: Record<string, number>;
  evidence_sufficient: boolean;
  top_rerank_score: number | null;
  results: EvidenceItem[];
}

export interface Citation {
  index: number;
  chunk_id: string;
  document_id: string;
  version_id: string;
  document_title: string;
  section_path: string[];
  source_uri: string;
  page_number: number | null;
  char_start: number;
  char_end: number;
  cited_text: string;
}

export interface Usage {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  estimated_cost_usd: number;
  fallback_used: boolean;
  served_by: string;
}

export interface RagQueryRecord {
  id: string;
  query: string;
  status: string;
  answer: string | null;
  abstained: boolean;
  citations: Citation[];
  diagnostics: Partial<RetrievalDiagnostics> & {
    rejected_citations?: number;
    stop_reason?: string | null;
    citation_spans?: { offset: number; citation: number }[];
  };
  usage: Partial<Usage>;
  trace_id: string | null;
  latency_ms: number | null;
  error: string | null;
  created_at: string;
}

export interface Readiness {
  status: "ready" | "not_ready";
  checks: Record<string, string>;
  generation_configured: boolean;
}

export interface AuditEntry {
  id: number;
  actor_user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  outcome: string;
  details: Record<string, unknown>;
  trace_id: string | null;
  created_at: string;
}

export type AgentStatus = "queued" | "running" | "completed" | "awaiting_approval" | "failed" | "cancelled";

export interface AgentEvent {
  seq: number;
  turn: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AgentSessionSummary {
  id: string;
  title: string;
  status: AgentStatus;
  model: string;
  turns: number;
  tool_calls: number;
  usage: Partial<Usage> & { estimated_cost_usd?: number };
  answer: string | null;
  report: IncidentReport | null;
  incident_id: string | null;
  approval_ids: string[];
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentSessionDetail extends AgentSessionSummary {
  events: AgentEvent[];
}

export interface EvidenceRef { id: string; kind: string; source: string; observation: string }
export interface TimelineEntry { time: string; event: string; evidence: string[] }
export interface CandidateCause { cause: string; assessment: "likely" | "possible" | "unlikely" | "ruled_out"; supporting_evidence: string[]; contradicting_evidence: string[] }
export interface RecommendedAction { action: string; rationale: string; risk: "low" | "medium" | "high"; requires_approval: boolean; evidence: string[] }

export interface IncidentReport {
  incident_summary: string;
  observed_symptoms: string[];
  relevant_services: string[];
  evidence: EvidenceRef[];
  timeline: TimelineEntry[];
  candidate_causes: CandidateCause[];
  most_likely_cause: string | null;
  confidence: { evidence_strength: "strong" | "moderate" | "weak"; explanation: string };
  missing_information: string[];
  recommended_actions: RecommendedAction[];
  risk_assessment: string;
}

export interface Incident {
  id: string;
  title: string;
  severity: string;
  status: string;
  service: string | null;
  summary: string;
  report: IncidentReport;
  created_by: string | null;
  created_at: string;
}

export interface Approval {
  id: string;
  action: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  target_resource: string;
  target_state: Record<string, unknown>;
  expected_impact: string;
  reason: string;
  incident_id: string | null;
  status: "pending" | "approved" | "rejected" | "expired" | "executing" | "succeeded" | "failed";
  requested_by: string;
  decided_by: string | null;
  requested_by_name: string | null;
  decided_by_name: string | null;
  decision_note: string | null;
  result: { message?: string; verified_current_version?: string | null } & Record<string, unknown>;
  expires_at: string;
  created_at: string;
  decided_at: string | null;
  finished_at: string | null;
  can_decide: boolean;
}

export interface IntegrationStatus {
  servers: { server: string; status: "connected" | "unavailable"; error?: string; tools: { name: string; description: string }[] }[];
  github: { token_configured: boolean; repositories: string[] };
}

export interface DemoState {
  environment: string;
  scenarios: { name: string; description: string }[];
  services: { service: string; current_version: string; commit_sha: string; deployed_at: string; deployed_by: string; active_faults: Record<string, unknown>; scenario: string | null }[];
}
