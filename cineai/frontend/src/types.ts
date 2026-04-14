// ── Pipeline SSE event types ──────────────────────────────────────────────────

export type AgentName =
  | 'supervisor_route'
  | 'tmdb_agent'
  | 'rag_agent'
  | 'search_agent'
  | 'synthesise';

export type RoutingDecision =
  | 'tmdb' | 'rag' | 'search'
  | 'tmdb+rag' | 'tmdb+search' | 'rag+search' | 'all';

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

export interface ChunksRetrievedEvent {
  type: 'chunks_retrieved';
  chunks: Array<{ text: string; score: number; source: string }>;
  count: number;
  ts: number;
}

export interface TmdbResultsEvent {
  type: 'tmdb_results';
  results: TmdbMovie[];
  count: number;
  ts: number;
}

export interface DoneEvent {
  type: 'done';
  total_latency_ms: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  agents_used: AgentName[];
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
  | DoneEvent
  | ErrorEvent;

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
  synthesise:       { label: 'Synthesiser',  color: '#eab308', icon: '✨' },
};
