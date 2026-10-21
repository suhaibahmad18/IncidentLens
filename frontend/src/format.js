// Pure display helpers (unit-tested with node --test).

export function relativeMinutes(minutes) {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return '—'
  if (minutes === 0) return 'at incident start'
  const abs = Math.abs(minutes)
  const text = abs >= 60 ? `${Math.floor(abs / 60)}h ${Math.round(abs % 60)}m` : `${Math.round(abs * 10) / 10}m`
  return minutes < 0 ? `${text} before` : `${text} after`
}

export function confidenceLabel(confidence) {
  if (typeof confidence !== 'number') return 'unknown'
  if (confidence >= 0.75) return 'high'
  if (confidence >= 0.45) return 'medium'
  return 'low'
}

export function percent(value) {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '—'
}

const KINDS = { log: 'Log', deploy: 'Deployment', metric: 'Metric', runbook: 'Runbook' }

export function evidenceKind(evidenceId) {
  const prefix = String(evidenceId).split(':', 1)[0]
  return KINDS[prefix] ?? 'Evidence'
}

export const TERMINAL_STATUSES = new Set(['completed', 'failed'])

export function isTerminal(status) {
  return TERMINAL_STATUSES.has(status)
}

export function runHeadline(run) {
  if (!run) return null
  if (run.status === 'pending' || run.status === 'running') return 'Investigating…'
  if (run.status === 'failed') return 'Investigation failed'
  if (run.report?.status === 'inconclusive') return 'Inconclusive: evidence does not support a clear root cause'
  return 'Likely root cause'
}

export function stepLabel(step) {
  const labels = { llm_call: 'Model', tool_call: 'Tool', retrieval: 'Runbook search', final: 'Report' }
  return labels[step.type] ?? step.type
}

export function formatDuration(ms) {
  if (typeof ms !== 'number') return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

export function formatTimestamp(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z').replace(/:00Z$/, 'Z')
}

// Scale a series into an SVG polyline "x,y x,y" within width x height.
export function sparklinePoints(values, width, height, padding = 2) {
  const numbers = values.filter((v) => typeof v === 'number')
  if (numbers.length === 0) return ''
  const min = Math.min(...numbers)
  const max = Math.max(...numbers)
  const span = max - min || 1
  const step = numbers.length > 1 ? (width - padding * 2) / (numbers.length - 1) : 0
  return numbers
    .map((v, i) => {
      const x = padding + i * step
      const y = height - padding - ((v - min) / span) * (height - padding * 2)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}
