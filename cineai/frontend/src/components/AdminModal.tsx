import { useEffect, useState, useCallback } from 'react'
import { apiFetch } from '../api'
import { AdminUsage } from '../types'

interface Props {
  onClose: () => void
}

function fmt(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`
  return `${n}`
}

function ago(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000) - ts)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h`
}

export function AdminModal({ onClose }: Props) {
  const [data, setData] = useState<AdminUsage | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  const load = useCallback(() => {
    apiFetch('/api/admin/usage')
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(setData)
      .catch(e => setError(e === 403 ? 'Admin access required — sign in first.' : 'Failed to load.'))
  }, [])

  useEffect(() => { load() }, [load])

  const setBlacklist = async (ip: string, action: 'add' | 'remove') => {
    setBusy(ip)
    try {
      const r = await apiFetch('/api/admin/blacklist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip, action }),
      })
      if (r.ok) load()
    } finally { setBusy('') }
  }

  const handleBackdrop = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose()
  }

  return (
    <div className="modal-backdrop" onClick={handleBackdrop}>
      <div className="modal">
        <div className="modal-header">
          <div className="modal-title">
            <span className="modal-title-icon">🛡️</span>
            <div>
              <div className="modal-title-text">Admin — Usage &amp; Abuse</div>
              <div className="modal-title-sub">
                {data
                  ? `${data.calls_today}${data.call_cap ? `/${data.call_cap}` : ''} searches today · ${data.ips.length} IPs · ${fmt(data.total_tokens)} tokens · ${data.free_limit}/IP/hr`
                  : error || 'Loading…'}
              </div>
            </div>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="modal-body">
          {error && <div className="admin-error">{error}</div>}

          {data && (
            <>
              {data.blacklist.length > 0 && (
                <div className="admin-blacklist">
                  <div className="admin-section-label">🚫 Blacklisted</div>
                  <div className="admin-chip-row">
                    {data.blacklist.map(ip => (
                      <span key={ip} className="admin-chip">
                        {ip}
                        <button onClick={() => setBlacklist(ip, 'remove')} disabled={busy === ip} title="Un-block">✕</button>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="admin-section-label">Today’s traffic (by IP)</div>
              <table className="admin-table">
                <thead>
                  <tr><th>IP</th><th>Reqs</th><th>Tokens</th><th>Last</th><th></th></tr>
                </thead>
                <tbody>
                  {data.ips.length === 0 && (
                    <tr><td colSpan={5} className="admin-empty">No traffic yet today.</td></tr>
                  )}
                  {data.ips.map(row => (
                    <tr key={row.ip} className={row.blacklisted ? 'admin-row-blocked' : ''}>
                      <td className="admin-ip">{row.ip}</td>
                      <td>{row.requests}</td>
                      <td>{fmt(row.tokens)}</td>
                      <td className="admin-dim">{ago(row.last_seen)} ago</td>
                      <td>
                        {row.blacklisted
                          ? <button className="admin-btn" onClick={() => setBlacklist(row.ip, 'remove')} disabled={busy === row.ip}>unblock</button>
                          : <button className="admin-btn admin-btn--danger" onClick={() => setBlacklist(row.ip, 'add')} disabled={busy === row.ip}>block</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <button className="admin-refresh" onClick={load}>↻ Refresh</button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
