import { useEffect, useState } from 'react'

import { api } from '../api.js'
import { confidenceLabel, isTerminal, percent, runHeadline } from '../format.js'
import EvidenceList from './EvidenceList.jsx'
import TraceTimeline from './TraceTimeline.jsx'

const POLL_MS = 1500

export default function Investigation({ slug, config, onRunFinished }) {
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

  return (
    <section className="card investigation">
      <div className="card-header">
        <h3>Investigation</h3>
        <button type="button" className="primary" onClick={start} disabled={busy || !config?.investigations_enabled}>
          {busy ? 'Investigating…' : run ? 'Investigate again' : 'Start investigation'}
        </button>
      </div>

      {config && !config.investigations_enabled && (
        <p className="notice">Investigations are disabled: the server has no LLM API key configured.</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {!run && !error && <p className="muted">No investigation yet. Start one to let the agent gather evidence.</p>}

      {run && (
        <div aria-live="polite">
          <h4 className={`headline ${run.status} ${report?.status ?? ''}`}>{runHeadline(run)}</h4>

          {!isTerminal(run.status) && (
            <p className="muted">
              The agent is querying logs, metrics, deployments and runbooks ({trace.length} steps so far).
            </p>
          )}

          {run.status === 'failed' && <p className="error">{run.error ?? 'The investigation failed.'}</p>}

          {report && (
            <div className="report">
              <p className="root-cause">{report.root_cause}</p>
              <dl className="facts">
                <div><dt>Affected service</dt><dd>{report.affected_service ?? 'undetermined'}</dd></div>
                <div>
                  <dt>Confidence</dt>
                  <dd>
                    <span className={`confidence ${confidenceLabel(report.confidence)}`}>
                      {confidenceLabel(report.confidence)} ({percent(report.confidence)})
                    </span>
                  </dd>
                </div>
                <div><dt>Model</dt><dd>{run.model}</dd></div>
              </dl>

              <h4>Explanation</h4>
              <p>{report.explanation}</p>

              <h4>Evidence ({report.evidence.length})</h4>
              <EvidenceList citations={report.evidence} records={evidence} error={evidenceError} />

              {report.remediation?.length > 0 && (
                <>
                  <h4>Suggested remediation <span className="badge advisory">advisory: requires human approval</span></h4>
                  <ul>{report.remediation.map((step) => <li key={step}>{step}</li>)}</ul>
                </>
              )}

              {report.missing_information?.length > 0 && (
                <>
                  <h4>What is still unknown</h4>
                  <ul>{report.missing_information.map((item) => <li key={item}>{item}</li>)}</ul>
                </>
              )}

              {report.validation_notes?.length > 0 && (
                <p className="notice small">{report.validation_notes.join(' ')}</p>
              )}
            </div>
          )}

          <h4>Agent trace</h4>
          <TraceTimeline steps={trace} />
        </div>
      )}
    </section>
  )
}
