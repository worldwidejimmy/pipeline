import { AgentName, AGENT_META, PipelineEvent } from '../types'

interface Span {
  agent: AgentName
  startTs: number
  endTs?: number
  latency_ms?: number
}

interface Props {
  events: PipelineEvent[]
}

export function AgentTimeline({ events }: Props) {
  const spans: Span[] = []
  const startMap: Partial<Record<AgentName, number>> = {}
  let pipelineStart = 0

  for (const e of events) {
    if (e.type === 'pipeline_start') pipelineStart = e.ts
    if (e.type === 'agent_start') {
      startMap[e.agent] = e.ts
    }
    if (e.type === 'agent_end') {
      const startTs = startMap[e.agent] ?? e.ts
      spans.push({ agent: e.agent, startTs, endTs: e.ts, latency_ms: e.latency_ms })
    }
  }

  if (spans.length === 0) {
    return (
      <div className="timeline">
        <div className="timeline-title">Agent Execution Timeline</div>
        <div style={{ color: 'var(--text-dim)', fontSize: 12, fontStyle: 'italic' }}>
          No agents have run yet.
        </div>
      </div>
    )
  }

  const minTs = pipelineStart || Math.min(...spans.map(s => s.startTs))
  const maxTs = Math.max(...spans.map(s => s.endTs ?? s.startTs + 100))
  const totalDuration = maxTs - minTs || 1

  return (
    <div className="timeline">
      <div className="timeline-title">Agent Execution Timeline</div>
      {spans.map((span, i) => {
        const meta = AGENT_META[span.agent]
        const left = ((span.startTs - minTs) / totalDuration) * 100
        const width = (((span.endTs ?? span.startTs + 100) - span.startTs) / totalDuration) * 100
        const clampedWidth = Math.max(width, 2)

        return (
          <div className="timeline-row" key={i}>
            <div className="timeline-label" title={span.agent}>
              {meta.icon} {meta.label.split(' ')[0]}
            </div>
            <div className="timeline-track">
              <div
                className="timeline-bar"
                style={{
                  left: `${left}%`,
                  width: `${clampedWidth}%`,
                  background: meta.color,
                }}
              >
                {clampedWidth > 8 && span.latency_ms && `${span.latency_ms}ms`}
              </div>
            </div>
            <div className="timeline-latency">
              {span.latency_ms ? `${span.latency_ms}ms` : '—'}
            </div>
          </div>
        )
      })}

      {/* Total duration */}
      <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text-muted)', textAlign: 'right', fontFamily: 'var(--font-mono)' }}>
        Total: {totalDuration}ms
      </div>
    </div>
  )
}
