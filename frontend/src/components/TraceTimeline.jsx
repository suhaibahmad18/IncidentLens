import { formatDuration, stepLabel } from '../format.js'

export default function TraceTimeline({ steps }) {
  if (!steps?.length) return <p className="empty small">No steps recorded yet.</p>

  return (
    <ol className="timeline">
      {steps.map((step) => (
        <li key={step.index} className={`step step-${step.type}${step.error ? ' has-error' : ''}`}>
          <span className="step-node" aria-hidden="true" />
          <details>
            <summary>
              <span className="step-index">{String(step.index + 1).padStart(2, '0')}</span>
              <span className="step-type">{stepLabel(step)}</span>
              <span className="step-name">{step.name}</span>
              <span className="step-meta">
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
  )
}
