import { useEffect, useState } from 'react'
import { apiFetch } from '../api'

interface ServiceStatus {
  status: 'ok' | 'error' | 'rate_limited' | 'auth_error' | 'loading'
  latency_ms?: number
  detail?: string
  model?: string
  retry_in?: string
  chunks?: number
  collection?: string
}

interface StatusData {
  keys: { anthropic: boolean; openai: boolean; tmdb: boolean; tavily: boolean }
  anthropic: ServiceStatus
  milvus: ServiceStatus
  tmdb: ServiceStatus
}

function StatusDot({ status }: { status: string }) {
  const cls = status === 'ok' ? 'dot-ok'
    : status === 'rate_limited' ? 'dot-warn'
    : status === 'loading' ? 'dot-loading'
    : 'dot-err'
  return <span className={`status-dot ${cls}`} />
}

function StatusBadge({ status, text }: { status: string; text: string }) {
  const cls = status === 'ok' ? 'badge-ok'
    : status === 'rate_limited' ? 'badge-warn'
    : status === 'loading' ? 'badge-loading'
    : 'badge-err'
  return <span className={`status-badge ${cls}`}>{text}</span>
}

function KeyRow({ label, present }: { label: string; present: boolean }) {
  return (
    <div className="key-row">
      <span className="key-label">{label}</span>
      {present
        ? <span className="status-badge badge-ok">✓ configured</span>
        : <span className="status-badge badge-err">✗ missing</span>}
    </div>
  )
}

interface EvalTier { tier: string; name: string; status: string; headline: string; at: string; model: string }
interface EvalSummary { updated?: string; model?: string; tiers: EvalTier[] }

const TIER_LABEL: Record<string, string> = {
  t1: 'Routing', t2: 'Retrieval', t3: 'Answer quality (LLM judge)', t4: 'Canary questions', t5: 'Latency', t6: 'MCP server',
}

function hoursAgo(iso?: string): string {
  if (!iso) return ''
  const h = (Date.now() - Date.parse(iso)) / 3.6e6
  return h < 1 ? 'under 1h ago' : h < 48 ? `${Math.round(h)}h ago` : `${Math.round(h / 24)}d ago`
}

interface Props { onClose: () => void }

export function StatusModal({ onClose }: Props) {
  const [data, setData] = useState<StatusData | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshed, setRefreshed] = useState<Date | null>(null)
  const [evals, setEvals] = useState<EvalSummary | null>(null)

  const load = () => {
    setLoading(true)
    apiFetch('/api/status')
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); setRefreshed(new Date()) })
      .catch(() => setLoading(false))
  }

  useEffect(() => {
    load()
    apiFetch('/api/evals/summary').then(r => r.json()).then(setEvals).catch(() => {})
  }, [])

  const handleBackdrop = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose()
  }

  const fmtLatency = (ms?: number) => ms !== undefined ? `${ms}ms` : ''

  return (
    <div className="modal-backdrop" onClick={handleBackdrop}>
      <div className="modal">
        <div className="modal-header">
          <div className="modal-title">
            <span className="modal-title-icon">⚙️</span>
            <div>
              <div className="modal-title-text">Service Status</div>
              <div className="modal-title-sub">
                {loading ? 'Checking services…'
                  : refreshed ? `Last checked ${refreshed.toLocaleTimeString()}` : ''}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="modal-close" onClick={load} title="Refresh" style={{ fontSize: 14 }}>↻</button>
            <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
          </div>
        </div>

        <div className="modal-body">
          {loading && <div className="modal-loading">Pinging services…</div>}

          {!loading && data && (
            <>
              {/* Services */}
              <div className="status-section-label">Services</div>
              <div className="status-rows">

                {/* Claude */}
                <div className="status-row">
                  <StatusDot status={data.anthropic.status} />
                  <div className="status-row-info">
                    <span className="status-row-name">Claude LLM</span>
                    <span className="status-row-detail">
                      {data.anthropic.model}
                      {data.anthropic.latency_ms !== undefined && (
                        <span className="status-latency">{fmtLatency(data.anthropic.latency_ms)}</span>
                      )}
                    </span>
                  </div>
                  {data.anthropic.status === 'rate_limited' ? (
                    <div style={{ textAlign: 'right' }}>
                      <StatusBadge status="rate_limited" text="rate limited" />
                      {data.anthropic.retry_in && (
                        <div className="status-retry">retry in {data.anthropic.retry_in}</div>
                      )}
                    </div>
                  ) : (
                    <StatusBadge status={data.anthropic.status}
                      text={data.anthropic.status === 'ok' ? 'online' : data.anthropic.status} />
                  )}
                </div>

                {/* Milvus */}
                <div className="status-row">
                  <StatusDot status={data.milvus.status} />
                  <div className="status-row-info">
                    <span className="status-row-name">Milvus Vector DB</span>
                    <span className="status-row-detail">
                      {data.milvus.status === 'ok'
                        ? `${data.milvus.collection} · ${data.milvus.chunks?.toLocaleString()} chunks`
                        : data.milvus.detail ?? ''}
                      {data.milvus.latency_ms !== undefined && (
                        <span className="status-latency">{fmtLatency(data.milvus.latency_ms)}</span>
                      )}
                    </span>
                  </div>
                  <StatusBadge status={data.milvus.status}
                    text={data.milvus.status === 'ok' ? 'online' : data.milvus.status} />
                </div>

                {/* TMDB */}
                <div className="status-row">
                  <StatusDot status={data.tmdb.status} />
                  <div className="status-row-info">
                    <span className="status-row-name">TMDB API</span>
                    <span className="status-row-detail">
                      {data.tmdb.status === 'ok' ? 'movie database' : data.tmdb.detail ?? ''}
                      {data.tmdb.latency_ms !== undefined && (
                        <span className="status-latency">{fmtLatency(data.tmdb.latency_ms)}</span>
                      )}
                    </span>
                  </div>
                  <StatusBadge status={data.tmdb.status}
                    text={data.tmdb.status === 'ok' ? 'online' : data.tmdb.status} />
                </div>

              </div>

              {/* Automated evals — public summary only (details live on /admin) */}
              {evals && evals.tiers.length > 0 && (
                <>
                  <div className="status-section-label" style={{ marginTop: 24 }}>Quality checks</div>
                  <div className="status-evals-sub">
                    The agents are evaluated automatically every night ({evals.model}, {hoursAgo(evals.updated)}).
                  </div>
                  <div className="status-rows">
                    {evals.tiers.map(t => (
                      <div key={t.tier} className="status-row">
                        <StatusDot status={t.status === 'pass' ? 'ok' : t.status === 'fail' ? 'error' : 'loading'} />
                        <div className="status-row-info">
                          <span className="status-row-name">{TIER_LABEL[t.tier] ?? t.name}</span>
                          <span className="status-row-detail">{t.headline} · {hoursAgo(t.at)}</span>
                        </div>
                        <StatusBadge status={t.status === 'pass' ? 'ok' : t.status === 'fail' ? 'error' : 'loading'}
                          text={t.status === 'pass' ? '✓ pass' : t.status === 'fail' ? '✕ regression' : 'new'} />
                      </div>
                    ))}
                  </div>
                </>
              )}

              {/* API Keys */}
              <div className="status-section-label" style={{ marginTop: 24 }}>API Keys</div>
              <div className="key-grid">
                <KeyRow label="Anthropic"   present={data.keys.anthropic} />
                <KeyRow label="OpenAI" present={data.keys.openai} />
                <KeyRow label="TMDB"   present={data.keys.tmdb} />
                <KeyRow label="Tavily" present={data.keys.tavily} />
              </div>

              {/* Info box for rate limit */}
              {data.anthropic.status === 'rate_limited' && (
                <div className="status-info-box">
                  <strong>⏳ Claude API rate limit reached.</strong>
                  <br />
                  The 100k tokens/day limit resets on a rolling 24-hour window.
                  {data.anthropic.retry_in && <> Try again in <strong>{data.anthropic.retry_in}</strong>.</>}
                  {' '}To remove this limit, upgrade at{' '}
                  <a href="https://console.anthropic.com/settings/billing" target="_blank" rel="noopener noreferrer">
                    console.anthropic.com
                  </a>.
                </div>
              )}

              {/* Info box for missing keys */}
              {Object.values(data.keys).some(v => !v) && (
                <div className="status-info-box status-info-box--warn">
                  <strong>🔑 Missing API keys</strong> — add them to{' '}
                  <code style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>cineai/backend/.env</code>
                  {' '}and restart the backend container.
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
