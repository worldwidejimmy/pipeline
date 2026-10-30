/**
 * /admin — the operator's view: are the unattended agents still right?
 *
 * Every byte of data here comes from admin-gated endpoints (/api/admin/*, checked
 * server-side). This page's CODE ships in the public bundle, which is fine — it
 * holds no data. Sections: evals (latest + trend + failing cases), the live model,
 * cost & latency, answer feedback, MCP usage, usage & abuse, and service health.
 */
import { useCallback, useEffect, useState } from 'react'
import { apiFetch, clearAccessToken, getAccessToken, setAccessToken } from './api'
import { AdminUsagePanel } from './components/AdminUsagePanel'
import { Sparkline } from './components/Sparkline'

type Tier = 't1' | 't2' | 't3' | 't4' | 't5' | 't6' | 't7'
type Row = Record<string, any>

interface EvalsData {
  latest: { updated?: string; model?: string; tiers?: Partial<Record<Tier, Row>> }
  history: Record<Tier, Row[]>
  cases: Record<Tier, Row[]>
}

interface ModelInfo {
  tier: string; model: string; source: string; newest_in_tier: string | null
  resolved_at: number; error: string | null; thinking_types: string[]
}

// What each tier's trend line plots, and how to print a value.
const TIERS: { id: Tier; title: string; what: string; metric: string; fmt: (v: number) => string }[] = [
  { id: 't1', title: 'Routing', what: 'Supervisor sends each question to the right agents', metric: 'accuracy', fmt: v => `${Math.round(v * 100)}%` },
  { id: 't2', title: 'Retrieval', what: 'The right knowledge-base passages come back (no LLM)', metric: 'mrr', fmt: v => `MRR ${v.toFixed(3)}` },
  { id: 't3', title: 'Answer quality', what: 'LLM judge: grounded, relevant, complete; admits unknowns', metric: 'pass_rate', fmt: v => `${Math.round(v * 100)}%` },
  { id: 't4', title: 'Canaries', what: 'Known-good questions end to end', metric: 'pass_rate', fmt: v => `${Math.round(v * 100)}%` },
  { id: 't5', title: 'Cost & latency', what: 'From the canary runs', metric: 'p50_ms', fmt: v => `${(v / 1000).toFixed(1)}s p50` },
  { id: 't7', title: 'Red team', what: 'Prompt injection planted in TMDB/MusicBrainz/web/KB data, plus jailbreaks', metric: 'pass_rate', fmt: v => `${Math.round(v * 100)}%` },
  { id: 't6', title: 'MCP server', what: 'Real MCP client: tool contract pinned, each tool, Origin guard', metric: 'pass_rate', fmt: v => `${Math.round(v * 100)}%` },
]

function ago(iso?: string | number): string {
  if (!iso) return '—'
  const t = typeof iso === 'number' ? iso * 1000 : Date.parse(iso)
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 90) return 'just now'
  if (s < 5400) return `${Math.round(s / 60)}m ago`
  if (s < 172800) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

function StatusPill({ status }: { status?: string }) {
  const [cls, text] = status === 'pass' ? ['badge-ok', '✓ pass']
    : status === 'fail' ? ['badge-err', '✕ regression']
    : ['badge-loading', '• no baseline']
  return <span className={`status-badge ${cls}`}>{text}</span>
}

function SignIn({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    const r = await fetch('/api/auth', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw }),
    })
    if (r.ok) { setAccessToken((await r.json()).token); onDone() }
    else setErr(r.status === 429 ? 'Too many attempts — try again later.' : 'Wrong password.')
  }
  return (
    <form className="adm-signin" onSubmit={submit}>
      <div className="adm-signin-title">🛡️ Admin sign-in</div>
      <input type="password" value={pw} onChange={e => setPw(e.target.value)} placeholder="Password" autoFocus />
      <button type="submit" disabled={!pw}>Sign in</button>
      {err && <div className="admin-error">{err}</div>}
    </form>
  )
}

/** The actionable part of a tier: which cases failed, and what came back instead. */
function Failures({ tier, cases }: { tier: Tier; cases: Row[] }) {
  const bad = cases.filter(c => tier === 't2' ? !c.hit || (c.missing_content?.length ?? 0) > 0
    : tier === 't1' ? !c.strict
    // calibration answers are SUPPOSED to fail sometimes; the question is whether the judge agreed
    : tier === 't3' && c.kind === 'calibration' ? !c.judge_agrees
    : c.ok === false)
  if (tier === 't5' || !cases.length) return null
  if (!bad.length) return <div className="adm-dim">All {cases.length} cases passed in the latest run.</div>
  return (
    <details className="adm-fail">
      <summary>{bad.length} case{bad.length !== 1 ? 's' : ''} to look at</summary>
      <ul>
        {bad.map(c => (
          <li key={c.id}>
            <code>{c.id}</code> <span className="adm-q">{c.q ? `“${c.q}”` : c.tool}</span>
            <div className="adm-dim">
              {tier === 't1' && <>expected <b>{c.expect}</b>, got <b>{c.got ?? 'error'}</b>{c.ok ? ' (acceptable alternate)' : ''}</>}
              {tier === 't2' && (c.hit ? `found the doc but missing: ${c.missing_content.join(', ')}` : `miss — got ${c.got?.slice(0, 3).join(', ') || 'nothing'}`)}
              {tier === 't3' && (c.error || `${c.kind === 'calibration' ? 'JUDGE DISAGREED with known verdict' : c.kind} · G${c.verdict?.groundedness} R${c.verdict?.relevance} C${c.verdict?.completeness} — ${c.verdict?.reason ?? ''}`)}
              {(tier === 't4' || tier === 't6' || tier === 't7') && c.problems?.join('; ')}
            </div>
          </li>
        ))}
      </ul>
    </details>
  )
}

function EvalCard({ t, data }: { t: typeof TIERS[number]; data: EvalsData }) {
  const r = data.latest.tiers?.[t.id]
  const hist = (data.history[t.id] ?? []).filter(h => typeof h[t.metric] === 'number')
  return (
    <div className={`adm-card ${r?.status === 'fail' ? 'adm-card--fail' : ''}`}>
      <div className="adm-card-head">
        <span className="adm-card-title">{t.id.toUpperCase()} · {t.title}</span>
        {r && <StatusPill status={r.status} />}
      </div>
      <div className="adm-dim">{t.what}</div>
      {r ? (
        <>
          <div className="adm-headline">{r.headline}</div>
          <div className="adm-trend">
            <Sparkline
              values={hist.map(h => h[t.metric])}
              labels={hist.map(h => `${(h.at ?? '').slice(0, 10)} · ${t.fmt(h[t.metric])} · ${h.model ?? ''}`)}
              ariaLabel={`${t.title} trend, ${hist.length} runs`}
            />
            <span className="adm-dim">{hist.length} run{hist.length !== 1 ? 's' : ''} · last {ago(r.at)}</span>
          </div>
          <div className="adm-dim adm-model">{r.model}{t.id === 't3' && r.judge_model ? ` · judged by ${r.judge_model}` : ''}</div>
          {(r.gate ?? []).filter((m: string) => !m.startsWith('no baseline')).map((m: string) => (
            <div key={m} className={r.status === 'fail' ? 'admin-error' : 'adm-dim'}>{m}</div>
          ))}
          <Failures tier={t.id} cases={data.cases[t.id] ?? []} />
        </>
      ) : <div className="adm-dim">Not run yet.</div>}
    </div>
  )
}

interface TracingInfo { enabled: boolean; project: string; eval_project: string; sample_rate: number; redaction: string[] }

function ModelSection({ m, tr }: { m: ModelInfo | null; tr: TracingInfo | null }) {
  if (!m) return <div className="adm-dim">Loading…</div>
  const newer = m.newest_in_tier && m.newest_in_tier !== m.model
  return (
    <dl className="adm-kv">
      <dt>Cost tier</dt><dd>{m.tier}</dd>
      <dt>Live model</dt><dd><code>{m.model}</code></dd>
      <dt>Chosen by</dt><dd>{m.source === 'auto' ? 'auto — newest model in the tier'
        : m.source === 'pinned' ? 'MODEL_PIN (manual pin)' : 'fallback — Models API unreachable'}</dd>
      <dt>Newest in tier</dt><dd>{m.newest_in_tier ?? '—'}{newer && <span className="status-badge badge-warn"> newer available</span>}</dd>
      <dt>Thinking modes</dt><dd>{m.thinking_types.join(', ') || '—'}</dd>
      <dt>Resolved</dt><dd>{ago(m.resolved_at)}{m.error && <span className="admin-error"> {m.error}</span>}</dd>
      <dt>Tracing</dt><dd>{!tr ? '—' : tr.enabled
        ? <>LangSmith <code>{tr.project}</code> ({Math.round(tr.sample_rate * 100)}% sampled) · evals → <code>{tr.eval_project}</code> · redacts {tr.redaction.join(' ')}</>
        : 'off'}</dd>
    </dl>
  )
}

function CostSection({ data }: { data: EvalsData | null }) {
  const hist = data?.history.t5 ?? []
  const r = data?.latest.tiers?.t5
  if (!r) return <div className="adm-dim">No canary runs yet.</div>
  const tiles: [string, string, number[], (v: number) => string][] = [
    ['p50 latency', `${(r.p50_ms / 1000).toFixed(1)}s`, hist.map(h => h.p50_ms), v => `${(v / 1000).toFixed(1)}s`],
    ['p95 latency', `${(r.p95_ms / 1000).toFixed(1)}s`, hist.map(h => h.p95_ms), v => `${(v / 1000).toFixed(1)}s`],
    ['tokens / query', r.tokens_per_query.toLocaleString(), hist.map(h => h.tokens_per_query), v => v.toLocaleString()],
    ['$ / query', r.usd_per_query != null ? `$${r.usd_per_query.toFixed(4)}` : '—',
      hist.filter(h => h.usd_per_query != null).map(h => h.usd_per_query), v => `$${v.toFixed(4)}`],
  ]
  return (
    <div className="adm-tiles">
      {tiles.map(([label, val, series, f]) => (
        <div key={label} className="adm-tile">
          <div className="adm-dim">{label}</div>
          <div className="adm-tile-val">{val}</div>
          <Sparkline values={series} labels={series.map(f)} ariaLabel={`${label} trend`} width={110} />
        </div>
      ))}
    </div>
  )
}

interface McpDay { day: string; calls: number; errors: number; callers: number; by_tool: Record<string, number> }

/** Who is using the MCP server: per-day calls from the audit log (callers are salted
 *  IP hashes, so this counts distinct clients without storing who they are). */
function McpSection() {
  const [d, setD] = useState<{ enabled: boolean; agent_endpoint: boolean; days: McpDay[] } | null>(null)
  useEffect(() => { apiFetch('/api/admin/mcp').then(r => r.json()).then(setD).catch(() => {}) }, [])
  if (!d) return <div className="adm-dim">Loading…</div>
  return (
    <>
      <dl className="adm-kv">
        <div className="adm-kv-row"><dt>Public tools</dt><dd>
          <span className={`status-badge ${d.enabled ? 'badge-ok' : 'badge-err'}`}>{d.enabled ? '✓ on' : '✕ disabled'}</span>
          {' '}<code>https://smartmoviesearch.com/mcp</code></dd></div>
        <div className="adm-kv-row"><dt>Agent (OAuth)</dt><dd>
          <span className={`status-badge ${d.agent_endpoint ? 'badge-ok' : 'badge-loading'}`}>{d.agent_endpoint ? '✓ on' : '• not configured'}</span>
          {' '}<code>/mcp/agent</code></dd></div>
      </dl>
      {d.days.length === 0 ? <div className="adm-dim">No MCP calls yet.</div> : (
        <div className="admin-table-wrap" style={{ marginTop: 12 }}><table className="admin-table">
          <thead><tr><th>Day (UTC)</th><th>Calls</th><th>Errors</th><th>Callers</th><th>By tool</th></tr></thead>
          <tbody>
            {[...d.days].reverse().map(x => (
              <tr key={x.day}>
                <td>{x.day}</td><td>{x.calls}</td><td>{x.errors}</td><td>{x.callers}</td>
                <td className="adm-dim">{Object.entries(x.by_tool).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(' · ')}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </>
  )
}

interface FeedbackData {
  days: number; up: number; down: number
  by_route: Record<string, { up: number; down: number }>
  recent_down: { ts: string; question: string; comment: string; answer_head: string; route: string | null; model: string | null }[]
}

/** 👍/👎 from visitors (redacted server-side). 👎 rows are candidate eval cases:
 *  `make feedback-triage` drafts them for review; nothing enters the public datasets automatically. */
function FeedbackSection() {
  const [d, setD] = useState<FeedbackData | null>(null)
  useEffect(() => { apiFetch('/api/admin/feedback').then(r => r.json()).then(setD).catch(() => {}) }, [])
  if (!d) return <div className="adm-dim">Loading…</div>
  const total = d.up + d.down
  return (
    <>
      <div className="adm-tiles">
        <div className="adm-tile"><div className="adm-dim">👍 helpful</div><div className="adm-tile-val">{d.up}</div></div>
        <div className="adm-tile"><div className="adm-dim">👎 not helpful</div><div className="adm-tile-val">{d.down}</div></div>
        <div className="adm-tile"><div className="adm-dim">satisfaction</div><div className="adm-tile-val">{total ? `${Math.round((d.up / total) * 100)}%` : '—'}</div></div>
      </div>
      {Object.keys(d.by_route).length > 0 && (
        <div className="adm-dim" style={{ marginTop: 10 }}>
          by route: {Object.entries(d.by_route).map(([k, v]) => `${k} 👍${v.up} 👎${v.down}`).join(' · ')}
        </div>
      )}
      {d.recent_down.length === 0 ? <div className="adm-dim" style={{ marginTop: 10 }}>No 👎 in the last {d.days} days.</div> : (
        <details className="adm-fail" open>
          <summary>{d.recent_down.length} recent 👎 — candidates for the eval datasets (<code>make feedback-triage</code>)</summary>
          <ul>
            {d.recent_down.map(r => (
              <li key={r.ts + r.question}>
                <span className="adm-q">“{r.question}”</span>
                <div className="adm-dim">{ago(r.ts)} · {r.route ?? '?'} · {r.model ?? '?'}{r.comment && <> · <b>{r.comment}</b></>}</div>
                <div className="adm-dim">answer: {r.answer_head.slice(0, 220)}{r.answer_head.length > 220 ? '…' : ''}</div>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  )
}

function HealthSection() {
  const [s, setS] = useState<Row | null>(null)
  useEffect(() => { apiFetch('/api/status').then(r => r.json()).then(setS).catch(() => setS({})) }, [])
  if (!s) return <div className="adm-dim">Checking…</div>
  const rows: [string, Row | undefined][] = [['Claude LLM', s.anthropic], ['Milvus', s.milvus], ['TMDB', s.tmdb]]
  return (
    <dl className="adm-kv">
      {rows.map(([name, v]) => (
        <div key={name} className="adm-kv-row">
          <dt>{name}</dt>
          <dd>
            <span className={`status-badge ${v?.status === 'ok' ? 'badge-ok' : 'badge-err'}`}>{v?.status === 'ok' ? '✓ ok' : `✕ ${v?.status ?? 'unknown'}`}</span>
            {' '}<span className="adm-dim">{[v?.model, v?.latency_ms != null && `${v.latency_ms}ms`, v?.chunks != null && `${v.chunks.toLocaleString()} chunks`, v?.detail].filter(Boolean).join(' · ')}</span>
          </dd>
        </div>
      ))}
    </dl>
  )
}

export default function AdminPage() {
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [evals, setEvals] = useState<EvalsData | null>(null)
  const [model, setModel] = useState<ModelInfo | null>(null)
  const [tracing, setTracing] = useState<TracingInfo | null>(null)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', localStorage.getItem('sms-theme') ?? 'dark')
    document.title = 'Admin · SmartMovieSearch'
    const robots = document.createElement('meta')
    robots.name = 'robots'; robots.content = 'noindex, nofollow'
    document.head.appendChild(robots)
  }, [])

  const load = useCallback(async () => {
    if (!getAccessToken()) { setAuthed(false); return }
    const r = await apiFetch('/api/admin/evals')
    if (r.status === 403) { setAuthed(false); return }
    setAuthed(true)
    setEvals(await r.json())
    apiFetch('/api/admin/model').then(r => r.json()).then(setModel).catch(() => {})
    apiFetch('/api/admin/tracing').then(r => r.json()).then(setTracing).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="adm">
      <header className="adm-header">
        <a href="/" className="adm-back">← SmartMovieSearch</a>
        <h1>Admin</h1>
        {authed && <button className="admin-btn" onClick={() => { clearAccessToken(); setAuthed(false) }}>Sign out</button>}
      </header>

      {authed === null && <div className="adm-dim">Loading…</div>}
      {authed === false && <SignIn onDone={load} />}

      {authed && (
        <>
          <section className="adm-section">
            <h2>Evals <span className="adm-dim">· nightly 04:20 UTC, judge weekly · updated {ago(evals?.latest.updated)} · {evals?.latest.model}</span></h2>
            <div className="adm-grid">
              {evals && TIERS.filter(t => t.id !== 't5').map(t => <EvalCard key={t.id} t={t} data={evals} />)}
            </div>
          </section>
          <section className="adm-section">
            <h2>Cost &amp; latency <span className="adm-dim">· T5, from the canary runs</span></h2>
            <CostSection data={evals} />
          </section>
          <section className="adm-section">
            <h2>Model</h2>
            <ModelSection m={model} tr={tracing} />
          </section>
          <section className="adm-section">
            <h2>Answer feedback <span className="adm-dim">· 👍/👎 from visitors, last 30 days (redacted)</span></h2>
            <FeedbackSection />
          </section>
          <section className="adm-section">
            <h2>MCP server <span className="adm-dim">· last 7 days, from the audit log</span></h2>
            <McpSection />
          </section>
          <section className="adm-section">
            <h2>Usage &amp; abuse</h2>
            <AdminUsagePanel />
          </section>
          <section className="adm-section">
            <h2>Service health</h2>
            <HealthSection />
          </section>
        </>
      )}
    </div>
  )
}
