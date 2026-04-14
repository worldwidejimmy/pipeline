import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import { PipelineEvent, TmdbMovie } from './types'
import { PipelineGraph } from './components/PipelineGraph'
import { AgentTimeline } from './components/AgentTimeline'
import { EventLog } from './components/EventLog'
import { MetricsBar } from './components/MetricsBar'
import { ChunksPanel } from './components/ChunksPanel'

const EXAMPLE_QUERIES = [
  'Show me good bank heist movies',
  'What are trending movies this week?',
  'Tell me about Inception — cast, rating, themes',
  'Top sci-fi films of all time',
  'Christopher Nolan directing style',
  'Best horror movies rated above 8',
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

  // Auto-scroll answer panel
  useEffect(() => {
    answerEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [answer])

  // Auto-scroll history to bottom
  useEffect(() => {
    if (historyRef.current) {
      historyRef.current.scrollTop = historyRef.current.scrollHeight
    }
  }, [history])

  // Load trending on mount
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
      // Append turn to local history
      if (currentAnswer) {
        setHistory(prev => [...prev, { q: q.trim(), a: currentAnswer }])
      }
    })

    es.onerror = () => {
      // Only treat as error if we're still streaming (not just connection close after done)
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

        {/* Header */}
        <div className="header">
          <span className="header-logo">🎬</span>
          <span className="header-title">Cine<span>AI</span></span>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            {history.length > 0 && (
              <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                {history.length} turn{history.length !== 1 ? 's' : ''}
              </span>
            )}
            <button
              className="new-chat-btn"
              onClick={startNewConversation}
              title="Start a new conversation"
            >
              + New Chat
            </button>
          </div>
        </div>

        {/* Query input */}
        <div className="query-section">
          <div className="query-label">
            {history.length > 0
              ? 'Ask a follow-up question'
              : 'Ask about any movie or TV show'}
          </div>
          <form className="query-form" onSubmit={handleSubmit}>
            <input
              className="query-input"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder={
                history.length > 0
                  ? 'e.g. What about the director? Or show me similar films…'
                  : 'e.g. Show me good bank heist movies'
              }
              disabled={isStreaming}
            />
            <button className="query-btn" type="submit" disabled={isStreaming || !query.trim()}>
              {isStreaming ? '…' : 'Ask'}
            </button>
          </form>
          {history.length === 0 && (
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
          )}
        </div>

        {/* Conversation + current answer */}
        <div className="answer-section" ref={historyRef}>

          {/* Trending movies (first load only) */}
          {showTrending && trending.length > 0 && (
            <div>
              <div className="section-label">🔥 Trending This Week</div>
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

          {showTrending && trending.length === 0 && (
            <div className="answer-placeholder">
              Ask a question about any movie or TV show.
              <br />
              The pipeline routes to TMDB, RAG, or web search automatically.
            </div>
          )}

          {/* Previous conversation turns */}
          {history.map((turn, i) => (
            <div key={i} className="history-turn">
              <div className="history-q">
                <span className="history-q-icon">You</span>
                {turn.q}
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
                  {/* last question is the current one */}
                  {query || '…'}
                </div>
              )}
              <div className="answer-content">
                <ReactMarkdown>{answer}</ReactMarkdown>
                {isStreaming && <span className="cursor-blink" />}
              </div>
            </div>
          )}

          <div ref={answerEndRef} />
        </div>
      </div>

      {/* ── Right panel: Observability ────────────────────────────────────── */}
      <div className="panel-right">
        <div className="obs-header">
          <span className="obs-title">Pipeline Observability</span>
          <span className={`obs-badge ${isStreaming ? 'live' : ''}`}>
            {isStreaming ? 'LIVE' : events.length > 0 ? 'COMPLETE' : 'IDLE'}
          </span>
          {turnNumber > 0 && (
            <span style={{ fontSize: 10, color: 'var(--text-dim)', fontFamily: 'var(--font-mono)', marginLeft: 4 }}>
              turn {turnNumber}
            </span>
          )}
          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>
            {events.length} events
          </span>
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
