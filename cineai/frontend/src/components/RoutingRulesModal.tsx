import { useEffect, useState } from 'react'
import { apiFetch } from '../api'

interface Agent {
  id: string
  name: string
  icon: string
  description: string
  source: string
}

interface RoutingDecision {
  key: string
  description: string
}

interface KeywordOverrides {
  description: string
  music: string[]
  tmdb: string[]
}

interface RulesData {
  model: string
  agents: Agent[]
  routing_decisions: RoutingDecision[]
  keyword_overrides: KeywordOverrides
  llm_rules: string[]
}

type Tab = 'agents' | 'routing' | 'keywords' | 'llm'

interface Props { onClose: () => void }

export function RoutingRulesModal({ onClose }: Props) {
  const [data, setData]       = useState<RulesData | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab]         = useState<Tab>('agents')

  useEffect(() => {
    apiFetch('/api/rules')
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const handleBackdrop = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose()
  }

  const TABS: [Tab, string, string][] = [
    ['agents',   '🤖', 'Agents'],
    ['routing',  '🗺',  'Routes'],
    ['keywords', '⚡', 'Keywords'],
    ['llm',      '🧠', 'LLM Rules'],
  ]

  return (
    <div className="modal-backdrop" onClick={handleBackdrop}>
      <div className="modal" style={{ maxWidth: 620 }}>
        <div className="modal-header">
          <div className="modal-title">
            <span className="modal-title-icon">🧭</span>
            <div>
              <div className="modal-title-text">Routing Rules</div>
              <div className="modal-title-sub">
                {data ? `Model: ${data.model}` : 'How the supervisor decides which agents to call'}
              </div>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* Tabs */}
        <div className="obs-tabs" style={{ padding: '0 20px' }}>
          {TABS.map(([id, icon, label]) => (
            <button
              key={id}
              className={`obs-tab ${tab === id ? 'active' : ''}`}
              onClick={() => setTab(id)}
            >
              {icon} {label}
            </button>
          ))}
        </div>

        <div className="modal-body">
          {loading && <div className="modal-loading">Loading rules…</div>}

          {!loading && data && (
            <>
              {/* ── Agents tab ─────────────────────────────────────────── */}
              {tab === 'agents' && (
                <div>
                  <p className="rules-intro">
                    Each query is dispatched to one or more agents in parallel.
                    Results are merged by the synthesiser into a single answer.
                  </p>
                  <div className="rules-agent-grid">
                    {data.agents.map(a => (
                      <div key={a.id} className="rules-agent-card">
                        <div className="rules-agent-header">
                          <span className="rules-agent-icon">{a.icon}</span>
                          <span className="rules-agent-name">{a.name}</span>
                          <span className="rules-agent-source">{a.source}</span>
                        </div>
                        <div className="rules-agent-desc">{a.description}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ── Routes tab ─────────────────────────────────────────── */}
              {tab === 'routing' && (
                <div>
                  <p className="rules-intro">
                    The supervisor outputs exactly one of these routing decisions per query.
                    Multi-agent routes run all named agents in parallel.
                  </p>
                  <div className="rules-route-list">
                    {data.routing_decisions.map(r => (
                      <div key={r.key} className="rules-route-row">
                        <code className="rules-route-key">{r.key}</code>
                        <span className="rules-route-desc">{r.description}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ── Keywords tab ───────────────────────────────────────── */}
              {tab === 'keywords' && (
                <div>
                  <p className="rules-intro">
                    <strong>Checked before the LLM</strong> — if any of these words appear in
                    the query, the route is forced immediately without calling Groq.
                    Zero latency, 100% reliable.
                  </p>

                  <div className="status-section-label">
                    ⚡ Forces <code style={{ fontFamily: 'var(--font-mono)' }}>music</code> route
                  </div>
                  <div className="rules-keyword-cloud">
                    {data.keyword_overrides.music.map(kw => (
                      <span key={kw} className="rules-keyword rules-keyword--music">{kw}</span>
                    ))}
                  </div>

                  <div className="status-section-label" style={{ marginTop: 20 }}>
                    ⚡ Forces <code style={{ fontFamily: 'var(--font-mono)' }}>tmdb</code> route
                  </div>
                  <div className="rules-keyword-cloud">
                    {data.keyword_overrides.tmdb.map(kw => (
                      <span key={kw} className="rules-keyword rules-keyword--tmdb">{kw}</span>
                    ))}
                  </div>
                </div>
              )}

              {/* ── LLM Rules tab ──────────────────────────────────────── */}
              {tab === 'llm' && (
                <div>
                  <p className="rules-intro">
                    When no keyword override matches, the supervisor LLM ({data.model}) uses
                    these rules to classify the query. Add rules here to fix routing mistakes.
                  </p>
                  <ol className="rules-llm-list">
                    {data.llm_rules.map((rule, i) => {
                      const [condition, route] = rule.split(' → ')
                      return (
                        <li key={i} className="rules-llm-item">
                          <span className="rules-llm-condition">{condition}</span>
                          {route && (
                            <>
                              {' → '}
                              <code className="rules-route-key">{route}</code>
                            </>
                          )}
                        </li>
                      )
                    })}
                  </ol>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
