import { AgentName, AGENT_META, PipelineEvent, RoutingDecision } from '../types'

interface NodeState {
  status: 'idle' | 'running' | 'done'
  latency_ms?: number
}

interface Props {
  events: PipelineEvent[]
}

const ALL_NODES: AgentName[] = [
  'supervisor_route', 'tmdb_agent', 'rag_agent', 'search_agent', 'synthesise'
]

const ROUTING_AGENTS: Record<RoutingDecision, AgentName[]> = {
  'tmdb':        ['tmdb_agent'],
  'rag':         ['rag_agent'],
  'search':      ['search_agent'],
  'tmdb+rag':    ['tmdb_agent', 'rag_agent'],
  'tmdb+search': ['tmdb_agent', 'search_agent'],
  'rag+search':  ['rag_agent', 'search_agent'],
  'all':         ['tmdb_agent', 'rag_agent', 'search_agent'],
}

export function PipelineGraph({ events }: Props) {
  const nodeStates: Record<AgentName, NodeState> = Object.fromEntries(
    ALL_NODES.map(n => [n, { status: 'idle' }])
  ) as Record<AgentName, NodeState>

  let routing: RoutingDecision | null = null

  for (const e of events) {
    if (e.type === 'agent_start') {
      nodeStates[e.agent] = { status: 'running' }
    }
    if (e.type === 'agent_end') {
      nodeStates[e.agent] = { status: 'done', latency_ms: e.latency_ms }
    }
    if (e.type === 'routing_decision') {
      routing = e.routing
    }
  }

  const activeWorkers = routing ? ROUTING_AGENTS[routing] : []

  const renderNode = (name: AgentName) => {
    const meta = AGENT_META[name]
    const state = nodeStates[name]
    const dimmed = name !== 'supervisor_route' && name !== 'synthesise'
      && routing !== null && !activeWorkers.includes(name)

    return (
      <div
        key={name}
        className={`graph-node ${state.status}`}
        style={{
          '--node-color': meta.color,
          '--node-color-dim': meta.color + '44',
          opacity: dimmed ? 0.25 : 1,
        } as React.CSSProperties}
      >
        <span className="graph-node-icon">{meta.icon}</span>
        <span className="graph-node-label">{meta.label}</span>
        {state.status === 'running' && (
          <span className="graph-node-status running">
            <LoadingDots />
          </span>
        )}
        {state.status === 'done' && state.latency_ms !== undefined && (
          <span className="graph-node-latency">{state.latency_ms}ms</span>
        )}
      </div>
    )
  }

  const allIdle = Object.values(nodeStates).every(s => s.status === 'idle')

  return (
    <div className="pipeline-graph">
      {allIdle && (
        <div className="pipeline-idle-hint">Ask a question to watch agents work in real-time</div>
      )}
      <div className="graph-nodes">
        {renderNode('supervisor_route')}

        <div className="graph-connector">↓</div>

        {routing && (
          <div className="graph-routing-label">→ {routing}</div>
        )}

        <div className="graph-fan">
          {renderNode('tmdb_agent')}
          {renderNode('rag_agent')}
          {renderNode('search_agent')}
        </div>

        <div className="graph-connector">↓</div>

        {renderNode('synthesise')}
      </div>
    </div>
  )
}

function LoadingDots() {
  return (
    <span className="loading-dots">
      <span>.</span><span>.</span><span>.</span>
    </span>
  )
}
