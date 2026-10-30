import { summaryLines } from '../incidentForm.js'

export default function EvidenceSummary({ summary, onDismiss, title = 'Evidence received' }) {
  if (!summary) return null
  return (
    <section className="card notice-card" aria-live="polite">
      <div className="card-header">
        <h3>{title}</h3>
        {onDismiss && <button type="button" className="link-button" onClick={onDismiss}>Dismiss</button>}
      </div>
      <ul className="summary-lines">
        {summaryLines(summary).map((line) => <li key={line}>{line}</li>)}
      </ul>
      {summary.skipped_details?.length > 0 && (
        <ul className="small muted">
          {summary.skipped_details.map((item) => <li key={item}>{item}</li>)}
          {summary.skipped > summary.skipped_details.length && (
            <li>…and {summary.skipped - summary.skipped_details.length} more.</li>
          )}
        </ul>
      )}
    </section>
  )
}
