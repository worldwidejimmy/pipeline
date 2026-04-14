import { PipelineEvent, AGENT_META } from '../types'

interface Props {
  events: PipelineEvent[]
  isStreaming: boolean
}

export function MetricsBar({ events, isStreaming }: Props) {
  let totalLatency = 0
  let promptTokens = 0
  let completionTokens = 0
  let agentsUsed: string[] = []
  let routing = '—'
  let llmCalls = 0

  for (const e of events) {
    if (e.type === 'done') {
      totalLatency = e.total_latency_ms
      promptTokens = e.total_prompt_tokens
      completionTokens = e.total_completion_tokens
      agentsUsed = e.agents_used.map(a => AGENT_META[a]?.icon ?? a)
    }
    if (e.type === 'routing_decision') routing = e.routing
    if (e.type === 'llm_start') llmCalls++
  }

  const totalTokens = promptTokens + completionTokens

  return (
    <div className="metrics-bar">
      <div className="metric-item">
        <span className="metric-label">Latency</span>
        <span className="metric-value cyan">
          {isStreaming ? '…' : totalLatency ? `${(totalLatency / 1000).toFixed(1)}s` : '—'}
        </span>
      </div>
      <div className="metric-item">
        <span className="metric-label">Tokens</span>
        <span className="metric-value amber">
          {totalTokens ? totalTokens.toLocaleString() : '—'}
        </span>
      </div>
      <div className="metric-item">
        <span className="metric-label">LLM Calls</span>
        <span className="metric-value indigo">{llmCalls || '—'}</span>
      </div>
      <div className="metric-item">
        <span className="metric-label">Routing</span>
        <span className="metric-value" style={{ fontSize: 11 }}>{routing}</span>
      </div>
      <div className="metric-item">
        <span className="metric-label">Agents</span>
        <span className="metric-value" style={{ fontSize: 16 }}>
          {agentsUsed.length ? agentsUsed.join(' ') : '—'}
        </span>
      </div>
    </div>
  )
}
