import { confidenceLabel, hasUnknowns, percent, pluralize, reportSections } from '../format.js'
import { confidenceSegments } from '../ui.js'
import EvidenceList, { EvidenceChip, EvidenceRecord } from './EvidenceList.jsx'

const SEGMENTS = 5

function ConfidenceMeter({ value }) {
  const label = confidenceLabel(value)
  const width = typeof value === 'number' ? Math.round(Math.min(Math.max(value, 0), 1) * 100) : 0
  const filled = confidenceSegments(value, SEGMENTS)
  return (
    <div className={`confidence confidence-${label}`}>
      <div className="confidence-gauge" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={width}
        aria-label="Confidence">
        {Array.from({ length: SEGMENTS }, (_, i) => (
          <span key={i} className={i < filled ? 'segment filled' : 'segment'} />
        ))}
      </div>
      <span className="confidence-text">{label} · {percent(value)}</span>
    </div>
  )
}

// Facts (observed) -> hypothesis (inferred) -> recommendations (advisory) -> unknowns.
export default function Report({ report, model, records, evidenceError }) {
  const sections = reportSections(report)
  if (!sections) return null
  const { facts, hypothesis, recommendations, unknowns, legacy } = sections
  const byId = Object.fromEntries((records ?? []).map((item) => [item.evidence_id, item.record]))

  return (
    <div className="report">
      <section className="report-section rail-facts" aria-labelledby="report-facts">
        <header className="section-head">
          <span className="section-index">1</span>
          <h4 id="report-facts">Observed facts</h4>
          <span className="section-hint">read directly from evidence</span>
        </header>
        {facts.length === 0 ? (
          <p className="empty small">
            {legacy
              ? 'This report was created before observed facts were recorded separately.'
              : 'No statement could be backed by the retrieved evidence.'}
          </p>
        ) : (
          <ul className="fact-list">
            {facts.map((fact) => (
              <li key={fact.statement}>
                <p>{fact.statement}</p>
                <div className="fact-evidence">
                  {fact.evidence_ids.map((id) => (
                    <details key={id} className="evidence-item compact">
                      <summary><EvidenceChip id={id} /></summary>
                      <EvidenceRecord record={byId[id]} loading={!records && !evidenceError} />
                    </details>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="report-section rail-hypothesis" aria-labelledby="report-hypothesis">
        <header className="section-head">
          <span className="section-index">2</span>
          <h4 id="report-hypothesis">Root-cause hypothesis</h4>
          <span className="section-hint">AI interpretation of the facts</span>
        </header>
        <p className="root-cause">{hypothesis.rootCause}</p>
        <dl className="kv">
          <div><dt>Affected service</dt><dd><span className="service">{hypothesis.affectedService ?? 'undetermined'}</span></dd></div>
          <div><dt>Confidence</dt><dd><ConfidenceMeter value={hypothesis.confidence} /></dd></div>
          <div><dt>Model</dt><dd className="mono small">{model}</dd></div>
        </dl>
        {hypothesis.explanation && <p className="explanation">{hypothesis.explanation}</p>}
        {hypothesis.alternatives.length > 0 && (
          <div className="subsection">
            <h5>Other possible explanations</h5>
            <ul className="plain-list">{hypothesis.alternatives.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        )}
        <div className="subsection">
          <h5>Supporting evidence <span className="count">{hypothesis.citations.length}</span></h5>
          <EvidenceList citations={hypothesis.citations} records={records} error={evidenceError} />
        </div>
      </section>

      <section className="report-section rail-recommendations" aria-labelledby="report-recommendations">
        <header className="section-head">
          <span className="section-index">3</span>
          <h4 id="report-recommendations">Recommendations</h4>
          <span className="pill pill-warn">advisory · nothing has been executed</span>
        </header>
        {recommendations.length === 0
          ? <p className="empty small">No recommendations.</p>
          : <ol className="steps">{recommendations.map((step) => <li key={step}>{step}</li>)}</ol>}
      </section>

      <section className="report-section rail-unknowns" aria-labelledby="report-unknowns">
        <header className="section-head">
          <span className="section-index">4</span>
          <h4 id="report-unknowns">Unknowns and limitations</h4>
        </header>
        {!hasUnknowns(unknowns) && <p className="empty small">None reported.</p>}
        {unknowns.missing.length > 0 && (
          <ul className="plain-list">{unknowns.missing.map((item) => <li key={item}>{item}</li>)}</ul>
        )}
        {unknowns.unsupported.length > 0 && (
          <div className="subsection">
            <h5>Claims not supported by the evidence (not treated as facts)</h5>
            <ul className="plain-list">{unknowns.unsupported.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        )}
        {(unknowns.notes.length > 0 || unknowns.droppedCount > 0) && (
          <div className="callout">
            <p><strong>Checks applied to the AI&apos;s answer</strong></p>
            <ul>
              {unknowns.notes.map((note) => <li key={note}>{note}</li>)}
              {unknowns.droppedCount > 0 && <li>{pluralize(unknowns.droppedCount, 'invalid evidence reference')} removed.</li>}
            </ul>
          </div>
        )}
      </section>
    </div>
  )
}
