// Pure display helpers (unit-tested with node --test).

// "1 deployment", "2 deployments", "1 log entry", "3 log entries".
export function pluralize(count, singular, plural = `${singular}s`) {
  return `${count.toLocaleString('en-US')} ${pluralNoun(count, singular, plural)}`
}

export function pluralNoun(count, singular, plural = `${singular}s`) {
  return count === 1 ? singular : plural
}

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
  return 'Investigation complete'
}

// Normalize a stored report into the four sections the UI shows. Reports saved before facts
// existed simply have no facts; nothing is invented to fill the gap.
export function reportSections(report) {
  if (!report) return null
  const list = (value) => (Array.isArray(value) ? value : [])
  return {
    facts: list(report.facts).filter((f) => f && f.statement && list(f.evidence_ids).length),
    hypothesis: {
      status: report.status,
      rootCause: report.root_cause ?? '',
      affectedService: report.affected_service ?? null,
      confidence: report.confidence,
      explanation: report.explanation ?? '',
      alternatives: list(report.alternative_explanations),
      citations: list(report.evidence),
    },
    recommendations: list(report.remediation),
    unknowns: {
      missing: list(report.missing_information),
      unsupported: list(report.unsupported_statements),
      notes: list(report.validation_notes),
      droppedCount: list(report.dropped_citations).length,
    },
    legacy: !Array.isArray(report.facts),
  }
}

export function hasUnknowns(unknowns) {
  return Boolean(unknowns && (unknowns.missing.length || unknowns.unsupported.length || unknowns.notes.length
    || unknowns.droppedCount))
}

// A metric's baseline may be unavailable (no data from well before the incident): say so
// instead of printing an empty or made-up number.
export function baselineText(series) {
  return series?.baseline_avg === null || series?.baseline_avg === undefined
    ? 'no baseline (no data from before the incident)'
    : `baseline ${series.baseline_avg}`
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

// Stable evidence-kind key for styling chips: 'log' | 'deploy' | 'metric' | 'runbook' | 'other'.
export function evidenceKindKey(evidenceId) {
  const prefix = String(evidenceId).split(':', 1)[0]
  return ['log', 'deploy', 'metric', 'runbook'].includes(prefix) ? prefix : 'other'
}

// True when evidence was stored after the run started, i.e. the report predates it.
export function isReportOutdated(run, evidenceUpdatedAt) {
  if (!run?.started_at || !evidenceUpdatedAt) return false
  const started = new Date(run.started_at).getTime()
  const updated = new Date(evidenceUpdatedAt).getTime()
  return Number.isFinite(started) && Number.isFinite(updated) && updated > started
}

// Geometry for a telemetry sparkline: line, filled area, incident-start marker, optional
// baseline line and first-deviation dot. The baseline is included in the vertical scale so the
// dashed line is always visible. Returns null when there is nothing to draw.
export function sparklineGeometry(points, { width, height, padding = 3, baseline = null, deviationMinutes = null }) {
  const usable = (points ?? []).filter((p) => typeof p?.value === 'number')
  if (usable.length === 0) return null
  const scale = typeof baseline === 'number' ? [...usable.map((p) => p.value), baseline] : usable.map((p) => p.value)
  const min = Math.min(...scale)
  const max = Math.max(...scale)
  const span = max - min || 1
  const step = usable.length > 1 ? (width - padding * 2) / (usable.length - 1) : 0
  const x = (i) => padding + i * step
  const y = (v) => height - padding - ((v - min) / span) * (height - padding * 2)
  const coords = usable.map((p, i) => [x(i), y(p.value)])
  const fmt = ([cx, cy]) => `${cx.toFixed(1)},${cy.toFixed(1)}`
  const bottom = (height - padding).toFixed(1)

  const zeroIndex = usable.findIndex((p) => p.minutes_from_incident >= 0)
  let deviation = null
  if (typeof deviationMinutes === 'number') {
    const index = usable.findIndex((p) => p.minutes_from_incident >= deviationMinutes)
    if (index >= 0) deviation = { x: coords[index][0], y: coords[index][1] }
  }
  return {
    line: coords.map(fmt).join(' '),
    area: `M${coords[0][0].toFixed(1)},${bottom} L${coords.map(fmt).join(' L')} L${coords.at(-1)[0].toFixed(1)},${bottom} Z`,
    markerX: zeroIndex > 0 ? x(zeroIndex) : null,
    baselineY: typeof baseline === 'number' ? y(baseline) : null,
    deviation,
  }
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
