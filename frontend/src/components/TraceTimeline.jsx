import { formatDuration, pluralize, stepLabel } from '../format.js'
import { latencyBars, traceSummary } from '../ui.js'

export default function TraceTimeline({ steps }) {
  if (!steps?.length) return <p className="empty small">No steps recorded yet.</p>
  const bars = latencyBars(steps)
  const totals = traceSummary(steps)

  return (
    <>
      <dl className="trace-summary">
        <div className="tone-model"><dt>Model calls</dt><dd>{totals.modelCalls}</dd></div>
        <div className="tone-tool"><dt>Tool calls</dt><dd>{totals.toolCalls}</dd></div>
        <div><dt>Total time</dt><dd>{formatDuration(totals.durationMs) || '0ms'}</dd></div>
        <div><dt>Tokens</dt><dd>{totals.tokens.toLocaleString('en-US')}</dd></div>
        {totals.errors > 0 && <div className="tone-error"><dt>Errors</dt><dd>{totals.errors}</dd></div>}
      </dl>
      <ol className="timeline" aria-label={`Agent trace, ${pluralize(totals.steps, 'step')}`}>
        {steps.map((step, i) => (
          <li key={step.index} className={`step step-${step.type}${step.error ? ' has-error' : ''}`}>
            <span className="step-node" aria-hidden="true" />
            <details>
              <summary>
                <span className="step-index">{String(step.index + 1).padStart(2, '0')}</span>
                <span className="step-type">{stepLabel(step)}</span>
                <span className="step-name">{step.name}</span>
                <span className="step-meta">
                  {bars[i] !== null && (
                    <span className="step-bar" aria-hidden="true"><span style={{ width: `${bars[i]}%` }} /></span>
                  )}
                  {formatDuration(step.latency_ms)}
                  {step.input_tokens != null && ` · ${step.input_tokens}→${step.output_tokens} tok`}
                  {step.output?.cached && ' · cached'}
                </span>
                {step.error && <span className="step-error">{step.error}</span>}
              </summary>
              <div className="step-body">
                <h5>Input</h5>
                <pre>{JSON.stringify(step.input, null, 2)}</pre>
                {step.output != null && (
                  <>
                    <h5>Output</h5>
                    <pre>{JSON.stringify(step.output, null, 2)}</pre>
                  </>
                )}
              </div>
            </details>
          </li>
        ))}
      </ol>
    </>
  )
}
