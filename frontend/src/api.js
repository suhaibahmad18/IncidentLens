// Thin client for the IncidentLens API. Errors become ApiError with a readable message.
//
// Authentication uses an HttpOnly session cookie the browser sends automatically (JavaScript
// never sees it). State-changing requests also carry the session's CSRF token, which the
// server returns on login, registration and /api/auth/me and which is kept only in memory.

let csrfToken = null
let onSessionLost = null

export function setCsrfToken(token) {
  csrfToken = token || null
}

// Called when the server no longer accepts our session (expired, logged out elsewhere).
export function setSessionLostHandler(handler) {
  onSessionLost = handler
}

export class ApiError extends Error {
  constructor(message, status, detail = null) {
    super(message)
    this.status = status
    this.detail = detail // the server's raw `detail` (string or list of field problems)
  }
}

export function describeError(status, body) {
  const detail = body && typeof body.detail === 'string' ? body.detail : null
  if (detail) return detail
  if (Array.isArray(body?.detail)) return 'Some fields are not valid. Check the highlighted fields.'
  if (status === 0) return 'Cannot reach the IncidentLens API. Is the backend running?'
  if (status === 401) return 'Please sign in to continue.'
  if (status === 429) return 'Too many requests. Please wait a moment and try again.'
  if (status === 413) return 'The request is too large. Reduce the amount of evidence and try again.'
  if (status >= 500) return `The server had a problem (HTTP ${status}).`
  return `Request failed (HTTP ${status}).`
}

async function request(path, options = {}) {
  const method = options.method ?? 'GET'
  const headers = { Accept: 'application/json', ...options.headers }
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken
  let response
  try {
    response = await fetch(path, { ...options, method, headers, credentials: 'same-origin' })
  } catch {
    throw new ApiError(describeError(0, null), 0)
  }
  let body = null
  try {
    body = await response.json()
  } catch {
    body = null
  }
  if (!response.ok) {
    const sessionLost = csrfToken && !path.startsWith('/api/auth/')
      && (response.status === 401 || (response.status === 403 && /CSRF/.test(body?.detail ?? '')))
    if (sessionLost && onSessionLost) onSessionLost()
    throw new ApiError(describeError(response.status, body), response.status, body?.detail ?? null)
  }
  return body
}

function postJson(path, payload) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

// /me answers 401 for visitors; that is a normal state, not an error.
async function currentSession() {
  try {
    return await request('/api/auth/me')
  } catch (err) {
    if (err.status === 401) return null
    throw err
  }
}

export const api = {
  me: currentSession,
  login: (credentials) => postJson('/api/auth/login', credentials),
  register: (credentials) => postJson('/api/auth/register', credentials),
  logout: () => request('/api/auth/logout', { method: 'POST' }),
  config: () => request('/api/config'),
  incidents: () => request('/api/incidents'),
  incident: (slug) => request(`/api/incidents/${encodeURIComponent(slug)}`),
  incidentRuns: (slug) => request(`/api/incidents/${encodeURIComponent(slug)}/runs`),
  createIncident: (payload) => postJson('/api/incidents', payload),
  addEvidence: (slug, payload) => postJson(`/api/incidents/${encodeURIComponent(slug)}/evidence`, payload),
  deleteIncident: (slug) => request(`/api/incidents/${encodeURIComponent(slug)}`, { method: 'DELETE' }),
  startInvestigation: (slug) =>
    request(`/api/incidents/${encodeURIComponent(slug)}/investigations`, { method: 'POST' }),
  run: (id) => request(`/api/runs/${encodeURIComponent(id)}`),
  trace: (id) => request(`/api/runs/${encodeURIComponent(id)}/trace`),
  evidence: (id) => request(`/api/runs/${encodeURIComponent(id)}/evidence`),
}
