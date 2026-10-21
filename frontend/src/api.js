// Thin client for the IncidentLens API. Errors become ApiError with a readable message.

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

export function describeError(status, body) {
  const detail = body && typeof body.detail === 'string' ? body.detail : null
  if (detail) return detail
  if (Array.isArray(body?.detail)) return 'The request was not valid.'
  if (status === 0) return 'Cannot reach the IncidentLens API. Is the backend running?'
  if (status >= 500) return `The server had a problem (HTTP ${status}).`
  return `Request failed (HTTP ${status}).`
}

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(path, { headers: { Accept: 'application/json' }, ...options })
  } catch {
    throw new ApiError(describeError(0, null), 0)
  }
  let body = null
  try {
    body = await response.json()
  } catch {
    body = null
  }
  if (!response.ok) throw new ApiError(describeError(response.status, body), response.status)
  return body
}

export const api = {
  config: () => request('/api/config'),
  incidents: () => request('/api/incidents'),
  incident: (slug) => request(`/api/incidents/${encodeURIComponent(slug)}`),
  incidentRuns: (slug) => request(`/api/incidents/${encodeURIComponent(slug)}/runs`),
  startInvestigation: (slug) =>
    request(`/api/incidents/${encodeURIComponent(slug)}/investigations`, { method: 'POST' }),
  run: (id) => request(`/api/runs/${encodeURIComponent(id)}`),
  trace: (id) => request(`/api/runs/${encodeURIComponent(id)}/trace`),
  evidence: (id) => request(`/api/runs/${encodeURIComponent(id)}/evidence`),
}
