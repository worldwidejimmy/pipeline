/** Evals & ops: the automated evals, the scheduled jobs and the ingest/embedding history,
 *  so a visitor can see what keeps this site honest and running. Public data only
 *  (backend /api/evals/summary and /api/ops/overview are allow-listed). */
import { useEffect, useState } from 'react'
import { apiFetch } from '../api'

interface EvalTier { tier: string; name: string; status: string; headline: string; at: string; model: string }
interface EvalSummary { updated?: string; model?: string; tiers: EvalTier[] }
interface LastRun { at?: string; ok?: boolean; note?: string }
interface Job { id: string; time: string; days: string; name: string; what: string; llm: string; last: LastRun }
interface IngestRun {
  at: string; ok: boolean; reviews: string[]; review_count: number; chunks: number
  embed_tokens: number; tokens_estimated: boolean; cost_usd: number; total_rows: number | null
}
interface Overview {
  jobs: Job[]
  ingest: {
    wiring: {
      source: string; chunking: { reviews: string; guides: string }
      embedding: { model: string; provider: string; dims: number; usd_per_million_tokens: number; query_cost_usd: number }
      index: string; query_time: string
    }
    runs: IngestRun[]
  }
}

// What each tier tests and how. Kept here (public copy); the runner is evals/run_evals.py.
const TIER_INFO: Record<string, { title: string; what: string; how: string; when: string }> = {
  t1: { title: 'Routing', what: 'Does the supervisor send each question to the right sources?', how: '48 labelled questions · Claude (router only)', when: 'nightly' },
  t2: { title: 'Retrieval', what: 'Do the right knowledge-base passages come back? hit@k and MRR, with a held-out set.', how: '34 questions with known answers · no LLM', when: 'nightly' },
  t3: { title: 'Answer quality', what: 'Are answers grounded in the sources, relevant and complete, and do they admit unknowns?', how: 'LLM judge, calibrated with deliberately bad answers', when: 'weekly (Sun)' },
  t4: { title: 'Canary questions', what: 'Known-good questions answered end to end through the whole pipeline.', how: 'full pipeline · Claude', when: 'nightly' },
  t5: { title: 'Cost & latency', what: 'Latency, tokens per question and prompt-cache share, from the canary runs.', how: 'measured, gated on a baseline', when: 'nightly' },
  t6: { title: 'MCP server', what: 'A real MCP client lists and calls every public tool; the tool contract is pinned; Host/Origin checks.', how: 'no LLM', when: 'nightly' },
  t7: { title: 'Red team', what: 'Prompt injections planted in TMDB, MusicBrainz, web and knowledge-base data, plus jailbreaks.', how: 'full pipeline · Claude', when: 'nightly' },
}

type Tab = 'evals' | 'jobs' | 'ingest'

const fmt = (n: number) => n.toLocaleString()
function ago(iso?: string): string {
  if (!iso) return 'never'
  const h = (Date.now() - new Date(iso).getTime()) / 3.6e6
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min ago`
  if (h < 48) return `${Math.round(h)}h ago`
  return `${Math.round(h / 24)} days ago`
}
function localTime(utc: string): string {
  const [h, m] = utc.split(':').map(Number)
  const d = new Date(); d.setUTCHours(h, m, 0, 0)
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}
const usd = (v: number) => v === 0 ? '$0' : v < 0.0001 ? `$${v.toFixed(7)}` : `$${v.toFixed(5)}`

export function OpsModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('evals')
  const [evals, setEvals] = useState<EvalSummary | null>(null)
  const [ops, setOps] = useState<Overview | null>(null)
  const [err, setErr] = useState(false)

  useEffect(() => {
    apiFetch('/api/evals/summary').then(r => r.json()).then(setEvals).catch(() => setErr(true))
    apiFetch('/api/ops/overview').then(r => r.json()).then(setOps).catch(() => setErr(true))
  }, [])

  const passing = evals?.tiers.filter(t => t.status === 'pass').length ?? 0

  return (
    <div className="modal-backdrop" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal" style={{ maxWidth: 760 }} role="dialog" aria-label="Evals and operations">
        <div className="modal-header">
          <div className="modal-title">
            <span className="modal-title-icon">🧪</span>
            <div>
              <div className="modal-title-text">Evals &amp; ops</div>
              <div className="modal-title-sub">
                What tests this site and keeps it running, automatically
                {evals?.tiers.length ? ` · evals ${passing}/${evals.tiers.length} passing` : ''}
              </div>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="obs-tabs" style={{ padding: '0 20px' }}>
          {([['evals', '🧪 Evals'], ['jobs', '⏰ Scheduled jobs'], ['ingest', '📥 Ingest & embeddings']] as [Tab, string][]).map(([id, label]) => (
            <button key={id} className={`obs-tab ${tab === id ? 'active' : ''}`} onClick={() => setTab(id)}>{label}</button>
          ))}
        </div>

        <div className="modal-body">
          {err && <div className="admin-error">Couldn't load everything; try again in a moment.</div>}

          {tab === 'evals' && (
            <div>
              <p className="rules-intro">
                Every night at 04:20 UTC an eval runner tests the live pipeline tier by tier and emails on any
                regression against a committed baseline. Each tier is a different question about the system.
                {evals?.model && <> Model under test: <code className="rules-agent-source">{evals.model}</code>.</>}
              </p>
              <div className="ops-list">
                {(evals?.tiers ?? []).map(t => {
                  const info = TIER_INFO[t.tier]
                  return (
                    <div key={t.tier} className="ops-card">
                      <div className="ops-card-head">
                        <span className={`ops-dot ${t.status === 'pass' ? 'ok' : 'bad'}`} aria-hidden />
                        <span className="ops-card-title">{info?.title ?? t.name}</span>
                        <code className="rules-route-key step-chip-route">{t.tier}</code>
                        <span className="ops-card-when">{info?.when} · {ago(t.at)}</span>
                      </div>
                      {info && <div className="ops-card-what">{info.what}</div>}
                      <div className="ops-card-meta">
                        <span className="ops-result">{t.status === 'pass' ? '✓' : '✗'} {t.headline}</span>
                        {info && <span className="rules-agent-source">{info.how}</span>}
                      </div>
                    </div>
                  )
                })}
              </div>
              <p className="rules-intro" style={{ marginTop: 14 }}>
                Also measured, kept with the code: the knowledge-base relevance gate's thresholds (68 labelled
                questions, held-out split) and a reranker that was tested and <em>not</em> shipped.
              </p>
            </div>
          )}

          {tab === 'jobs' && ops && (
            <div>
              <p className="rules-intro">
                Unattended jobs on the server's scheduler (cron). Times are UTC, with your local time alongside.
                The "LLM" column says whether a job uses a model at all: most ops work doesn't need one.
              </p>
              <div className="admin-table-wrap">
                <table className="ops-table">
                  <thead><tr><th>When</th><th>Job</th><th>LLM</th><th>Last run</th></tr></thead>
                  <tbody>
                    {ops.jobs.map(j => (
                      <tr key={j.id}>
                        <td className="ops-when"><strong>{j.time} UTC</strong><br /><span>{localTime(j.time)} local · {j.days}</span></td>
                        <td><strong>{j.name}</strong><br /><span className="ops-dim">{j.what}</span></td>
                        <td className="ops-dim">{j.llm}</td>
                        <td>
                          {j.last.at
                            ? <><span className={`ops-dot ${j.last.ok ? 'ok' : 'bad'}`} aria-hidden /> {ago(j.last.at)}<br /><span className="ops-dim">{j.last.note}</span></>
                            : <span className="ops-dim">not yet run</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {tab === 'ingest' && ops && (() => {
            const w = ops.ingest.wiring
            return (
              <div>
                <p className="rules-intro">How the knowledge base is built, and every nightly run that added to it.</p>
                <ol className="ops-flow">
                  <li><strong>Source</strong> · {w.source}</li>
                  <li><strong>Chunking</strong> · reviews: {w.chunking.reviews}. Guides: {w.chunking.guides}.</li>
                  <li><strong>Embedding</strong> · <code className="rules-agent-source">{w.embedding.model}</code> ({w.embedding.provider}),
                    {' '}{fmt(w.embedding.dims)} dimensions, ${w.embedding.usd_per_million_tokens} per million tokens. Only new passages are embedded.</li>
                  <li><strong>Index</strong> · {w.index}.</li>
                  <li><strong>At question time</strong> · {w.query_time} (≈ {usd(w.embedding.query_cost_usd)} per question).</li>
                </ol>
                <div className="status-section-label" style={{ marginTop: 16 }}>Nightly ingest history (08:00 UTC)</div>
                <div className="admin-table-wrap">
                  <table className="ops-table">
                    <thead><tr><th>Run</th><th>New reviews</th><th>Passages embedded</th><th>Tokens</th><th>Cost</th><th>Index size</th></tr></thead>
                    <tbody>
                      {ops.ingest.runs.map(r => (
                        <tr key={r.at} className={r.chunks ? '' : 'ops-quiet'}>
                          <td className="ops-when">{new Date(r.at).toLocaleDateString([], { month: 'short', day: 'numeric' })}{!r.ok && <span title="run failed"> ⚠</span>}</td>
                          <td>{r.review_count
                            ? <details><summary>{r.review_count}</summary><span className="ops-dim">{r.reviews.join(' · ')}</span></details>
                            : <span className="ops-dim">none new</span>}</td>
                          <td>{r.chunks ? fmt(r.chunks) : '0'}</td>
                          <td>{r.chunks ? `${r.tokens_estimated ? '≈ ' : ''}${fmt(r.embed_tokens)}` : '0'}</td>
                          <td>{usd(r.cost_usd)}</td>
                          <td>{r.total_rows ? fmt(r.total_rows) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="ops-dim" style={{ fontSize: 11, marginTop: 8 }}>
                  ≈ = estimated from the passage count (exact token counts are recorded from 2 Nov 2026).
                  Reviews already in the index are skipped, so most nights embed nothing.
                </p>
              </div>
            )
          })()}
          {(tab === 'jobs' || tab === 'ingest') && !ops && !err && <div className="modal-loading">Loading…</div>}
        </div>
      </div>
    </div>
  )
}
