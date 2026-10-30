import assert from 'node:assert/strict'
import { test } from 'node:test'

import { describeError } from '../src/api.js'
import {
  baselineText,
  confidenceLabel,
  evidenceKind,
  evidenceKindKey,
  formatDuration,
  formatTimestamp,
  hasUnknowns,
  isReportOutdated,
  isTerminal,
  percent,
  pluralNoun,
  pluralize,
  relativeMinutes,
  reportSections,
  runHeadline,
  sparklineGeometry,
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
  assert.equal(runHeadline({ status: 'completed', report: { status: 'conclusive' } }), 'Investigation complete')
})

test('report sections separate facts, hypothesis, recommendations and unknowns', () => {
  const sections = reportSections({
    status: 'conclusive',
    facts: [
      { statement: 'pool timeouts from 13:58', evidence_ids: ['log:0003'] },
      { statement: 'no evidence attached', evidence_ids: [] },
    ],
    root_cause: 'v7.1.0 leaks connections', affected_service: 'orders-api', confidence: 0.7,
    explanation: 'timeline', alternative_explanations: ['traffic spike'],
    evidence: [{ evidence_id: 'deploy:1', why: 'just before' }],
    remediation: ['Roll back (requires approval)'],
    missing_information: ['no DB metrics'], unsupported_statements: ['disk was full'],
    validation_notes: ['Removed evidence references that no tool returned.'], dropped_citations: ['log:9999'],
  })
  assert.deepEqual(sections.facts, [{ statement: 'pool timeouts from 13:58', evidence_ids: ['log:0003'] }])
  assert.equal(sections.hypothesis.rootCause, 'v7.1.0 leaks connections')
  assert.deepEqual(sections.hypothesis.alternatives, ['traffic spike'])
  assert.deepEqual(sections.recommendations, ['Roll back (requires approval)'])
  assert.deepEqual(sections.unknowns.unsupported, ['disk was full'])
  assert.equal(sections.unknowns.droppedCount, 1)
  assert.equal(hasUnknowns(sections.unknowns), true)
  assert.equal(sections.legacy, false)
})

test('reports saved before facts existed still render without inventing facts', () => {
  const sections = reportSections({ status: 'conclusive', root_cause: 'x', confidence: 0.8, evidence: [] })
  assert.deepEqual(sections.facts, [])
  assert.equal(sections.legacy, true)
  assert.deepEqual(sections.recommendations, [])
  assert.equal(hasUnknowns(sections.unknowns), false)
  assert.equal(reportSections(null), null)
})

test('polling stops only on terminal statuses', () => {
  assert.equal(isTerminal('completed'), true)
  assert.equal(isTerminal('failed'), true)
  assert.equal(isTerminal('running'), false)
  assert.equal(isTerminal('pending'), false)
})

test('API errors become readable messages', () => {
  assert.equal(describeError(404, { detail: 'incident not found' }), 'incident not found')
  assert.match(describeError(422, { detail: [{ msg: 'x' }] }), /highlighted fields/)
  assert.match(describeError(413, null), /too large/)
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

test('a missing metric baseline is shown as unavailable, never as a number', () => {
  assert.equal(baselineText({ baseline_avg: 120 }), 'baseline 120')
  assert.equal(baselineText({ baseline_avg: 0 }), 'baseline 0')
  assert.equal(baselineText({ baseline_avg: null }), 'no baseline (no data from before the incident)')
  assert.equal(baselineText({}), 'no baseline (no data from before the incident)')
})

test('evidence kind keys drive chip styling', () => {
  assert.equal(evidenceKindKey('log:0001'), 'log')
  assert.equal(evidenceKindKey('deploy:2'), 'deploy')
  assert.equal(evidenceKindKey('metric:a:b'), 'metric')
  assert.equal(evidenceKindKey('runbook:x.md#0'), 'runbook')
  assert.equal(evidenceKindKey('weird'), 'other')
})

test('a report is outdated only when evidence was stored after the run started', () => {
  const run = { started_at: '2026-08-20T14:00:00Z' }
  assert.equal(isReportOutdated(run, '2026-08-20T14:05:00Z'), true)
  assert.equal(isReportOutdated(run, '2026-08-20T13:59:00Z'), false)
  assert.equal(isReportOutdated(run, null), false)
  assert.equal(isReportOutdated(null, '2026-08-20T14:05:00Z'), false)
  assert.equal(isReportOutdated({ started_at: 'garbage' }, '2026-08-20T14:05:00Z'), false)
})

test('sparkline geometry includes baseline, incident marker and deviation dot', () => {
  const points = [
    { minutes_from_incident: -60, value: 100 },
    { minutes_from_incident: -30, value: 100 },
    { minutes_from_incident: 0, value: 300 },
    { minutes_from_incident: 30, value: 300 },
  ]
  const g = sparklineGeometry(points, { width: 100, height: 40, baseline: 100, deviationMinutes: 0 })
  assert.equal(g.line.split(' ').length, 4)
  assert.ok(g.area.startsWith('M3.0,37.0 L3.0,37.0') && g.area.endsWith('Z'))
  assert.equal(g.baselineY, 37) // baseline is the minimum here, so it sits on the bottom edge
  assert.ok(g.markerX > 3 && g.markerX < 97)
  assert.deepEqual(g.deviation, { x: g.markerX, y: 3 })
})

test('sparkline geometry without a baseline draws no baseline line', () => {
  const g = sparklineGeometry([{ minutes_from_incident: 0, value: 2100 }], { width: 100, height: 40 })
  assert.equal(g.baselineY, null)
  assert.equal(g.deviation, null)
  assert.equal(sparklineGeometry([], { width: 100, height: 40 }), null)
})

test('sparkline scales values into the box', () => {
  const points = sparklinePoints([0, 50, 100], 100, 20).split(' ')
  assert.equal(points.length, 3)
  assert.equal(points[0], '2.0,18.0')
  assert.equal(points[2], '98.0,2.0')
  assert.equal(sparklinePoints([], 100, 20), '')
  assert.equal(sparklinePoints([5, 5], 100, 20).split(' ').length, 2)
})

test('pluralize picks the singular only for exactly one', () => {
  assert.equal(pluralize(1, 'deployment'), '1 deployment')
  assert.equal(pluralize(0, 'deployment'), '0 deployments')
  assert.equal(pluralize(2, 'log entry', 'log entries'), '2 log entries')
  assert.equal(pluralize(1, 'log entry', 'log entries'), '1 log entry')
  assert.equal(pluralize(1738, 'metric point'), '1,738 metric points')
  assert.equal(pluralNoun(1, 'step'), 'step')
  assert.equal(pluralNoun(30, 'step'), 'steps')
})
