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
