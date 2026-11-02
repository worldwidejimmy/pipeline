// ── Pipeline SSE event types ──────────────────────────────────────────────────

export type AgentName =
  | 'supervisor_route'
  | 'tmdb_agent'
  | 'rag_agent'
  | 'search_agent'
  | 'music_agent'
  | 'synthesise'
  | 'kb_lookup';      // knowledge-base-first retrieval, before routing

// Must stay in sync with the _dispatch mapping in backend/src/graph/pipeline.py.
export type RoutingDecision =
  | 'tmdb' | 'rag' | 'search' | 'music'
  | 'tmdb+rag' | 'tmdb+search' | 'tmdb+music'
  | 'music+search' | 'rag+search' | 'all';

export interface PipelineStartEvent {
  type: 'pipeline_start';
  question: string;
  ts: number;
}

export interface AgentStartEvent {
  type: 'agent_start';
  agent: AgentName;
  ts: number;
}

export interface AgentEndEvent {
  type: 'agent_end';
  agent: AgentName;
  latency_ms: number;
  ts: number;
}

export interface RoutingDecisionEvent {
  type: 'routing_decision';
  routing: RoutingDecision;
  agent: AgentName;
  by?: 'keyword' | 'model';   // deterministic keyword rule, or the LLM classifier
  ts: number;
}

export interface LlmStartEvent {
  type: 'llm_start';
  agent: AgentName;
  model: string;
  ts: number;
}

export interface TokenEvent {
  type: 'token';
  content: string;
  agent: AgentName;
  is_final: boolean;
  ts: number;
}

export interface LlmEndEvent {
  type: 'llm_end';
  agent: AgentName;
  prompt_tokens: number;
  completion_tokens: number;
  ts: number;
}

export interface RagChunk {
  text: string;
  score: number;
  source: string;
  /** 'hybrid' = BM25 + dense (Milvus 2.5); 'dense' = embedding-only fallback */
  search_type?: 'hybrid' | 'dense';
}

export interface ChunksRetrievedEvent {
  type: 'chunks_retrieved';
  chunks: RagChunk[];
  count: number;
  ts: number;
}

export interface TmdbResultsEvent {
  type: 'tmdb_results';
  results: TmdbMovie[];
  count: number;
  ts: number;
}

export interface KbLookupEvent {
  type: 'kb_lookup';
  relevant: boolean;          // passed the calibrated relevance gate
  relevant_count: number;
  best_score: number;         // dense similarity of the best passage
  threshold: number;
  chunks: RagChunk[];         // the relevant passages (empty when none)
  latency_ms: number;
  ts: number;
}

export interface SearchResultsEvent {
  type: 'search_results';
  count: number;
  domains: string[];
  ts: number;
}

export interface MusicResultsEvent {
  type: 'music_results';
  artist: string | null;
  albums: unknown[];
  genres: string[];
  ts: number;
}

export interface GuardrailEvent {
  type: 'guardrail';
  kind: 'link_removed';
  count: number;
  domains: string[];
  ts: number;
}

export interface DoneEvent {
  type: 'done';
  total_latency_ms: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  agents_used: AgentName[];
  model?: string;
  ts: number;
}

export interface ErrorEvent {
  type: 'error';
  message: string;
  ts: number;
}

export type PipelineEvent =
  | PipelineStartEvent
  | AgentStartEvent
  | AgentEndEvent
  | RoutingDecisionEvent
  | LlmStartEvent
  | TokenEvent
  | LlmEndEvent
  | ChunksRetrievedEvent
  | TmdbResultsEvent
  | KbLookupEvent
  | SearchResultsEvent
  | MusicResultsEvent
  | GuardrailEvent
  | DoneEvent
  | ErrorEvent;

// ── Usage / quota ─────────────────────────────────────────────────────────────

export interface Usage {
  unlimited: boolean;
  free_limit: number;
  free_remaining: number;
  free_used: number;
  window_seconds: number;
  reset_in: number;
  tokens_used_today: number;
  token_budget: number;
}

// ── Admin ─────────────────────────────────────────────────────────────────────

export interface AdminIpRow {
  ip: string;
  requests: number;
  tokens: number;
  last_seen: number;
  blacklisted: boolean;
}

export interface AdminUsage {
  day: string;
  total_tokens: number;
  calls_today: number;
  call_cap: number;
  free_limit: number;
  window_seconds: number;
  ips: AdminIpRow[];
  blacklist: string[];
}

// ── TMDB types ────────────────────────────────────────────────────────────────

export interface TmdbMovie {
  id: number;
  title: string;
  year: string;
  rating: number;
  vote_count: number;
  overview: string;
  poster: string | null;
  genres: string[];
  media_type: string;
}

// ── Agent display metadata ────────────────────────────────────────────────────

export const AGENT_META: Record<AgentName, { label: string; color: string; icon: string }> = {
  supervisor_route: { label: 'Supervisor',   color: '#a855f7', icon: '🧠' },
  tmdb_agent:       { label: 'TMDB Agent',   color: '#06b6d4', icon: '🎬' },
  rag_agent:        { label: 'RAG Agent',    color: '#3b82f6', icon: '🔍' },
  search_agent:     { label: 'Web Search',   color: '#f97316', icon: '🌐' },
  music_agent:      { label: 'Music Agent',  color: '#ec4899', icon: '🎵' },
  synthesise:       { label: 'Synthesiser',  color: '#eab308', icon: '✨' },
  kb_lookup:        { label: 'KB lookup',    color: '#60a5fa', icon: '📚' },
};
