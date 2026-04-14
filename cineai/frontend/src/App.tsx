import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import { PipelineEvent, TmdbMovie } from './types'
import { PipelineGraph } from './components/PipelineGraph'
import { AgentTimeline } from './components/AgentTimeline'
import { EventLog } from './components/EventLog'
import { MetricsBar } from './components/MetricsBar'
import { ChunksPanel } from './components/ChunksPanel'

const EXAMPLE_QUERIES = [
  { icon: '🕵️', text: 'Show me good bank heist movies' },
  { icon: '🔥', text: 'Trending movies this week' },
  { icon: '🎭', text: 'Tell me about Inception — cast, rating, themes' },
  { icon: '🚀', text: 'Top sci-fi films of all time' },
  { icon: '🎬', text: "Christopher Nolan's directing style" },
  { icon: '👻', text: 'Best horror movies rated above 8' },
]

type ObsTab = 'graph' | 'timeline' | 'events' | 'context'

interface Turn {
  q: string
  a: string
}

function generateThreadId(): string {
  return `thread_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

export default function App() {
  const [query, setQuery]         = useState('')
  const [events, setEvents]       = useState<PipelineEvent[]>([])
  const [answer, setAnswer]       = useState('')
  const [isStreaming, setStream]  = useState(false)
  const [activeTab, setActiveTab] = useState<ObsTab>('graph')
  const [trending, setTrending]   = useState<TmdbMovie[]>([])
  const [history, setHistory]     = useState<Turn[]>([])
  const [threadId, setThreadId]   = useState(generateThreadId)
  const [turnNumber, setTurnNum]  = useState(0)

  const esRef        = useRef<EventSource | null>(null)
  const answerEndRef = useRef<HTMLDivElement>(null)
  const historyRef   = useRef<HTMLDivElement>(null)

  useEffect(() => {
    answerEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [answer])

  useEffect(() => {
    if (historyRef.current) {
      historyRef.current.scrollTop = historyRef.current.scrollHeight
    }
  }, [history])

  useEffect(() => {
    fetch('/api/trending')
      .then(r => r.json())
      .then(d => setTrending(d.results?.slice(0, 6) ?? []))
      .catch(() => {})
  }, [])

  const startNewConversation = useCallback(() => {
    esRef.current?.close()
    setThreadId(generateThreadId())
    setHistory([])
    setEvents([])
    setAnswer('')
    setQuery('')
    setStream(false)
    setTurnNum(0)
  }, [])

  const runQuery = useCallback((q: string) => {
    if (!q.trim() || isStreaming) return
    esRef.current?.close()

    setEvents([])
    setAnswer('')
    setStream(true)
    setActiveTab('graph')

    const url = `/api/query?q=${encodeURIComponent(q.trim())}&thread_id=${threadId}`
    const es = new EventSource(url)
    esRef.current = es

    let currentAnswer = ''

    const addEvent = (type: string, e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data)
        setEvents(prev => [...prev, { ...payload, type }])
      } catch { /* ignore parse errors */ }
    }

    es.addEventListener('pipeline_start',   e => { addEvent('pipeline_start', e); setTurnNum(p => p + 1) })
    es.addEventListener('agent_start',      e => addEvent('agent_start', e))
    es.addEventListener('agent_end',        e => addEvent('agent_end', e))
    es.addEventListener('routing_decision', e => addEvent('routing_decision', e))
    es.addEventListener('llm_start',        e => addEvent('llm_start', e))
    es.addEventListener('llm_end',          e => addEvent('llm_end', e))
    es.addEventListener('chunks_retrieved', e => addEvent('chunks_retrieved', e))
    es.addEventListener('tmdb_results',     e => addEvent('tmdb_results', e))
    es.addEventListener('pipeline_error',   e => { addEvent('error', e); setStream(false); es.close() })

    es.addEventListener('token', (e: MessageEvent) => {
      addEvent('token', e)
      const payload = JSON.parse(e.data)
      if (payload.is_final) {
        currentAnswer += payload.content
        setAnswer(currentAnswer)
      }
    })

    es.addEventListener('done', (e: MessageEvent) => {
      addEvent('done', e)
      setStream(false)
      es.close()
      if (currentAnswer) {
        setHistory(prev => [...prev, { q: q.trim(), a: currentAnswer }])
      }
    })

    es.onerror = () => {
      setStream(prev => { if (prev) es.close(); return false })
    }
  }, [isStreaming, threadId])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    runQuery(query)
  }

  const showTrending = !answer && !isStreaming && history.length === 0

  return (
    <div className="app">
      {/* ── Left panel ──────────────────────────────────────────────────── */}
      <div className="panel-left">

        {/* Brand header */}
        <div className="header">
          <div className="header-logo-wrap">
            <span className="header-icon">🎬</span>
            <div>
              <div className="header-title">
                <span className="t-smart">Smart</span>
                <span className="t-movie">Movie</span>
                <span className="t-search">Search</span>
              </div>
              <div className="header-tagline">AI-powered movie &amp; TV intelligence</div>
            </div>
          </div>
          <div className="header-right">
            {history.length > 0 && (
              <span className="header-turns">
                {history.length} turn{history.length !== 1 ? 's' : ''}
              </span>
            )}
            <button
              className="new-chat-btn"
              onClick={startNewConversation}
              title="Start a new conversation"
            >
              + New Search
            </button>
          </div>
        </div>

        {/* Search hero */}
        <div className="query-section">
          <div className="query-label">
            {history.length > 0 ? 'Follow-up question' : 'Ask about any movie or TV show'}
          </div>
          <form className="query-form" onSubmit={handleSubmit}>
            <div className="query-input-wrap">
              <span className="query-input-icon">🔍</span>
              <input
                className="query-input"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder={
                  history.length > 0
                    ? 'e.g. What about the director? Show me similar films…'
                    : 'e.g. Best heist movies, or "Tell me about Inception"'
                }
                disabled={isStreaming}
                autoFocus
              />
            </div>
            <button className="query-btn" type="submit" disabled={isStreaming || !query.trim()}>
              {isStreaming ? '…' : 'Search'}
            </button>
          </form>

          {history.length === 0 && (
            <div className="example-queries">
              {EXAMPLE_QUERIES.map(({ icon, text }) => (
                <button
                  key={text}
                  className="example-chip"
                  onClick={() => { setQuery(text); runQuery(text) }}
                  disabled={isStreaming}
                >
                  <span>{icon}</span>
                  {text}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Content area */}
        <div className="answer-section" ref={historyRef}>

          {/* Trending movies — shown on first load */}
          {showTrending && trending.length > 0 && (
            <div>
              <div className="section-label">🔥 Trending This Week</div>
              <div className="movie-cards">
                {trending.map(m => (
                  <div
                    key={m.id}
                    className="movie-card"
                    onClick={() => { setQuery(`Tell me about ${m.title}`); runQuery(`Tell me about ${m.title}`) }}
                  >
                    <div className="movie-card-poster-wrap">
                      {m.poster
                        ? <img src={m.poster} alt={m.title} />
                        : <div className="movie-card-poster-placeholder">🎬</div>
                      }
                      {m.rating && (
                        <div className="movie-card-rating-badge">⭐ {m.rating.toFixed(1)}</div>
                      )}
                    </div>
                    <div className="movie-card-info">
                      <div className="movie-card-title">{m.title}</div>
                      <div className="movie-card-meta">
                        <span>{m.year}</span>
                        <span className="movie-card-meta-sep">·</span>
                        <span className="movie-card-type">{m.media_type}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Empty state when no trending */}
          {showTrending && trending.length === 0 && (
            <div className="answer-placeholder">
              <div className="answer-placeholder-icon">🎬</div>
              <div className="answer-placeholder-text">
                Ask anything about movies, TV shows, directors, or genres.
                <br />
                The AI pipeline searches TMDB, a knowledge base, and the web simultaneously.
              </div>
              <div className="answer-placeholder-hint">Try: "Best heist films with a heist expert's take"</div>
            </div>
          )}

          {/* Previous turns */}
          {history.map((turn, i) => (
            <div key={i} className="history-turn">
              <div className="history-q">
                <span className="history-q-icon">You</span>
                {turn.q}
              </div>
              <div className="ai-indicator">
                <div className="ai-indicator-badge">
                  <span className="ai-indicator-dot" />
                  SmartMovieSearch
                </div>
              </div>
              <div className="history-a answer-content">
                <ReactMarkdown>{turn.a}</ReactMarkdown>
              </div>
            </div>
          ))}

          {/* Current streaming answer */}
          {(answer || isStreaming) && (
            <div className="current-turn">
              {history.length > 0 && (
                <div className="history-q">
                  <span className="history-q-icon">You</span>
                  {query || '…'}
                </div>
              )}
              <div className="ai-indicator">
                <div className="ai-indicator-badge">
                  <span className={`ai-indicator-dot ${isStreaming ? 'streaming' : ''}`} />
                  SmartMovieSearch
                </div>
              </div>
              <div className="answer-content">
                <ReactMarkdown>{answer}</ReactMarkdown>
                {isStreaming && !answer && (
                  <span style={{ color: 'var(--text-dim)', fontStyle: 'italic', fontSize: 13 }}>
                    Agents thinking<span className="loading-dots"><span>.</span><span>.</span><span>.</span></span>
                  </span>
                )}
                {isStreaming && <span className="cursor-blink" />}
              </div>
            </div>
          )}

          <div ref={answerEndRef} />
        </div>
      </div>

      {/* ── Right panel: AI Pipeline ────────────────────────────────────────── */}
      <div className="panel-right">
        <div className="obs-header">
          <span className="obs-title">⚡ AI Pipeline</span>
          <span className={`obs-badge ${isStreaming ? 'live' : ''}`}>
            {isStreaming ? 'LIVE' : events.length > 0 ? 'COMPLETE' : 'IDLE'}
          </span>
          {turnNumber > 0 && (
            <span style={{ fontSize: 10, color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>
              turn {turnNumber}
            </span>
          )}
          <span className="obs-event-count">{events.length > 0 ? `${events.length} events` : ''}</span>
        </div>

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

        <div className="obs-content">
          {activeTab === 'graph'    && <PipelineGraph events={events} />}
          {activeTab === 'timeline' && <AgentTimeline events={events} />}
          {activeTab === 'events'   && <EventLog events={events} />}
          {activeTab === 'context'  && <ChunksPanel events={events} />}
        </div>

        <MetricsBar events={events} isStreaming={isStreaming} />
      </div>
    </div>
  )
}
