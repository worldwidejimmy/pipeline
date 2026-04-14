import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import { PipelineEvent, TmdbMovie } from './types'
import { PipelineGraph } from './components/PipelineGraph'
import { AgentTimeline } from './components/AgentTimeline'
import { EventLog } from './components/EventLog'
import { MetricsBar } from './components/MetricsBar'
import { ChunksPanel } from './components/ChunksPanel'

const EXAMPLE_QUERIES = [
  "What are trending movies this week?",
  "Tell me about Inception — cast, rating, and themes",
  "Top sci-fi films of all time",
  "Christopher Nolan directing style",
  "What's new in theaters right now?",
  "Best horror movies with a rating above 8",
]

type ObsTab = 'graph' | 'timeline' | 'events' | 'context'

export default function App() {
  const [query, setQuery]           = useState('')
  const [events, setEvents]         = useState<PipelineEvent[]>([])
  const [answer, setAnswer]         = useState('')
  const [isStreaming, setStreaming]  = useState(false)
  const [activeTab, setActiveTab]   = useState<ObsTab>('graph')
  const [trending, setTrending]     = useState<TmdbMovie[]>([])

  const esRef        = useRef<EventSource | null>(null)
  const answerEndRef = useRef<HTMLDivElement>(null)
  const eventLogRef  = useRef<HTMLDivElement>(null)

  // Auto-scroll answer
  useEffect(() => {
    answerEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [answer])

  // Auto-scroll event log
  useEffect(() => {
    if (eventLogRef.current) {
      eventLogRef.current.scrollTop = eventLogRef.current.scrollHeight
    }
  }, [events])

  // Load trending on mount
  useEffect(() => {
    fetch('/api/trending')
      .then(r => r.json())
      .then(d => setTrending(d.results?.slice(0, 6) ?? []))
      .catch(() => {})
  }, [])

  const runQuery = useCallback((q: string) => {
    if (!q.trim() || isStreaming) return

    // Close any existing stream
    esRef.current?.close()

    setEvents([])
    setAnswer('')
    setStreaming(true)
    setActiveTab('graph')

    const es = new EventSource(`/api/query?q=${encodeURIComponent(q.trim())}`)
    esRef.current = es

    es.addEventListener('pipeline_start',    (e) => addEvent('pipeline_start',    e))
    es.addEventListener('agent_start',       (e) => addEvent('agent_start',       e))
    es.addEventListener('agent_end',         (e) => addEvent('agent_end',         e))
    es.addEventListener('routing_decision',  (e) => addEvent('routing_decision',  e))
    es.addEventListener('llm_start',         (e) => addEvent('llm_start',         e))
    es.addEventListener('llm_end',           (e) => addEvent('llm_end',           e))
    es.addEventListener('chunks_retrieved',  (e) => addEvent('chunks_retrieved',  e))
    es.addEventListener('tmdb_results',      (e) => addEvent('tmdb_results',      e))
    es.addEventListener('error_event',       (e) => addEvent('error',             e))

    es.addEventListener('token', (e: MessageEvent) => {
      const payload = JSON.parse(e.data)
      addEvent('token', e)
      if (payload.is_final) {
        setAnswer(prev => prev + payload.content)
      }
    })

    es.addEventListener('done', (e: MessageEvent) => {
      addEvent('done', e)
      setStreaming(false)
      es.close()
    })

    es.onerror = () => {
      setStreaming(false)
      es.close()
    }

    function addEvent(type: string, e: MessageEvent) {
      try {
        const payload = JSON.parse(e.data)
        setEvents(prev => [...prev, { ...payload, type }])
      } catch {/* ignore parse errors */}
    }
  }, [isStreaming])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    runQuery(query)
  }

  return (
    <div className="app">
      {/* ── Left panel ─────────────────────────────────────────────────────── */}
      <div className="panel-left">
        {/* Header */}
        <div className="header">
          <span className="header-logo">🎬</span>
          <span className="header-title">Cine<span>AI</span></span>
          <span className="header-sub">LangGraph · Groq · TMDB · RAG</span>
        </div>

        {/* Query input */}
        <div className="query-section">
          <div className="query-label">Ask about any movie or TV show</div>
          <form className="query-form" onSubmit={handleSubmit}>
            <input
              className="query-input"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="e.g. What are the best sci-fi films of all time?"
              disabled={isStreaming}
            />
            <button className="query-btn" type="submit" disabled={isStreaming || !query.trim()}>
              {isStreaming ? '…' : 'Ask'}
            </button>
          </form>
          <div className="example-queries">
            {EXAMPLE_QUERIES.map(q => (
              <button
                key={q}
                className="example-chip"
                onClick={() => { setQuery(q); runQuery(q) }}
                disabled={isStreaming}
              >
                {q}
              </button>
            ))}
          </div>
        </div>

        {/* Answer area */}
        <div className="answer-section">
          {!answer && !isStreaming && (
            <div>
              {/* Trending movies grid */}
              {trending.length > 0 && (
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.8px', marginBottom: 12 }}>
                    🔥 Trending This Week
                  </div>
                  <div className="movie-cards">
                    {trending.map(m => (
                      <div
                        key={m.id}
                        className="movie-card"
                        style={{ cursor: 'pointer' }}
                        onClick={() => { setQuery(`Tell me about ${m.title}`); runQuery(`Tell me about ${m.title}`) }}
                      >
                        {m.poster
                          ? <img src={m.poster} alt={m.title} />
                          : <div className="movie-card-poster-placeholder">🎬</div>
                        }
                        <div className="movie-card-info">
                          <div className="movie-card-title">{m.title}</div>
                          <div className="movie-card-meta">{m.year} · {m.media_type}</div>
                          {m.rating && <div className="movie-card-rating">⭐ {m.rating.toFixed(1)}</div>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {trending.length === 0 && (
                <div className="answer-placeholder">
                  Ask a question about any movie or TV show.<br />
                  The pipeline will route to TMDB, RAG, or web search automatically.
                </div>
              )}
            </div>
          )}

          {(answer || isStreaming) && (
            <div className="answer-content">
              <ReactMarkdown>{answer}</ReactMarkdown>
              {isStreaming && <span className="cursor-blink" />}
              <div ref={answerEndRef} />
            </div>
          )}
        </div>
      </div>

      {/* ── Right panel: Observability ──────────────────────────────────────── */}
      <div className="panel-right">
        {/* Obs header */}
        <div className="obs-header">
          <span className="obs-title">Pipeline Observability</span>
          <span className={`obs-badge ${isStreaming ? 'live' : ''}`}>
            {isStreaming ? 'LIVE' : events.length > 0 ? 'COMPLETE' : 'IDLE'}
          </span>
          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>
            {events.length} events
          </span>
        </div>

        {/* Tabs */}
        <div className="obs-tabs">
          {([
            ['graph',    '🗺 Graph'],
            ['timeline', '⏱ Timeline'],
            ['events',   '📋 Events'],
            ['context',  '📄 Context'],
          ] as [ObsTab, string][]).map(([id, label]) => (
            <button
              key={id}
              className={`obs-tab ${activeTab === id ? 'active' : ''}`}
              onClick={() => setActiveTab(id)}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="obs-content" ref={activeTab === 'events' ? eventLogRef : undefined}>
          {activeTab === 'graph'    && <PipelineGraph events={events} />}
          {activeTab === 'timeline' && <AgentTimeline events={events} />}
          {activeTab === 'events'   && <EventLog events={events} />}
          {activeTab === 'context'  && <ChunksPanel events={events} />}
        </div>

        {/* Metrics bar always visible */}
        <MetricsBar events={events} isStreaming={isStreaming} />
      </div>
    </div>
  )
}
