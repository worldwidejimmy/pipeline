/** The pipeline's steps, in the conversation: one quiet line per step, kept in the
 *  history (never erased), so a visitor can see the knowledge base being searched, the
 *  routing decision, each source fanning out, the guardrails acting and the final
 *  synthesis. Built purely from the SSE events the backend already streams. */
import { useState } from 'react'
import { PipelineEvent, AgentName, RagChunk } from '../types'

const AGENT_LABEL: Partial<Record<AgentName, string>> = {
  tmdb_agent: 'TMDB', rag_agent: 'Knowledge base', search_agent: 'Web search',
  music_agent: 'MusicBrainz', kb_lookup: 'Knowledge base',
}

const ROUTE_LABEL: Record<string, string> = {
  tmdb: 'TMDB', rag: 'knowledge base', search: 'web search', music: 'MusicBrainz',
}

function routeText(routing: string): string {
  if (routing === 'all') return 'all sources'
  return routing.split('+').map(r => ROUTE_LABEL[r] ?? r).join(' + ')
}

/** "ebert/fargo-1996" → "Ebert: Fargo 1996"; "docs/directors/christopher-nolan.md" → "Christopher Nolan guide" */
export function sourceLabel(src: string): string {
  const title = (s: string) => s.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  if (src.startsWith('ebert/great-movie-')) return `Ebert, Great Movies: ${title(src.slice(18))}`
  if (src.startsWith('ebert/')) return `Ebert: ${title(src.slice(6))}`
  const base = src.split('/').pop()?.replace(/\.md$/, '') ?? src
  return `${title(base)} guide`
}

function uniqueSources(chunks: RagChunk[], n = 3): string[] {
  return [...new Set(chunks.map(c => sourceLabel(c.source)))].slice(0, n)
}

interface Step { key: string; text: string; state: 'run' | 'ok' | 'none' | 'warn'; ms?: number }

export function buildSteps(events: PipelineEvent[]): { steps: Step[]; summary: string } {
  const steps: Step[] = []
  const at = new Map<string, number>()          // step key → index
  const put = (s: Step) => {
    const i = at.get(s.key)
    if (i === undefined) { at.set(s.key, steps.length); steps.push(s) } else steps[i] = { ...steps[i], ...s }
  }
  const started: Record<string, number> = {}
  let sources = 0

  for (const e of events) {
    switch (e.type) {
      case 'kb_lookup': {
        if (e.relevant) {
          put({ key: 'kb', state: 'ok', ms: e.latency_ms,
            text: `Searched the knowledge base: ${e.relevant_count} relevant passage${e.relevant_count === 1 ? '' : 's'} (${uniqueSources(e.chunks).join(', ')})` })
        } else {
          put({ key: 'kb', state: 'none', ms: e.latency_ms,
            text: 'Searched the knowledge base: nothing relevant to this question' })
        }
        break
      }
      case 'agent_start': {
        started[e.agent] = e.ts
        if (e.agent === 'supervisor_route') put({ key: 'route', state: 'run', text: 'Deciding which sources to ask…' })
        else if (e.agent === 'synthesise') put({ key: 'synth', state: 'run', text: `Combining ${sources || 'the'} source${sources === 1 ? '' : 's'} into one answer…` })
        else if (AGENT_LABEL[e.agent]) put({ key: e.agent, state: 'run', text: `${AGENT_LABEL[e.agent]}: working…` })
        break
      }
      case 'routing_decision':
        put({ key: 'route', state: 'ok', text: `Routing → ${routeText(e.routing)}${e.by === 'keyword' ? ' (keyword rule)' : ''}` })
        break
      case 'chunks_retrieved':
        put({ key: 'rag_agent', state: 'ok', text: `Knowledge base: read ${uniqueSources(e.chunks).join(', ')}` })
        break
      case 'tmdb_results': {
        const first = e.results[0]
        put({ key: 'tmdb_agent', state: 'ok',
          text: `TMDB: ${first ? `${first.title}${first.year ? ` (${first.year})` : ''}` : 'results'}${e.count > 1 ? ` + ${e.count - 1} more` : ''}` })
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
        const ms = e.latency_ms
        if (e.agent === 'supervisor_route') { put({ key: 'route', state: 'ok', text: steps[at.get('route')!]?.text ?? 'Routed', ms }); break }
        if (e.agent === 'synthesise') { put({ key: 'synth', state: 'ok', text: `Combined ${sources} source${sources === 1 ? '' : 's'} into one answer`, ms }); break }
        if (!AGENT_LABEL[e.agent]) break
        sources++
        const i = at.get(e.agent)
        const prev = i === undefined ? undefined : steps[i]
        put({ key: e.agent, ms, state: prev?.state === 'run' ? 'ok' : prev?.state ?? 'ok',
          text: prev && prev.state !== 'run' ? prev.text : `${AGENT_LABEL[e.agent]}: done` })
        break
      }
      case 'done': {
        const secs = (e.total_latency_ms / 1000).toFixed(1)
        const tok = (e.total_prompt_tokens + e.total_completion_tokens).toLocaleString()
        const i = at.get('synth')
        if (i !== undefined && e.model) steps[i] = { ...steps[i], text: `${steps[i].text} with ${e.model}` }
        return { steps, summary: `${sources} source${sources === 1 ? '' : 's'} · ${secs}s · ${tok} tokens${e.model ? ` · ${e.model}` : ''}` }
      }
    }
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
              <span className="step-text">{s.text}</span>
              {s.ms !== undefined && s.state !== 'run' && <span className="step-ms">{(s.ms / 1000).toFixed(1)}s</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
