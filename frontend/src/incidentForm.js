// Pure helpers for the "new incident" and "add evidence" forms (unit-tested with node --test).
// Limits mirror backend/app/incidents/parsing.py and backend/app/api/user_incidents.py.

import { pluralize } from './format.js'

export const LIMITS = {
  titleMin: 3,
  titleMax: 200,
  alertMin: 3,
  alertMax: 2000,
  logs: 1_000_000,
  metrics_csv: 300_000,
  deployments_csv: 50_000,
}

export const SERVICE_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/

export const EVIDENCE_FIELDS = [
  {
    name: 'logs',
    label: 'Logs',
    accept: '.log,.txt,.json,.jsonl,text/plain',
    hint: 'One entry per line: plain text (e.g. "2026-08-20T14:00:02Z ERROR service=orders-api timeout") or JSON lines. Indented lines are joined to the previous entry (stack traces).',
    placeholder: '2026-08-20T13:58:30Z WARN service=orders-api db pool usage 92%\n2026-08-20T14:00:02Z ERROR service=orders-api timeout acquiring db connection',
  },
  {
    name: 'metrics_csv',
    label: 'Metrics (CSV)',
    accept: '.csv,text/csv',
    hint: 'Header row required: timestamp,service,metric,value (unit optional).',
    placeholder: 'timestamp,service,metric,value,unit\n2026-08-20T14:00:00Z,orders-db,connections,100,count',
  },
  {
    name: 'deployments_csv',
    label: 'Deployments (CSV)',
    accept: '.csv,text/csv',
    hint: 'Header row required: timestamp,version (service, status, description, deployed_by optional). Status: succeeded, failed or rolled_back.',
    placeholder: 'timestamp,service,version,description\n2026-08-20T13:30:00Z,orders-api,v7.1.0,New reporting query',
  },
]

export const EMPTY_EVIDENCE = { logs: '', metrics_csv: '', deployments_csv: '' }
export const EMPTY_INCIDENT = {
  title: '', alert_summary: '', default_service: '', incident_start: '', ...EMPTY_EVIDENCE,
}

export function hasEvidence(values) {
  return EVIDENCE_FIELDS.some((field) => (values[field.name] ?? '').trim() !== '')
}

function validateEvidence(values, errors) {
  for (const field of EVIDENCE_FIELDS) {
    const text = values[field.name] ?? ''
    if (text.length > LIMITS[field.name]) {
      errors[field.name] = `${field.label} is too long: ${text.length.toLocaleString('en-US')} of ${LIMITS[field.name].toLocaleString('en-US')} characters allowed.`
    }
  }
  return errors
}

export function validateIncident(values) {
  const errors = {}
  const title = values.title.trim()
  const alert = values.alert_summary.trim()
  if (title.length < LIMITS.titleMin) errors.title = `Enter a title of at least ${LIMITS.titleMin} characters.`
  else if (title.length > LIMITS.titleMax) errors.title = `Keep the title under ${LIMITS.titleMax} characters.`
  if (alert.length < LIMITS.alertMin) errors.alert_summary = 'Describe the symptom or alert (at least 3 characters).'
  else if (alert.length > LIMITS.alertMax) errors.alert_summary = `Keep the alert summary under ${LIMITS.alertMax} characters.`
  const serviceError = validateService(values.default_service)
  if (serviceError) errors.default_service = serviceError
  if (values.incident_start && Number.isNaN(new Date(values.incident_start).getTime())) {
    errors.incident_start = 'Enter a valid date and time, or leave it empty.'
  }
  return validateEvidence(values, errors)
}

export function validateEvidenceOnly(values) {
  const errors = {}
  if (!hasEvidence(values)) errors.logs = 'Paste or load at least one kind of evidence.'
  const serviceError = validateService(values.default_service)
  if (serviceError) errors.default_service = serviceError
  return validateEvidence(values, errors)
}

function validateService(value) {
  const service = (value ?? '').trim()
  if (!service) return 'Enter the main service name (used for lines that do not name a service).'
  if (!SERVICE_PATTERN.test(service)) {
    return 'Use lowercase letters, digits and dashes, starting with a letter or digit (e.g. orders-api).'
  }
  return null
}

// `datetime-local` values have no time zone: interpret them as the browser's local time.
export function localDateTimeToIso(value) {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date.toISOString()
}

export function incidentPayload(values) {
  return {
    title: values.title.trim(),
    alert_summary: values.alert_summary.trim(),
    default_service: values.default_service.trim(),
    incident_start: localDateTimeToIso(values.incident_start),
    logs: values.logs,
    metrics_csv: values.metrics_csv,
    deployments_csv: values.deployments_csv,
  }
}

export function evidencePayload(values) {
  return {
    default_service: values.default_service.trim(),
    logs: values.logs,
    metrics_csv: values.metrics_csv,
    deployments_csv: values.deployments_csv,
  }
}

const FIELD_NAMES = new Set(['title', 'alert_summary', 'default_service', 'incident_start',
  'logs', 'metrics_csv', 'deployments_csv'])

// Server 422 details: [{loc: ["body", "title"], msg}] for field validation, or a plain string
// (e.g. "metrics CSV needs a header row ..."). Map both onto form fields where possible.
export function fieldErrorsFromApi(detail) {
  const errors = {}
  if (Array.isArray(detail)) {
    for (const problem of detail) {
      const field = (problem.loc ?? []).find((part) => FIELD_NAMES.has(part))
      const key = field ?? '_form'
      errors[key] = errors[key] ?? problem.msg ?? 'Invalid value.'
    }
  } else if (typeof detail === 'string') {
    if (/^logs\b/.test(detail)) errors.logs = detail
    else if (/^metrics\b/.test(detail)) errors.metrics_csv = detail
    else if (/^deployments\b/.test(detail)) errors.deployments_csv = detail
    else errors._form = detail
  }
  return errors
}

export function summaryLines(summary) {
  if (!summary) return []
  const lines = [
    `${pluralize(summary.logs_parsed, 'log entry', 'log entries')}, ${pluralize(summary.metrics_parsed, 'metric point')}, `
      + `${pluralize(summary.deployments_parsed, 'deployment')} received.`,
  ]
  if (summary.services?.length) lines.push(`Services found: ${summary.services.join(', ')}.`)
  if (summary.redactions) {
    lines.push(`${pluralize(summary.redactions, 'credential-like value')} ${summary.redactions === 1 ? 'was' : 'were'} redacted before storage.`)
  }
  if (summary.timestamps_inferred) {
    const n = summary.timestamps_inferred
    lines.push(`${pluralize(n, 'log line')} had no readable timestamp and ${n === 1 ? 'was' : 'were'} placed at the incident start.`)
  }
  if (summary.outside_investigation_window) {
    const n = summary.outside_investigation_window
    lines.push(`${pluralize(n, 'record')} ${n === 1 ? 'falls' : 'fall'} outside the investigation window (3 h before to 1 h after the start) and will not be seen by the agent.`)
  }
  if (summary.skipped) lines.push(`${pluralize(summary.skipped, 'row')} ${summary.skipped === 1 ? 'was' : 'were'} skipped:`)
  return lines
}

export function evidenceStatus(evidence) {
  if (!evidence) return null
  const total = evidence.logs + evidence.metric_points + evidence.deployments
  if (total === 0) return { ready: false, message: 'No evidence yet. Add logs, metrics or deployments before investigating.' }
  if (evidence.usable === 0) {
    return {
      ready: false,
      message: 'The evidence is outside the investigation window (3 h before to 1 h after the incident start). Add evidence closer to the incident start.',
    }
  }
  return { ready: true, message: `${pluralize(evidence.usable, 'record')} available to the investigation.` }
}

export function readFileAsText(file, limit) {
  // Bytes are an upper bound on characters, so this rejects early without reading huge files.
  if (file.size > limit * 4) {
    return Promise.reject(new Error(`"${file.name}" is too large (${Math.round(file.size / 1024)} KB).`))
  }
  return file.text().then((text) => {
    if (text.length > limit) throw new Error(`"${file.name}" has ${text.length.toLocaleString('en-US')} characters; the limit is ${limit.toLocaleString('en-US')}.`)
    return text
  })
}
