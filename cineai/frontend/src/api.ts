const TOKEN_KEY = 'sms_access_token'

export function getAccessToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function setAccessToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
}

export function clearAccessToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}

/** Fetch wrapper that automatically attaches the access token header. */
export function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const token = getAccessToken()
  const headers = new Headers(init?.headers)
  if (token) headers.set('X-Access-Token', token)
  return fetch(url, { ...init, headers })
}

/** Build an EventSource URL with the access token as a query param.
 *  EventSource does not support custom headers, so we pass the token
 *  in the URL instead (accepted by the backend middleware). */
export function makeSSEUrl(base: string): string {
  const token = getAccessToken()
  if (!token) return base
  const sep = base.includes('?') ? '&' : '?'
  return `${base}${sep}_t=${encodeURIComponent(token)}`
}
