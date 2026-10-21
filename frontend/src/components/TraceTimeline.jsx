import { formatDuration, stepLabel } from '../format.js'

export default function TraceTimeline({ steps }) {
  if (!steps?.length) return <p className="muted">No steps recorded yet.</p>

  return (
    <ol className="timeline">
      {steps.map((step) => (
        <li key={step.index} className={`step ${step.type}${step.error ? ' has-error' : ''}`}>
          <details>
            <summary>
              <span className="step-index">{step.index + 1}</span>
              <span className="badge kind">{stepLabel(step)}</span>
              <span className="step-name">{step.name}</span>
              <span className="muted small">
                {formatDuration(step.latency_ms)}
                {step.input_tokens != null && ` · ${step.input_tokens}→${step.output_tokens} tokens`}
                {step.output?.cached && ' · cached'}
              </span>
              {step.error && <span className="error small">{step.error}</span>}
            </summary>
            <div className="step-body">
              <h4>Input</h4>
              <pre>{JSON.stringify(step.input, null, 2)}</pre>
              {step.output != null && (
                <>
                  <h4>Output</h4>
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
