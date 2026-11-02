import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import { PipelineEvent, TmdbMovie, Usage } from './types'
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
import { StepTrace } from './components/StepTrace'
import { OpsModal } from './components/OpsModal'
import { FeedbackBar } from './components/FeedbackBar'
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
  runId?: string             // server's answer id, for 👍/👎
  events: PipelineEvent[]    // the turn's pipeline steps (tokens excluded), kept for its step trace
}

// Events that describe a step (everything except the token stream).
const STEP_EVENTS = ['pipeline_start', 'rewrite', 'kb_lookup', 'agent_start', 'agent_end', 'routing_decision',
  'chunks_retrieved', 'tmdb_results', 'music_results', 'search_results', 'guardrail', 'llm_start', 'llm_end', 'done'] as const

function loadPref(key: string, fallback: boolean): boolean {
  try { const v = localStorage.getItem(key); return v === null ? fallback : v === '1' } catch { return fallback }
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
  const [showOps,       setShowOps]       = useState(false)
  // The pipeline graph/timeline/events panel: optional now that the chat shows each step.
  const [showPipeline,  setShowPipeline]  = useState(() => loadPref('sms-pipeline-panel', false))
  const [pendingQ,      setPendingQ]      = useState('')   // the question being answered (input clears on send)

  // Open-access usage / sign-in
  const [usage,      setUsage]      = useState<Usage | null>(null)
  const [showSignIn, setShowSignIn] = useState(false)
  const [signInReason, setSignInReason] = useState<string | undefined>(undefined)

  const refreshUsage = useCallback(() => {
    fetchUsage().then(setUsage).catch(() => {})
  }, [])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('sms-theme', theme)
  }, [theme])

  useEffect(() => {
    try { localStorage.setItem('sms-pipeline-panel', showPipeline ? '1' : '0') } catch { /* private mode */ }
  }, [showPipeline])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { setShowKnowledge(false); setShowStatus(false); setShowRules(false); setShowOps(false)}
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const esRef        = useRef<EventSource | null>(null)
  const inputRef     = useRef<HTMLInputElement>(null)
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

  const startNewConversation = useCallback(() => {
    esRef.current?.close()
    setThreadId(generateThreadId())
    setHistory([])
    setEvents([])
    setAnswer('')
    setQuery('')
    setStream(false)
    setTurnNum(0)
    setPendingQ('')
  }, [])

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

    setPendingQ(q.trim())
    setQuery('')
    setEvents([])
    setAnswer('')
    setStream(true)
    setActiveTab('graph')

    const url = makeSSEUrl(`/api/query?q=${encodeURIComponent(q.trim())}&thread_id=${threadId}`)
    const es = new EventSource(url)
    esRef.current = es

    let currentAnswer = ''
    let runId: string | undefined
    const turnEvents: PipelineEvent[] = []

    const addEvent = (type: string, e: MessageEvent) => {
      try {
        const ev = { ...JSON.parse(e.data), type } as PipelineEvent
        if (type !== 'token') turnEvents.push(ev)
        setEvents(prev => [...prev, ev])
      } catch { /* ignore parse errors */ }
    }

    es.addEventListener('pipeline_start',   e => {
      addEvent('pipeline_start', e); setTurnNum(p => p + 1)
      try { runId = JSON.parse(e.data).run_id ?? undefined } catch { /* older backend */ }
    })
    for (const t of STEP_EVENTS) {
      if (t !== 'pipeline_start' && t !== 'done') es.addEventListener(t, e => addEvent(t, e as MessageEvent))
    }
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
        setHistory(prev => [...prev, { q: q.trim(), a: currentAnswer, runId, events: turnEvents }])
        setAnswer('')  // the turn lives in history now — keeping it would render twice
        setPendingQ('')
      }
      refreshUsage()
      setTimeout(() => inputRef.current?.focus(), 0)   // ready for the next follow-up
    })

    es.onerror = () => {
      setStream(prev => { if (prev) es.close(); return false })
    }
  }, [isStreaming, threadId, openSignIn, refreshUsage])

  const run = runQuery

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    run(query)
  }

  const chatting = history.length > 0 || isStreaming || !!answer

  // The question box: a hero at the top before the first question, docked at the bottom after.
  const composer = (
        <div className={`query-section ${chatting ? 'query-section--docked' : ''}`}>
          <div className="query-label">
            {chatting ? 'Ask a follow-up' : 'Ask about any movie, TV show, or music'}
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
                ref={inputRef}
              />
            </div>
            <button className="query-btn" type="submit" disabled={isStreaming || !query.trim()}>
              {isStreaming ? '…' : 'Ask'}
            </button>
          </form>

          {/* Honest about where a question goes (backend src/tracing.py redacts before upload). */}
          <div className="privacy-note">
            Questions are answered by Anthropic's Claude and logged for quality monitoring, with
            emails, phone numbers and IP addresses redacted. Please don't include personal information.
          </div>

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
  )

  const showTrending = !answer && !isStreaming && history.length === 0

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
    <div className={`app ${showPipeline ? '' : 'app--solo'}`}>

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

      {showOps && <OpsModal onClose={() => setShowOps(false)} />}

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
            {usage?.unlimited && (
              <a
                className="header-icon-btn"
                href="/admin"
                title="Admin — evals, model, usage & abuse"
                aria-label="Admin"
              >
                🛡️
              </a>
            )}
            {history.length > 0 && (
              <span className="header-turns">
                {history.length} turn{history.length !== 1 ? 's' : ''}
              </span>
            )}
            <button
              className={`header-icon-btn ${showPipeline ? 'header-icon-btn--on' : ''}`}
              onClick={() => setShowPipeline(v => !v)}
              title={showPipeline ? 'Hide the AI pipeline panel' : 'Show the AI pipeline panel (graph, timeline, events, retrieved context)'}
              aria-label="AI pipeline panel"
              aria-pressed={showPipeline}
            >
              ⚡
            </button>
            <button
              className="header-btn-labeled"
              onClick={() => setShowOps(true)}
              title="Evals, scheduled jobs and the ingest/embedding history"
              aria-label="Evals and ops"
            >
              🧪 Evals &amp; ops
            </button>
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
              + New chat
            </button>
          </div>
        </div>

        {!chatting && composer}

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
                      {m.rating ? (
                        <div className="movie-card-rating-badge">⭐ {m.rating.toFixed(1)}</div>
                      ) : null}
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
              <StepTrace events={turn.events} latest={i === history.length - 1} />
              <div className="history-a answer-content">
                <ReactMarkdown>{turn.a}</ReactMarkdown>
              </div>
              {turn.runId && <FeedbackBar key={turn.runId} runId={turn.runId} />}
            </div>
          ))}

          {/* Current streaming answer */}
          {(answer || isStreaming) && (
            <div className="current-turn">
              <div className="history-q">
                <span className="history-q-icon">You</span>
                {pendingQ || '…'}
              </div>
              <div className="ai-indicator">
                <div className="ai-indicator-badge">
                  <span className={`ai-indicator-dot ${isStreaming ? 'streaming' : ''}`} />
                  SmartMovieSearch
                </div>
              </div>
              <StepTrace events={events} live={isStreaming} latest />
              <div className="answer-content">
                <ReactMarkdown>{answer}</ReactMarkdown>
                {isStreaming && <span className="cursor-blink" />}
              </div>
            </div>
          )}

          <div ref={answerEndRef} />
        </div>

        {/* In a conversation the composer docks under the latest answer, like any chat app. */}
        {chatting && composer}

        <footer className="site-footer">© {new Date().getFullYear()} smartmoviesearch.com</footer>
      </div>

      {/* ── Right panel: AI Pipeline (optional; ⚡ in the header) ─────────────── */}
      {showPipeline && <div className="panel-right">
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
      </div>}
    </div>
  )
}
