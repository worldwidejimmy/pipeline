import { useState, useRef, useEffect } from 'react'
import { setAccessToken } from '../api'

interface Props {
  onAuth: (token: string) => void
}

export function PasswordGate({ onAuth }: Props) {
  const [password, setPassword] = useState('')
  const [showPass, setShowPass]   = useState(false)
  const [error, setError]         = useState('')
  const [loading, setLoading]     = useState(false)
  const [shake, setShake]         = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const triggerShake = () => {
    setShake(true)
    setTimeout(() => setShake(false), 500)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!password || loading) return
    setLoading(true)
    setError('')

    try {
      const res = await fetch('/api/auth', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ password }),
      })

      if (res.ok) {
        const { token } = await res.json()
        setAccessToken(token)
        onAuth(token)
      } else {
        setError('Incorrect password — try again.')
        setPassword('')
        triggerShake()
        inputRef.current?.focus()
      }
    } catch {
      setError('Connection error — please try again.')
      triggerShake()
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="pg-overlay">
      <div className={`pg-card ${shake ? 'pg-shake' : ''}`}>

        <div className="pg-logo">
          <span className="pg-logo-icon">🎬</span>
          <div className="pg-logo-text">
            <span className="t-smart">Smart</span><span className="t-movie">Movie</span><span className="t-search">Search</span>
          </div>
        </div>

        <h2 className="pg-title">Preview Access</h2>
        <p className="pg-subtitle">
          This site is in private preview. Enter the password to explore.
        </p>

        <form className="pg-form" onSubmit={handleSubmit}>
          <div className="pg-input-wrap">
            <input
              ref={inputRef}
              className="pg-input"
              type={showPass ? 'text' : 'password'}
              placeholder="Password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete="current-password"
              disabled={loading}
            />
            <button
              type="button"
              className="pg-show-btn"
              onClick={() => setShowPass(s => !s)}
              aria-label={showPass ? 'Hide password' : 'Show password'}
              tabIndex={-1}
            >
              {showPass ? '🙈' : '👁️'}
            </button>
          </div>

          {error && <p className="pg-error" role="alert">{error}</p>}

          <button className="pg-submit" type="submit" disabled={loading || !password}>
            {loading ? <span className="pg-spinner" /> : 'Enter'}
          </button>
        </form>

        <p className="pg-hint">
          AI-powered movie, TV &amp; music search — built on LangGraph + Groq
        </p>
      </div>
    </div>
  )
}
