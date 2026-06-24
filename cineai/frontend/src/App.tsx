import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import { PipelineEvent, TmdbMovie, Usage, RagChunk, CompareTokens } from './types'
import { PipelineGraph } from './components/PipelineGraph'
import { AgentTimeline } from './components/AgentTimeline'
import { EventLog } from './components/EventLog'
import { MetricsBar } from './components/MetricsBar'
import { ChunksPanel } from './components/ChunksPanel'
import { KnowledgeModal } from './components/KnowledgeModal'
import { StatusModal } from './components/StatusModal'
import { RoutingRulesModal } from './components/RoutingRulesModal'
import { PasswordGate } from './components/PasswordGate'
import { UsageBadge } from './components/UsageBadge'
import { CompareView } from './components/CompareView'
import { getAccessToken, clearAccessToken, apiFetch, makeSSEUrl, fetchUsage } from './api'

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
  const [token, setToken]         = useState<string | null>(() => getAccessToken())
  const [query, setQuery]         = useState('')
  const [events, setEvents]       = useState<PipelineEvent[]>([])
  const [answer, setAnswer]       = useState('')
  const [isStreaming, setStream]  = useState(false)
  const [activeTab, setActiveTab] = useState<ObsTab>('graph')
  const [trending, setTrending]   = useState<TmdbMovie[]>([])
  const [history, setHistory]     = useState<Turn[]>([])
  const [threadId, setThreadId]   = useState(generateThreadId)
  const [turnNumber, setTurnNum]  = useState(0)
  const [theme, setTheme]         = useState<'dark' | 'light'>(() =>
    (localStorage.getItem('sms-theme') as 'dark' | 'light') ?? 'dark'
  )
  const [errorBanner, setErrorBanner] = useState<{ code: string; message: string; detail?: string } | null>(null)
  const [showKnowledge, setShowKnowledge] = useState(false)
  const [showStatus,    setShowStatus]    = useState(false)
  const [showRules,     setShowRules]     = useState(false)

  // Open-access usage / sign-in
  const [usage,      setUsage]      = useState<Usage | null>(null)
  const [showSignIn, setShowSignIn] = useState(false)
  const [signInReason, setSignInReason] = useState<string | undefined>(undefined)

  // RAG-vs-no-RAG compare mode
  const [compareMode,  setCompareMode]  = useState(false)
  const [compareActive, setCompareActive] = useState(false)
  const [cmpRag,    setCmpRag]    = useState('')
  const [cmpBase,   setCmpBase]   = useState('')
  const [cmpChunks, setCmpChunks] = useState<RagChunk[]>([])
  const [cmpRagTokens,  setCmpRagTokens]  = useState<CompareTokens | null>(null)
  const [cmpBaseTokens, setCmpBaseTokens] = useState<CompareTokens | null>(null)

  const refreshUsage = useCallback(() => {
    fetchUsage().then(setUsage).catch(() => {})
  }, [])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('sms-theme', theme)
  }, [theme])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setShowKnowledge(false); setShowStatus(false); setShowRules(false) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

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

  // Public now — load trending and usage on mount (and whenever auth changes)
  useEffect(() => {
    apiFetch('/api/trending')
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(d => setTrending(d.results?.slice(0, 6) ?? []))
      .catch(() => {})
    refreshUsage()
  }, [token, refreshUsage])

  const resetCompare = useCallback(() => {
    setCompareActive(false)
    setCmpRag(''); setCmpBase('')
    setCmpChunks([]); setCmpRagTokens(null); setCmpBaseTokens(null)
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
    resetCompare()
  }, [resetCompare])

  const openSignIn = useCallback((reason?: string) => {
    setSignInReason(reason)
    setShowSignIn(true)
  }, [])

  const handleAuth = useCallback((t: string) => {
    setToken(t)
    setShowSignIn(false)
    setSignInReason(undefined)
    refreshUsage()
  }, [refreshUsage])

  const handleSignOut = useCallback(() => {
    clearAccessToken()
    setToken(null)
    refreshUsage()
  }, [refreshUsage])

  const runQuery = useCallback((q: string) => {
    if (!q.trim() || isStreaming) return
    esRef.current?.close()

    resetCompare()
    setEvents([])
    setAnswer('')
    setStream(true)
    setActiveTab('graph')

    const url = makeSSEUrl(`/api/query?q=${encodeURIComponent(q.trim())}&thread_id=${threadId}`)
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
    es.addEventListener('pipeline_error', e => {
      addEvent('error', e)
      try {
        const p = JSON.parse(e.data)
        if (p.code === 'ip_limit') {
          openSignIn(p.message)
        } else {
          setErrorBanner({ code: p.code ?? 'pipeline_error', message: p.message ?? 'Unknown error', detail: p.detail })
        }
      } catch {
        setErrorBanner({ code: 'pipeline_error', message: 'An unexpected error occurred.' })
      }
      setStream(false)
      es.close()
      refreshUsage()
    })

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
      refreshUsage()
    })

    es.onerror = () => {
      setStream(prev => { if (prev) es.close(); return false })
    }
  }, [isStreaming, threadId, resetCompare, openSignIn, refreshUsage])

  const runCompare = useCallback((q: string) => {
    if (!q.trim() || isStreaming) return
    esRef.current?.close()

    setEvents([])
    setAnswer('')
    setStream(true)
    setCompareActive(true)
    setCmpRag(''); setCmpBase('')
    setCmpChunks([]); setCmpRagTokens(null); setCmpBaseTokens(null)
    setTurnNum(p => p + 1)

    let rag = '', base = ''
    const url = makeSSEUrl(`/api/compare?q=${encodeURIComponent(q.trim())}`)
    const es = new EventSource(url)
    esRef.current = es

    es.addEventListener('chunks_retrieved', (e: MessageEvent) => {
      try { setCmpChunks(JSON.parse(e.data).chunks ?? []) } catch { /* ignore */ }
    })
    es.addEventListener('compare_token', (e: MessageEvent) => {
      const p = JSON.parse(e.data)
      if (p.side === 'rag')  { rag  += p.content; setCmpRag(rag) }
      else                   { base += p.content; setCmpBase(base) }
    })
    es.addEventListener('compare_side_end', (e: MessageEvent) => {
      const p = JSON.parse(e.data)
      const t = { prompt_tokens: p.prompt_tokens, completion_tokens: p.completion_tokens }
      if (p.side === 'rag') setCmpRagTokens(t); else setCmpBaseTokens(t)
    })
    es.addEventListener('compare_done', () => {
      setStream(false); es.close(); refreshUsage()
    })
    es.addEventListener('pipeline_error', e => {
      try {
        const p = JSON.parse(e.data)
        if (p.code === 'ip_limit') openSignIn(p.message)
        else setErrorBanner({ code: p.code ?? 'pipeline_error', message: p.message ?? 'Unknown error' })
      } catch { setErrorBanner({ code: 'pipeline_error', message: 'An unexpected error occurred.' }) }
      setStream(false); es.close(); setCompareActive(false); refreshUsage()
    })
    es.addEventListener('compare_error', (e: MessageEvent) => {
      try {
        const p = JSON.parse(e.data)
        if (p.side === 'rag') setCmpRag(prev => prev || `⚠️ ${p.message}`)
        else setCmpBase(prev => prev || `⚠️ ${p.message}`)
      } catch { /* ignore */ }
    })
    es.onerror = () => { setStream(prev => { if (prev) es.close(); return false }) }
  }, [isStreaming, openSignIn, refreshUsage])

  const run = useCallback((q: string) => {
    if (compareMode) runCompare(q)
    else runQuery(q)
  }, [compareMode, runCompare, runQuery])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    run(query)
  }

  const showTrending = !answer && !isStreaming && history.length === 0 && !compareActive

  const ERROR_ICONS: Record<string, string> = {
    rate_limit:       '⏳',
    auth_error:       '🔑',
    connection_error: '📡',
    pipeline_error:   '⚠️',
  }
  const ERROR_TITLES: Record<string, string> = {
    rate_limit:       'Rate limit reached',
    auth_error:       'API key error',
    connection_error: 'Connection failed',
    pipeline_error:   'Pipeline error',
  }

  return (
    <div className="app">

      {/* ── Optional sign-in modal (unlock unlimited) ───────────────────── */}
      {showSignIn && (
        <PasswordGate
          onAuth={handleAuth}
          onClose={() => { setShowSignIn(false); setSignInReason(undefined) }}
          reason={signInReason}
        />
      )}

      {/* ── Status modal ────────────────────────────────────────────────── */}
      {showStatus && <StatusModal onClose={() => setShowStatus(false)} />}

      {/* ── Routing rules modal ─────────────────────────────────────────── */}
      {showRules && <RoutingRulesModal onClose={() => setShowRules(false)} />}

      {/* ── Knowledge modal ─────────────────────────────────────────────── */}
      {showKnowledge && (
        <KnowledgeModal
          onClose={() => setShowKnowledge(false)}
          onSearch={q => { setQuery(q); run(q) }}
        />
      )}

      {/* ── Error banner ────────────────────────────────────────────────── */}
      {errorBanner && (
        <div className={`error-banner error-banner--${errorBanner.code}`} role="alert">
          <div className="error-banner-icon">{ERROR_ICONS[errorBanner.code] ?? '⚠️'}</div>
          <div className="error-banner-body">
            <div className="error-banner-title">{ERROR_TITLES[errorBanner.code] ?? 'Error'}</div>
            <div className="error-banner-message">{errorBanner.message}</div>
            {errorBanner.detail && (
              <details className="error-banner-detail">
                <summary>Technical detail</summary>
                <pre>{errorBanner.detail}</pre>
              </details>
            )}
          </div>
          <button
            className="error-banner-close"
            onClick={() => setErrorBanner(null)}
            aria-label="Dismiss"
          >✕</button>
        </div>
      )}

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
              <div className="header-tagline">AI-powered Movie, TV &amp; Music search</div>
            </div>
          </div>
          <div className="header-right">
            <UsageBadge usage={usage} onSignIn={() => openSignIn()} onSignOut={handleSignOut} />
            {history.length > 0 && (
              <span className="header-turns">
                {history.length} turn{history.length !== 1 ? 's' : ''}
              </span>
            )}
            <button
              className="header-icon-btn"
              onClick={() => setShowStatus(true)}
              title="Service status"
              aria-label="Service status"
            >
              ⚙️
            </button>
            <button
              className="header-icon-btn"
              onClick={() => setShowKnowledge(true)}
              title="Browse knowledge base"
              aria-label="Knowledge base"
            >
              📚
            </button>
            <button
              className="header-icon-btn"
              onClick={() => setShowRules(true)}
              title="Routing rules & instructions"
              aria-label="Routing rules"
            >
              🧭
            </button>
            <a
              className="header-icon-btn"
              href="/whitepaper.html"
              target="_blank"
              rel="noopener noreferrer"
              title="Technical white paper"
              aria-label="White paper"
            >
              📄
            </a>
            <button
              className="theme-toggle"
              onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
              title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
              aria-label="Toggle theme"
            >
              {theme === 'dark' ? '☀️' : '🌙'}
            </button>
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
            {history.length > 0 ? 'Follow-up question' : 'Ask about any movie, TV show, or music'}
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
              {isStreaming ? '…' : compareMode ? 'Compare' : 'Search'}
            </button>
          </form>

          <label className="compare-toggle" title="Answer the same question with and without retrieval, side by side. Costs one search.">
            <input
              type="checkbox"
              checked={compareMode}
              onChange={e => setCompareMode(e.target.checked)}
              disabled={isStreaming}
            />
            <span className="compare-toggle-track"><span className="compare-toggle-thumb" /></span>
            <span className="compare-toggle-label">🆚 Compare RAG vs. no-RAG</span>
          </label>

          {history.length === 0 && (
            <div className="example-queries">
              {EXAMPLE_QUERIES.map(({ icon, text }) => (
                <button
                  key={text}
                  className="example-chip"
                  onClick={() => { setQuery(text); run(text) }}
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
                    onClick={() => {
                      const q = `Tell me about the movie ${m.title}${m.year ? ` (${m.year})` : ''}`
                      setQuery(q); run(q)
                    }}
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
                Ask anything about movies, TV shows, music, directors, or genres.
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

          {/* RAG vs no-RAG comparison */}
          {compareActive && (
            <div className="current-turn">
              <div className="history-q">
                <span className="history-q-icon">You</span>
                {query || '…'}
              </div>
              <CompareView
                question={query}
                ragText={cmpRag}
                baseText={cmpBase}
                chunks={cmpChunks}
                ragTokens={cmpRagTokens}
                baseTokens={cmpBaseTokens}
                streaming={isStreaming}
              />
            </div>
          )}

          {/* Current streaming answer */}
          {!compareActive && (answer || isStreaming) && (
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
