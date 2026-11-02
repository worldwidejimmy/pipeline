/** The pipeline's steps, in the conversation: one quiet line per step, kept in the
 *  history (never erased), so a visitor can see the follow-up being understood, the
 *  knowledge base searched (and whether it had anything useful, and how that was
 *  decided), the routing, each source fanning out, what the combiner received, the
 *  guardrails acting, and the tokens each step spent. Built purely from the SSE events
 *  the backend streams (backend src/main.py:_stream_pipeline). */
import { useState } from 'react'
import { PipelineEvent, AgentName } from '../types'

const SOURCE_AGENTS: AgentName[] = ['tmdb_agent', 'rag_agent', 'search_agent', 'music_agent']
const AGENT_LABEL: Partial<Record<AgentName, string>> = {
  tmdb_agent: 'TMDB', rag_agent: 'Knowledge base', search_agent: 'Web search', music_agent: 'MusicBrainz',
}
const ROUTE_LABEL: Record<string, string> = {
  tmdb: 'TMDB', rag: 'knowledge base', search: 'web search', music: 'MusicBrainz',
}
const ROUTE_AGENTS: Record<string, AgentName> = {
  tmdb: 'tmdb_agent', rag: 'rag_agent', search: 'search_agent', music: 'music_agent',
}

const fmt = (n: number) => n.toLocaleString()

function routeText(routing: string): string {
  if (routing === 'all') return 'all sources'
  return routing.split('+').map(r => ROUTE_LABEL[r] ?? r).join(' + ')
}

type Passage = { source: string; text?: string }

/** A readable title for a passage: its own first line ("Roger Ebert Review: Fargo (1996)",
 *  "# Christopher Nolan — Director Study > …"), else the source path. */
export function passageTitle(c: Passage): string {
  const first = (c.text ?? '').split('\n')[0].trim()
  const ebert = first.match(/^Roger Ebert Review:\s*(.+)$/)
  if (ebert) return `Ebert on ${ebert[1]}`
  if (first.startsWith('#')) return first.split(' > ')[0].replace(/^#+\s*/, '')
  // Later passages of a review have no header line: rebuild "Ebert on Title (Year)" from the slug.
  const ebertSlug = c.source.match(/^ebert\/(?:amp\/)?(?:great-movie-)?(.+?)(?:-(?:movie|film|tv))?(?:-review)?(?:-(\d{4}))?$/)
  const words = (s: string) => s.replace(/[-_]/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase())
  if (ebertSlug) return `Ebert on ${words(ebertSlug[1])}${ebertSlug[2] ? ` (${ebertSlug[2]})` : ''}`
  return words(c.source.split('/').pop()!.replace(/\.md$/, ''))
}

function titles(chunks: Passage[], n = 3): string {
  const t = [...new Set(chunks.map(passageTitle))]
  return t.slice(0, n).join('; ') + (t.length > n ? ` + ${t.length - n} more` : '')
}

interface Step {
  key: string; text: string; detail?: string; state: 'run' | 'ok' | 'none' | 'warn'; ms?: number
  route?: string      // the router's raw decision, as a chip (same look as the Routing Rules screen)
  src?: string        // the step's data source / tool, as a chip
}

// Same names as the Routing Rules screen's agent cards (backend /api/rules).
const STEP_SOURCE: Record<string, string> = {
  understand: 'query rewrite', kb_lookup: 'Milvus vector DB', supervisor_route: 'router',
  tmdb_agent: 'themoviedb.org', rag_agent: 'Milvus vector DB', search_agent: 'Tavily API',
  music_agent: 'MusicBrainz', guard: 'LinkGuard',
}
interface Tok { inp: number; out: number; calls: number }

// What each step's non-LLM part costs, and what its Claude calls (if any) are for. Lookups
// themselves don't use tokens; the agents use Claude to choose a search and write a report.
const STEP_COST: Record<string, { lookup?: string; llm: string }> = {
  understand:       { llm: 'rewrite the follow-up' },
  kb_lookup:        { lookup: 'embedding ≈ $0.0000002 (OpenAI) + Milvus search: free', llm: 'grade the passages' },
  supervisor_route: { llm: 'classify the question' },
  tmdb_agent:       { lookup: 'TMDB API: free', llm: 'pick the search, write a report' },
  music_agent:      { lookup: 'MusicBrainz API: free', llm: 'pick the search, write a report' },
  search_agent:     { lookup: 'Tavily search: 1 API credit', llm: 'write a report' },
  rag_agent:        { lookup: 'passages from the lookup above: no new search', llm: 'summarise the passages' },
  synthesise:       { llm: 'write the answer' },
}

export function buildSteps(events: PipelineEvent[]): { steps: Step[]; summary: string } {
  const steps: Step[] = []
  const at = new Map<string, number>()
  const put = (s: Partial<Step> & { key: string }) => {
    const i = at.get(s.key)
    if (i === undefined) { at.set(s.key, steps.length); steps.push({ text: '', state: 'run', src: STEP_SOURCE[s.key], ...s }) }
    else steps[i] = { ...steps[i], ...s }
  }
  const get = (k: string) => { const i = at.get(k); return i === undefined ? undefined : steps[i] }

  // Tokens per node, from every LLM call's usage (attributed by the backend to its LangGraph node).
  const tok: Record<string, Tok> = {}
  for (const e of events) {
    if (e.type !== 'llm_end') continue
    const t = (tok[e.agent] ??= { inp: 0, out: 0, calls: 0 })
    t.inp += e.prompt_tokens; t.out += e.completion_tokens; t.calls++
  }

  const ran = new Set<string>()
  let routing = ''
  for (const e of events) {
    switch (e.type) {
      case 'agent_start':
        ran.add(e.agent)
        if (e.agent === 'kb_lookup') put({ key: 'kb_lookup', text: 'Searching the knowledge base…' })
        else if (e.agent === 'supervisor_route') put({ key: 'supervisor_route', text: 'Deciding which sources to ask…' })
        else if (e.agent === 'synthesise') put({ key: 'synthesise', text: 'Combining the sources into one answer…' })
        else if (AGENT_LABEL[e.agent]) put({ key: e.agent, text: `${AGENT_LABEL[e.agent]}: working…` })
        break
      case 'rewrite':
        put({ key: 'understand', state: 'ok', text: `Understood the follow-up as: “${e.standalone}”` })
        break
      case 'kb_lookup': {
        const best = e.best_score.toFixed(2)
        const how = e.by === 'grader'
          ? `best match ${best} is in the unsure band (${e.low}–${e.high}), so Claude graded the passages`
          : e.relevant ? `best match ${best} ≥ ${e.high}: clearly relevant, no grading needed`
          : `best match ${best} < ${e.low}: clearly unrelated, no grading needed`
        put({ key: 'kb_lookup', state: e.relevant ? 'ok' : 'none', ms: e.latency_ms, detail: how,
          text: e.relevant
            ? `Knowledge base: ${e.relevant_count} useful passage${e.relevant_count === 1 ? '' : 's'} (${titles(e.chunks)})`
            : 'Knowledge base: nothing useful for this question' })
        break
      }
      case 'routing_decision':
        routing = e.routing
        put({ key: 'supervisor_route', state: 'ok', route: e.routing,
          text: `Routing → ${routeText(e.routing)}${e.by === 'keyword' ? ' (keyword rule, no LLM call)' : ''}` })
        break
      case 'chunks_retrieved':
        put({ key: 'rag_agent', state: 'ok', text: `Knowledge base: summarised ${titles(e.chunks)}` })
        break
      case 'tmdb_results': {
        const f = e.results[0]
        put({ key: 'tmdb_agent', state: 'ok',
          text: `TMDB: ${f ? `${f.title}${f.year ? ` (${f.year})` : ''}` : 'results'}${e.count > 1 ? ` + ${e.count - 1} more` : ''}` })
        break
      }
      case 'music_results':
        put({ key: 'music_agent', state: 'ok',
          text: `MusicBrainz: ${e.artist ?? 'artist'}${e.albums?.length ? `, ${e.albums.length} albums` : ''}` })
        break
      case 'search_results':
        put({ key: 'search_agent', state: e.count ? 'ok' : 'none',
          text: e.count ? `Web search: ${e.count} results (${e.domains.join(', ')})` : 'Web search: no results' })
        break
      case 'guardrail':
        put({ key: 'guard', state: 'warn',
          text: `Guardrail: removed ${e.count} link${e.count === 1 ? '' : 's'} to unapproved sites (${e.domains.join(', ')})` })
        break
      case 'agent_end': {
        if (e.agent === 'understand') break                       // shown only when it rewrote
        const s = get(e.agent)
        if (!s) break
        put({ key: e.agent, ms: e.latency_ms, state: s.state === 'run' ? 'ok' : s.state,
          text: s.state !== 'run' ? s.text : `${AGENT_LABEL[e.agent] ?? e.agent}: done` })
        break
      }
    }
  }

  // Routing as the knowledge-base gate adjusted it (backend graph/pipeline.py:apply_kb).
  const r = get('supervisor_route')
  if (r && routing) {
    const chosen = routing === 'all' ? SOURCE_AGENTS : routing.split('+').map(x => ROUTE_AGENTS[x])
    const kb = get('kb_lookup')
    if (ran.has('rag_agent') && !chosen.includes('rag_agent')) r.text += ', + knowledge base (it had useful passages)'
    if (chosen.includes('rag_agent') && !ran.has('rag_agent') && kb?.state === 'none') r.text += ', − knowledge base (nothing useful)'
  }

  // What the combiner received: each source's report, by its size in tokens.
  const sources = SOURCE_AGENTS.filter(a => ran.has(a))
  const synth = get('synthesise')
  if (synth && synth.state !== 'run') {
    const parts = sources.map(a => `${AGENT_LABEL[a]}${tok[a] ? ` (${fmt(tok[a].out)}-token report)` : ''}`)
    synth.text = `Combined ${parts.join(' + ')} into the answer`
  }

  // Per-step cost: the free/paid lookup, then Claude calls with their purpose and tokens.
  for (const s of steps) {
    if (s.key === 'guard' || s.state === 'run') continue
    const t = tok[s.key], c = STEP_COST[s.key]
    const llm = t ? `Claude ×${t.calls} (${c?.llm ?? 'LLM'}): ${fmt(t.inp)} in · ${fmt(t.out)} out tokens` : 'no Claude call'
    const spent = [c?.lookup, llm].filter(Boolean).join(' · ')
    s.detail = s.detail ? `${s.detail}\n${spent}` : spent
  }

  const done = events.find(e => e.type === 'done')
  if (done && done.type === 'done') {
    if (synth && done.model) synth.src = done.model
    const cached = done.cache_read_tokens
      ? ` (${Math.round(100 * done.cache_read_tokens / Math.max(1, done.total_prompt_tokens))}% cached)` : ''
    return { steps, summary: [
      `${sources.length} source${sources.length === 1 ? '' : 's'}`,
      `${(done.total_latency_ms / 1000).toFixed(1)}s`,
      `${fmt(done.total_prompt_tokens)} in${cached} / ${fmt(done.total_completion_tokens)} out tokens`,
      done.model ?? '',
    ].filter(Boolean).join(' · ') }
  }
  return { steps, summary: '' }
}

export function StepTrace({ events, live = false, latest = false }:
  { events: PipelineEvent[]; live?: boolean; latest?: boolean }) {
  const { steps, summary } = buildSteps(events)
  // The live and the latest turn are open; older turns fold to one line (click to reopen).
  const [override, setOverride] = useState<boolean | null>(null)
  if (!steps.length) return null
  const expanded = live || (override ?? latest)
  return (
    <div className="step-trace" aria-live={live ? 'polite' : undefined}>
      {!live && summary && (
        <button className="step-toggle" onClick={() => setOverride(!expanded)} aria-expanded={expanded}>
          {expanded ? '▾' : '▸'} {summary}
        </button>
      )}
      {expanded && (
        <ol className="step-list">
          {steps.map(s => (
            <li key={s.key} className={`step step--${s.state}`}>
              <span className="step-mark" aria-hidden>{s.state === 'run' ? '' : s.state === 'none' ? '○' : s.state === 'warn' ? '⚠' : '✓'}</span>
              <span className="step-body">
                <span className="step-text">
                  {s.text}
                  {s.route && <code className="rules-route-key step-chip-route">{s.route}</code>}
                  {s.src && <span className="rules-agent-source step-chip">{s.src}</span>}
                </span>
                {s.detail && <span className="step-detail">{s.detail}</span>}
              </span>
              {s.ms !== undefined && s.state !== 'run' && <span className="step-ms">{(s.ms / 1000).toFixed(1)}s</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
