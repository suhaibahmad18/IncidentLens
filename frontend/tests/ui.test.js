import assert from 'node:assert/strict'
import { test } from 'node:test'

import { confidenceSegments, incidentSummary, latencyBars, runTone, traceSummary } from '../src/ui.js'

test('each run status maps to one tone and a readable label', () => {
  assert.deepEqual(runTone('completed'), { tone: 'ok', label: 'Investigated' })
  assert.deepEqual(runTone('running'), { tone: 'running', label: 'Investigating' })
  assert.deepEqual(runTone('pending'), { tone: 'running', label: 'Queued' })
  assert.deepEqual(runTone('failed'), { tone: 'failed', label: 'Last run failed' })
  assert.deepEqual(runTone(null), { tone: 'idle', label: 'Not investigated' })
  assert.deepEqual(runTone('something-new'), { tone: 'idle', label: 'Not investigated' })
})

test('overview counts come from the incident list', () => {
  const summary = incidentSummary([
    { kind: 'demo', last_run_status: 'completed' },
    { kind: 'demo', last_run_status: 'failed' },
    { kind: 'user', last_run_status: 'running' },
    { kind: 'user', last_run_status: null },
    { kind: 'demo', last_run_status: 'pending' },
  ])
  assert.deepEqual(summary, { total: 5, mine: 2, demos: 3, investigated: 1, running: 2, failed: 1, idle: 1 })
  assert.deepEqual(incidentSummary(undefined), { total: 0, mine: 0, demos: 0, investigated: 0, running: 0, failed: 0, idle: 0 })
})

test('confidence fills 0 to 5 segments', () => {
  assert.equal(confidenceSegments(0.8), 4)
  assert.equal(confidenceSegments(0.95), 5)
  assert.equal(confidenceSegments(0.05), 0)
  assert.equal(confidenceSegments(1.7), 5)
  assert.equal(confidenceSegments(-1), 0)
  assert.equal(confidenceSegments(null), 0)
  assert.equal(confidenceSegments(Number.NaN), 0)
})

test('timeline bars are relative to the slowest step, with a visible minimum', () => {
  assert.deepEqual(latencyBars([{ latency_ms: 1000 }, { latency_ms: 250 }, { latency_ms: 0 }, { latency_ms: null }, {}]),
    [100, 25, 3, null, null])
  assert.deepEqual(latencyBars([{ latency_ms: 0 }, { latency_ms: 0 }]), [3, 3])
  assert.deepEqual(latencyBars([]), [])
})

test('trace summary totals model calls, tools, time and tokens', () => {
  const steps = [
    { type: 'llm_call', latency_ms: 1200, input_tokens: 100, output_tokens: 40 },
    { type: 'tool_call', latency_ms: 30 },
    { type: 'retrieval', latency_ms: 20, error: 'timeout' },
    { type: 'final', latency_ms: null },
  ]
  assert.deepEqual(traceSummary(steps), { steps: 4, modelCalls: 1, toolCalls: 2, errors: 1, durationMs: 1250, tokens: 140 })
  assert.deepEqual(traceSummary(null), { steps: 0, modelCalls: 0, toolCalls: 0, errors: 0, durationMs: 0, tokens: 0 })
})
