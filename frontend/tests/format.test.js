import assert from 'node:assert/strict'
import { test } from 'node:test'

import { describeError } from '../src/api.js'
import {
  confidenceLabel,
  evidenceKind,
  formatDuration,
  formatTimestamp,
  isTerminal,
  percent,
  relativeMinutes,
  runHeadline,
  sparklinePoints,
  stepLabel,
} from '../src/format.js'

test('relativeMinutes describes time around the incident', () => {
  assert.equal(relativeMinutes(-20), '20m before')
  assert.equal(relativeMinutes(2.5), '2.5m after')
  assert.equal(relativeMinutes(-150), '2h 30m before')
  assert.equal(relativeMinutes(0), 'at incident start')
  assert.equal(relativeMinutes(null), '—')
})

test('confidence labels and percentages', () => {
  assert.equal(confidenceLabel(0.85), 'high')
  assert.equal(confidenceLabel(0.5), 'medium')
  assert.equal(confidenceLabel(0.2), 'low')
  assert.equal(confidenceLabel(undefined), 'unknown')
  assert.equal(percent(0.4), '40%')
  assert.equal(percent(null), '—')
})

test('evidence kinds come from the stable ID prefix', () => {
  assert.equal(evidenceKind('log:0042'), 'Log')
  assert.equal(evidenceKind('deploy:2'), 'Deployment')
  assert.equal(evidenceKind('metric:payments-db:db_connections_in_use'), 'Metric')
  assert.equal(evidenceKind('runbook:tls-certificate-expiry.md#1'), 'Runbook')
  assert.equal(evidenceKind('weird'), 'Evidence')
})

test('run headline covers every state, including inconclusive', () => {
  assert.equal(runHeadline(null), null)
  assert.equal(runHeadline({ status: 'pending' }), 'Investigating…')
  assert.equal(runHeadline({ status: 'running' }), 'Investigating…')
  assert.equal(runHeadline({ status: 'failed' }), 'Investigation failed')
  assert.match(runHeadline({ status: 'completed', report: { status: 'inconclusive' } }), /^Inconclusive/)
  assert.equal(runHeadline({ status: 'completed', report: { status: 'conclusive' } }), 'Likely root cause')
})

test('polling stops only on terminal statuses', () => {
  assert.equal(isTerminal('completed'), true)
  assert.equal(isTerminal('failed'), true)
  assert.equal(isTerminal('running'), false)
  assert.equal(isTerminal('pending'), false)
})

test('API errors become readable messages', () => {
  assert.equal(describeError(404, { detail: 'incident not found' }), 'incident not found')
  assert.equal(describeError(422, { detail: [{ msg: 'x' }] }), 'The request was not valid.')
  assert.match(describeError(0, null), /Cannot reach/)
  assert.match(describeError(502, null), /HTTP 502/)
})

test('trace and time formatting', () => {
  assert.equal(stepLabel({ type: 'retrieval' }), 'Runbook search')
  assert.equal(formatDuration(1530), '1.5s')
  assert.equal(formatDuration(80), '80ms')
  assert.equal(formatTimestamp('2026-03-10T14:00:00+00:00'), '2026-03-10 14:00Z')
  assert.equal(formatTimestamp('nonsense'), '—')
})

test('sparkline scales values into the box', () => {
  const points = sparklinePoints([0, 50, 100], 100, 20).split(' ')
  assert.equal(points.length, 3)
  assert.equal(points[0], '2.0,18.0')
  assert.equal(points[2], '98.0,2.0')
  assert.equal(sparklinePoints([], 100, 20), '')
  assert.equal(sparklinePoints([5, 5], 100, 20).split(' ').length, 2)
})
