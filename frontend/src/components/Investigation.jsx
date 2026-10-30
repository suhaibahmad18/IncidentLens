import { useEffect, useState } from 'react'

import { api } from '../api.js'
import { isReportOutdated, isTerminal, pluralize, runHeadline } from '../format.js'
import Report from './Report.jsx'
import TraceTimeline from './TraceTimeline.jsx'

const POLL_MS = 1500

export default function Investigation({ slug, config, onRunFinished, blockedReason = null,
  evidenceUpdatedAt = null, flagOutdated = false }) {
  const [runId, setRunId] = useState(null)
  const [run, setRun] = useState(null)
  const [trace, setTrace] = useState([])
  const [evidence, setEvidence] = useState(null)
  const [evidenceError, setEvidenceError] = useState(null)
  const [error, setError] = useState(null)
  const [starting, setStarting] = useState(false)

  // Show the most recent run for this incident, if any. (The parent keys this component by
  // incident, so state starts fresh for every incident.)
  useEffect(() => {
    let cancelled = false
    api.incidentRuns(slug)
      .then((runs) => { if (!cancelled && runs.length) setRunId(runs[0].id) })
      .catch((err) => { if (!cancelled) setError(err.message) })
    return () => { cancelled = true }
  }, [slug])

  // Poll the selected run until it finishes, then load its evidence.
  useEffect(() => {
    if (!runId) return undefined
    let cancelled = false
    let timer = null

    async function poll() {
      try {
        const [latest, steps] = await Promise.all([api.run(runId), api.trace(runId)])
        if (cancelled) return
        setRun(latest)
        setTrace(steps)
        if (isTerminal(latest.status)) {
          onRunFinished?.()
          if (latest.status === 'completed') {
            api.evidence(runId)
              .then((items) => { if (!cancelled) setEvidence(items) })
              .catch((err) => { if (!cancelled) setEvidenceError(err.message) })
          }
          return
        }
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
      if (!cancelled) timer = setTimeout(poll, POLL_MS)
    }

    poll()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [runId, onRunFinished])

  async function start() {
    setStarting(true)
    setError(null)
    setEvidence(null)
    setEvidenceError(null)
    setTrace([])
    setRun({ status: 'pending' })
    try {
      const { run_id: id } = await api.startInvestigation(slug)
      setRunId(id)
    } catch (err) {
      setRun(null)
      setError(err.message)
    } finally {
      setStarting(false)
    }
  }

  const busy = starting || (run && !isTerminal(run.status))
  const report = run?.report
  const outdated = flagOutdated && isTerminal(run?.status) && isReportOutdated(run, evidenceUpdatedAt)
  const pillClass = !run ? '' : run.status === 'failed' ? 'pill-error'
    : !isTerminal(run.status) ? 'pill-live' : report?.status === 'inconclusive' ? 'pill-warn' : 'pill-ok'

  return (
    <section className="card investigation">
      <div className="card-header">
        <div>
          <p className="eyebrow">AI investigation · read-only</p>
          <h3>Investigation</h3>
        </div>
        <button type="button" className="primary" onClick={start}
          disabled={busy || !config?.investigations_enabled || Boolean(blockedReason)}
          aria-describedby={blockedReason ? `blocked-${slug}` : undefined}>
          {busy && <span className="spinner" aria-hidden="true" />}
          {busy ? 'Investigating…' : run ? 'Investigate again' : 'Start investigation'}
        </button>
      </div>

      {config && !config.investigations_enabled && (
        <p className="notice">Investigations are disabled: the server has no LLM API key configured.</p>
      )}
      {blockedReason && <p id={`blocked-${slug}`} className="notice">{blockedReason}</p>}
      {error && <p className="error-box" role="alert">{error}</p>}
      {!run && !error && !blockedReason && (
        <p className="empty">No investigation yet. Start one to let the agent gather evidence with read-only tools.</p>
      )}

      {run && (
        <div aria-live="polite">
          <div className="run-headline">
            <span className={`pill ${pillClass}`}>{run.status}</span>
            <h4 className={`headline ${run.status} ${report?.status ?? ''}`}>{runHeadline(run)}</h4>
          </div>

          {outdated && (
            <p className="notice" role="status">
              New evidence was added after this investigation started, so this report does not reflect it.
              Run <strong>Investigate again</strong> to include it.
            </p>
          )}

          {!isTerminal(run.status) && (
            <p className="muted">
              The agent is querying logs, metrics, deployments and runbooks ({pluralize(trace.length, 'step')} so far).
            </p>
          )}

          {run.status === 'failed' && <p className="error-box">{run.error ?? 'The investigation failed.'}</p>}

          {report && <Report report={report} model={run.model} records={evidence} evidenceError={evidenceError} />}

          <details className="trace" open={!isTerminal(run.status)}>
            <summary>
              <span className="eyebrow">Agent trace</span>
              <span className="muted small">{pluralize(trace.length, 'step')} · every model and tool call, as recorded</span>
            </summary>
            <TraceTimeline steps={trace} />
          </details>
        </div>
      )}
    </section>
  )
}
