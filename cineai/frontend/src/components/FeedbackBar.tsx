/** 👍/👎 under a finished answer. Votes reference the server's answer id only; the
 *  server already knows what it asked and answered (backend src/feedback.py). A 👎
 *  is recorded immediately; the "what was wrong?" note is optional and replaces it. */
import { useState } from 'react'
import { apiFetch } from '../api'

type State = 'idle' | 'up' | 'down' | 'sent' | 'error'

export function FeedbackBar({ runId }: { runId: string }) {
  const [state, setState] = useState<State>('idle')
  const [comment, setComment] = useState('')
  const [err, setErr] = useState('')

  const send = async (rating: 1 | -1, note = '') => {
    try {
      const r = await apiFetch('/api/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: runId, rating, comment: note }),
      })
      if (!r.ok) {
        setErr((await r.json().catch(() => ({})))?.detail ?? 'Could not record that.')
        setState('error')
        return false
      }
      return true
    } catch {
      setErr('Could not record that.')
      setState('error')
      return false
    }
  }

  if (state === 'sent') return <div className="fb-bar fb-done">Thanks — this feeds the evals that test this site.</div>
  if (state === 'error') return <div className="fb-bar fb-done">{err}</div>
  if (state === 'up') return <div className="fb-bar fb-done">Thanks!</div>

  return (
    <div className="fb-bar">
      {state === 'idle' && (
        <>
          <span className="fb-label">Was this helpful?</span>
          <button className="fb-btn" aria-label="Helpful" title="Helpful"
            onClick={async () => { if (await send(1)) setState('up') }}>👍</button>
          <button className="fb-btn" aria-label="Not helpful" title="Not helpful"
            onClick={async () => { if (await send(-1)) setState('down') }}>👎</button>
        </>
      )}
      {state === 'down' && (
        <form className="fb-form" onSubmit={async e => {
          e.preventDefault()
          if (!comment.trim() || await send(-1, comment.trim())) setState('sent')
        }}>
          <input className="fb-input" value={comment} maxLength={500} autoFocus
            onChange={e => setComment(e.target.value)}
            placeholder="What was wrong? (optional — no personal info please)" />
          <button className="fb-send" type="submit">{comment.trim() ? 'Send' : 'Skip'}</button>
        </form>
      )}
    </div>
  )
}
