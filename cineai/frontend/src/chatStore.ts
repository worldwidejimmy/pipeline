/** Saved chats, in this browser only (localStorage): "New chat" keeps the old one, and the
 *  🕘 Chats menu switches back. The server's conversation memory is keyed by the same
 *  thread id, so a follow-up in a reopened chat keeps its context (until the server restarts). */
import { PipelineEvent } from './types'

export interface Turn {
  q: string
  a: string
  runId?: string             // server's answer id, for 👍/👎
  events: PipelineEvent[]    // the turn's pipeline steps (tokens excluded), kept for its step trace
}
export interface Chat { id: string; title: string; updated: number; turns: Turn[] }

const KEY = 'sms-chats'
const MAX_CHATS = 30
const FULL_DETAIL = 5   // newest chats keep every prompt & response; older ones keep the steps only

export function loadChats(): Chat[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(v) ? v : []
  } catch { return [] }
}

/** Drop the bulky prompt/response/source text from a chat (its step lines and tokens stay). */
function slim(c: Chat): Chat {
  return { ...c, turns: c.turns.map(t => ({ ...t, events: t.events
    .filter(e => e.type !== 'source_data')
    .map(e => e.type === 'llm_end' ? { ...e, prompt: undefined, response: undefined } : e) })) }
}

export function saveChats(chats: Chat[]): void {
  const sorted = [...chats].sort((a, b) => b.updated - a.updated).slice(0, MAX_CHATS)
  const tries = [sorted.map((c, i) => i < FULL_DETAIL ? c : slim(c)), sorted.map(slim), sorted.slice(0, 10).map(slim)]
  for (const t of tries) {
    try { localStorage.setItem(KEY, JSON.stringify(t)); return } catch { /* quota: try smaller */ }
  }
}

export function ago(ts: number): string {
  const m = Math.round((Date.now() - ts) / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} h ago`
  return new Date(ts).toLocaleDateString()
}
