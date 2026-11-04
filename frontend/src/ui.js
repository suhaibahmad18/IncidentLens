// Presentation helpers for the dashboard, incident cards, confidence gauge and agent timeline.
// Pure functions (unit-tested with node --test); they only reshape data the API already returns.

// One visual tone per investigation state, used consistently by cards, badges and tiles:
// ok = investigated (teal), running = in progress (amber), failed = rose, idle = never run (slate).
const RUN_TONES = {
  completed: { tone: 'ok', label: 'Investigated' },
  running: { tone: 'running', label: 'Investigating' },
  pending: { tone: 'running', label: 'Queued' },
  failed: { tone: 'failed', label: 'Last run failed' },
}

export function runTone(status) {
  return RUN_TONES[status] ?? { tone: 'idle', label: 'Not investigated' }
}

// Counts for the dashboard overview strip, from the incident list the page already has.
export function incidentSummary(incidents) {
  const list = incidents ?? []
  const count = (tone) => list.filter((incident) => runTone(incident.last_run_status).tone === tone).length
  return {
    total: list.length,
    mine: list.filter((incident) => incident.kind === 'user').length,
    demos: list.filter((incident) => incident.kind !== 'user').length,
    investigated: count('ok'),
    running: count('running'),
    failed: count('failed'),
    idle: count('idle'),
  }
}

// Confidence as filled segments of a 5-part gauge (0..1 -> 0..5; non-numbers -> 0).
export function confidenceSegments(value, segments = 5) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0
  return Math.round(Math.min(Math.max(value, 0), 1) * segments)
}

// Relative duration bars for the agent timeline: each step's share of the slowest step, with a
// small minimum so even instant steps stay visible. Steps without a latency get no bar (null).
export function latencyBars(steps, minimum = 3) {
  const latencies = (steps ?? []).map((step) => (typeof step.latency_ms === 'number' ? step.latency_ms : null))
  const max = Math.max(0, ...latencies.filter((ms) => ms !== null))
  return latencies.map((ms) => {
    if (ms === null) return null
    if (max === 0) return minimum
    return Math.max(minimum, Math.round((ms / max) * 100))
  })
}

// Totals shown above the agent timeline.
export function traceSummary(steps) {
  const list = steps ?? []
  const sum = (key) => list.reduce((total, step) => total + (typeof step[key] === 'number' ? step[key] : 0), 0)
  return {
    steps: list.length,
    modelCalls: list.filter((step) => step.type === 'llm_call').length,
    toolCalls: list.filter((step) => step.type === 'tool_call' || step.type === 'retrieval').length,
    errors: list.filter((step) => step.error).length,
    durationMs: sum('latency_ms'),
    tokens: sum('input_tokens') + sum('output_tokens'),
  }
}
