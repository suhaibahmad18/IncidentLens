import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  EMPTY_INCIDENT,
  LIMITS,
  evidencePayload,
  evidenceStatus,
  fieldErrorsFromApi,
  hasEvidence,
  incidentPayload,
  localDateTimeToIso,
  readFileAsText,
  summaryLines,
  validateEvidenceOnly,
  validateIncident,
} from '../src/incidentForm.js'

const valid = { ...EMPTY_INCIDENT, title: 'Orders API failing', alert_summary: 'Orders 5xx above 20%', default_service: 'orders-api' }

test('a complete form has no errors', () => {
  assert.deepEqual(validateIncident(valid), {})
})

test('required fields and formats are validated like the server', () => {
  const errors = validateIncident({ ...EMPTY_INCIDENT, title: 'ab', default_service: 'Orders API' })
  assert.match(errors.title, /at least 3/)
  assert.match(errors.alert_summary, /symptom/)
  assert.match(errors.default_service, /lowercase/)
  assert.match(validateIncident({ ...valid, default_service: '' }).default_service, /main service/)
  assert.match(validateIncident({ ...valid, title: 'x'.repeat(201) }).title, /under 200/)
  assert.match(validateIncident({ ...valid, incident_start: 'not a date' }).incident_start, /valid date/)
})

test('evidence size limits match the backend', () => {
  assert.equal(LIMITS.logs, 1_000_000)
  assert.equal(LIMITS.metrics_csv, 300_000)
  assert.equal(LIMITS.deployments_csv, 50_000)
  const errors = validateIncident({ ...valid, metrics_csv: 'x'.repeat(300_001) })
  assert.match(errors.metrics_csv, /too long: 300,001 of 300,000/)
})

test('adding evidence requires an explicitly chosen service (no silent default)', () => {
  const errors = validateEvidenceOnly({ ...EMPTY_INCIDENT, logs: 'line', default_service: '' })
  assert.match(errors.default_service, /main service/)
  assert.equal(evidencePayload({ ...EMPTY_INCIDENT, logs: 'x', default_service: ' orders-api ' }).default_service, 'orders-api')
})

test('adding evidence requires some evidence and a service', () => {
  assert.match(validateEvidenceOnly({ ...EMPTY_INCIDENT, default_service: 'web' }).logs, /at least one/)
  assert.deepEqual(validateEvidenceOnly({ ...EMPTY_INCIDENT, default_service: 'web', logs: 'line' }), {})
  assert.equal(hasEvidence({ logs: '  \n', metrics_csv: '', deployments_csv: '' }), false)
})

test('payloads contain exactly the fields the API accepts', () => {
  const payload = incidentPayload({ ...valid, title: '  Orders API failing  ', logs: 'a\nb' })
  assert.deepEqual(Object.keys(payload).sort(), [
    'alert_summary', 'default_service', 'deployments_csv', 'incident_start', 'logs', 'metrics_csv', 'title'])
  assert.equal(payload.title, 'Orders API failing')
  assert.equal(payload.incident_start, null)
  assert.equal(payload.logs, 'a\nb') // evidence text is sent as typed, never trimmed or altered
  assert.deepEqual(Object.keys(evidencePayload(valid)).sort(), ['default_service', 'deployments_csv', 'logs', 'metrics_csv'])
})

test('local date-time input becomes an ISO timestamp', () => {
  const iso = localDateTimeToIso('2026-08-20T14:00')
  assert.match(iso, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000Z$/) // any time zone (e.g. IST is +05:30)
  assert.equal(new Date(iso).getTime(), new Date('2026-08-20T14:00').getTime())
  assert.equal(localDateTimeToIso(''), null)
})

test('server validation errors map onto form fields', () => {
  assert.deepEqual(
    fieldErrorsFromApi([{ loc: ['body', 'title'], msg: 'too short' }, { loc: ['body', 'default_service'], msg: 'bad' }]),
    { title: 'too short', default_service: 'bad' },
  )
  assert.deepEqual(fieldErrorsFromApi([{ loc: ['body'], msg: 'broken' }]), { _form: 'broken' })
  assert.deepEqual(fieldErrorsFromApi('metrics CSV needs a header row with: timestamp, metric, value'),
    { metrics_csv: 'metrics CSV needs a header row with: timestamp, metric, value' })
  assert.deepEqual(fieldErrorsFromApi('logs exceed 5,000 entries'), { logs: 'logs exceed 5,000 entries' })
  assert.deepEqual(fieldErrorsFromApi('deployments exceed 200 rows'), { deployments_csv: 'deployments exceed 200 rows' })
  assert.deepEqual(fieldErrorsFromApi('too many incidents created; try again later'),
    { _form: 'too many incidents created; try again later' })
  assert.deepEqual(fieldErrorsFromApi(null), {})
})

test('parse summary explains what happened to the evidence', () => {
  const lines = summaryLines({
    logs_parsed: 5, metrics_parsed: 2, deployments_parsed: 1, timestamps_inferred: 1, redactions: 2,
    skipped: 1, skipped_details: ['metrics line 4: invalid value'], outside_investigation_window: 3,
    services: ['orders-api', 'orders-db'],
  })
  assert.equal(lines[0], '5 log entries, 2 metric points, 1 deployment received.')
  assert.ok(lines.includes('2 credential-like values were redacted before storage.'))
  assert.ok(lines.includes('1 log line had no readable timestamp and was placed at the incident start.'))
  assert.ok(lines.some((l) => l.startsWith('3 records fall outside the investigation window')))
  assert.ok(lines.includes('1 row was skipped:'))
  assert.deepEqual(summaryLines(null), [])
})

test('parse summary uses singular forms for one item', () => {
  const lines = summaryLines({ logs_parsed: 1, metrics_parsed: 1, deployments_parsed: 0, redactions: 1,
    outside_investigation_window: 1, skipped: 2 })
  assert.equal(lines[0], '1 log entry, 1 metric point, 0 deployments received.')
  assert.ok(lines.includes('1 credential-like value was redacted before storage.'))
  assert.ok(lines.some((l) => l.startsWith('1 record falls outside')))
  assert.ok(lines.includes('2 rows were skipped:'))
})

test('evidence status explains when an incident cannot be investigated', () => {
  assert.equal(evidenceStatus({ logs: 0, metric_points: 0, deployments: 0, usable: 0 }).ready, false)
  assert.match(evidenceStatus({ logs: 0, metric_points: 0, deployments: 0, usable: 0 }).message, /No evidence yet/)
  assert.match(evidenceStatus({ logs: 4, metric_points: 0, deployments: 0, usable: 0 }).message, /outside the investigation window/)
  assert.deepEqual(evidenceStatus({ logs: 4, metric_points: 1, deployments: 0, usable: 5 }),
    { ready: true, message: '5 records available to the investigation.' })
  assert.equal(evidenceStatus(undefined), null)
})

test('file loading rejects files over the limit', async () => {
  const small = { name: 'app.log', size: 10, text: async () => 'hello' }
  assert.equal(await readFileAsText(small, 100), 'hello')
  await assert.rejects(readFileAsText({ name: 'huge.log', size: 10_000, text: async () => '' }, 100), /too large/)
  await assert.rejects(readFileAsText({ name: 'wide.log', size: 200, text: async () => 'x'.repeat(150) }, 100),
    /150 characters; the limit is 100/)
})
