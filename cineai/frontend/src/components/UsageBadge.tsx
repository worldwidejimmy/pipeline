import { Usage } from '../types'

interface Props {
  usage: Usage | null
  onSignIn: () => void
  onSignOut: () => void
}

function fmt(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`
  return `${n}`
}

export function UsageBadge({ usage, onSignIn, onSignOut }: Props) {
  if (!usage) return null

  const pct = usage.token_budget
    ? Math.min(100, Math.round((usage.tokens_used_today / usage.token_budget) * 100))
    : 0
  const meterColor = pct >= 90 ? 'var(--red)' : pct >= 70 ? 'var(--amber)' : 'var(--indigo)'

  return (
    <div className="usage-badge" title="Daily Groq free-tier token budget (shared by all visitors)">
      {/* Token meter */}
      <div className="usage-tokens">
        <span className="usage-tokens-label">🔢 {fmt(usage.tokens_used_today)} / {fmt(usage.token_budget)}</span>
        <div className="usage-meter">
          <div className="usage-meter-fill" style={{ width: `${pct}%`, background: meterColor }} />
        </div>
      </div>

      {/* Quota / sign-in */}
      {usage.unlimited ? (
        <button className="usage-chip usage-chip--unlimited" onClick={onSignOut}
                title="You have unlimited access — click to sign out">
          ♾️ Unlimited
        </button>
      ) : (
        <button className="usage-chip" onClick={onSignIn}
                title={`${usage.free_remaining} of ${usage.free_limit} free searches left this hour — sign in for unlimited`}>
          🎟️ {usage.free_remaining}/{usage.free_limit} free · <span className="usage-signin">Sign in</span>
        </button>
      )}
    </div>
  )
}
