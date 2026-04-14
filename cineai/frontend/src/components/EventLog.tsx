import { useState } from 'react'
import { AGENT_META, PipelineEvent } from '../types'

interface Props {
  events: PipelineEvent[]
}

interface DisplayEvent {
  color: string
  icon: string
  typeLabel: string
  detail: string
  subDetail?: string
  ts: number
  expandable?: object
}

function formatTime(ts: number, baseTs: number): string {
  const delta = ts - baseTs
  return delta >= 0 ? `+${delta}ms` : `${delta}ms`
}

function toDisplay(e: PipelineEvent, baseTs: number): DisplayEvent | null {
  const ts = e.ts

  switch (e.type) {
    case 'pipeline_start':
      return { color: 'var(--purple)', icon: '▶', typeLabel: 'START', detail: `"${e.question}"`, ts }

    case 'agent_start': {
      const m = AGENT_META[e.agent]
      return { color: m.color, icon: m.icon, typeLabel: 'AGENT START', detail: m.label, ts }
    }

    case 'agent_end': {
      const m = AGENT_META[e.agent]
      return { color: m.color, icon: '✓', typeLabel: 'AGENT END', detail: `${m.label} — ${e.latency_ms}ms`, ts }
    }

    case 'routing_decision':
      return {
        color: 'var(--purple)',
        icon: '🔀',
        typeLabel: 'ROUTING',
        detail: `→ ${e.routing}`,
        ts,
      }

    case 'llm_start': {
      const m = AGENT_META[e.agent]
      return {
        color: m.color,
        icon: '🤖',
        typeLabel: 'LLM CALL',
        detail: `${m.label}`,
        subDetail: e.model || 'groq',
        ts,
      }
    }

    case 'llm_end': {
      const m = AGENT_META[e.agent]
      return {
        color: m.color,
        icon: '📊',
        typeLabel: 'LLM END',
        detail: `${m.label}`,
        subDetail: `prompt: ${e.prompt_tokens}t  completion: ${e.completion_tokens}t`,
        ts,
      }
    }

    case 'token':
      // Skip — shown in aggregate in the answer panel, too noisy for log
      if (!e.is_final) return null
      return null

    case 'chunks_retrieved':
      return {
        color: 'var(--blue)',
        icon: '🔍',
        typeLabel: 'RETRIEVAL',
        detail: `${e.count} chunks retrieved`,
        subDetail: e.chunks[0] ? `top: "${e.chunks[0].text.slice(0, 60)}…"` : '',
        expandable: e.chunks,
        ts,
      }

    case 'tmdb_results':
      return {
        color: 'var(--cyan)',
        icon: '🎬',
        typeLabel: 'TMDB',
        detail: `${e.count} result${e.count !== 1 ? 's' : ''}`,
        subDetail: e.results[0]?.title ? `"${e.results[0].title}" (${e.results[0].year})` : '',
        expandable: e.results,
        ts,
      }

    case 'done':
      return {
        color: 'var(--green)',
        icon: '✅',
        typeLabel: 'DONE',
        detail: `${e.total_latency_ms}ms total · ${e.total_prompt_tokens + e.total_completion_tokens} tokens`,
        subDetail: `agents: ${e.agents_used.join(', ') || 'none'}`,
        ts,
      }

    case 'error':
      return { color: 'var(--red)', icon: '❌', typeLabel: 'ERROR', detail: e.message, ts }

    default:
      return null
  }
}

export function EventLog({ events }: Props) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const baseTs = events[0]?.ts ?? Date.now()

  const displayEvents = events
    .map((e, i) => ({ display: toDisplay(e, baseTs), idx: i }))
    .filter(({ display }) => display !== null) as Array<{ display: DisplayEvent; idx: number }>

  if (displayEvents.length === 0) {
    return (
      <div className="event-log" style={{ justifyContent: 'center', alignItems: 'center' }}>
        <div style={{ color: 'var(--text-dim)', fontSize: 12, fontStyle: 'italic' }}>
          Events will appear here as the pipeline runs…
        </div>
      </div>
    )
  }

  return (
    <div className="event-log">
      {displayEvents.map(({ display, idx }) => (
        <div key={idx}>
          <div
            className="event-row"
            style={{ '--event-color': display.color } as React.CSSProperties}
          >
            <span className="event-time">{formatTime(display.ts, baseTs)}</span>
            <span className="event-icon">{display.icon}</span>
            <div className="event-body">
              <div>
                <span className="event-type">{display.typeLabel}</span>
                {'  '}
                <span className="event-detail">{display.detail}</span>
                {display.expandable && (
                  <span
                    style={{ color: 'var(--text-dim)', cursor: 'pointer', marginLeft: 6 }}
                    onClick={() => setExpanded(prev => ({ ...prev, [idx]: !prev[idx] }))}
                  >
                    {expanded[idx] ? '▲' : '▼'}
                  </span>
                )}
              </div>
              {display.subDetail && (
                <div className="event-sub">{display.subDetail}</div>
              )}
              {expanded[idx] && display.expandable && (
                <pre style={{
                  background: 'var(--bg)',
                  borderRadius: 4,
                  color: 'var(--text-muted)',
                  fontSize: 10,
                  marginTop: 6,
                  maxHeight: 200,
                  overflow: 'auto',
                  padding: 8,
                }}>
                  {JSON.stringify(display.expandable, null, 2)}
                </pre>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
