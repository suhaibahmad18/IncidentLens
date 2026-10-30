import { baselineText, evidenceKind, evidenceKindKey, formatTimestamp, relativeMinutes } from '../format.js'
import Sparkline from './Sparkline.jsx'

export function EvidenceChip({ id }) {
  const key = evidenceKindKey(id)
  return (
    <span className={`chip chip-${key}`} title={id}>
      <span className="chip-kind">{evidenceKind(id)}</span>
      <code>{id}</code>
    </span>
  )
}

export default function EvidenceList({ citations, records, error }) {
  if (!citations?.length) return <p className="empty small">No evidence was cited.</p>
  const byId = Object.fromEntries((records ?? []).map((item) => [item.evidence_id, item.record]))

  return (
    <>
      {error && <p className="error small">Could not load evidence details: {error}</p>}
      <ul className="evidence-list">
        {citations.map((citation) => (
          <li key={citation.evidence_id}>
            <details className="evidence-item">
              <summary>
                <EvidenceChip id={citation.evidence_id} />
                {citation.why && <span className="why">{citation.why}</span>}
              </summary>
              <EvidenceRecord record={byId[citation.evidence_id]} loading={!records && !error} />
            </details>
          </li>
        ))}
      </ul>
    </>
  )
}

export function EvidenceRecord({ record, loading }) {
  if (loading) return <p className="muted small record">Loading evidence…</p>
  if (!record) return <p className="muted small record">This evidence could not be found.</p>

  switch (record.kind) {
    case 'log':
      return (
        <div className="record">
          <div className="record-meta">
            {formatTimestamp(record.ts)} · <span className="service">{record.service}</span> ·{' '}
            <span className={`level level-${record.level}`}>{record.level}</span>
          </div>
          <pre>{record.message}</pre>
        </div>
      )
    case 'deploy':
      return (
        <div className="record">
          <div className="record-meta">
            <strong>{record.service}</strong> <code className="version">{record.version}</code> · {record.status} ·{' '}
            {formatTimestamp(record.deployed_at)}
          </div>
          {record.change_summary && <p>{record.change_summary}</p>}
        </div>
      )
    case 'metric':
      return (
        <div className="record">
          <Sparkline points={record.points} baseline={record.baseline_avg}
            deviationMinutes={record.first_deviation_minutes_from_incident} width={280} height={56}
            label={`${record.metric} trend`} />
          <div className="record-meta">
            {baselineText(record)}, max {record.max}, latest {record.latest} {record.unit}
            {record.first_deviation_minutes_from_incident !== null &&
              `; first deviation ${relativeMinutes(record.first_deviation_minutes_from_incident)}`}
          </div>
        </div>
      )
    case 'runbook':
      return (
        <div className="record">
          <div className="record-meta">{record.source}</div>
          <pre>{record.content}</pre>
        </div>
      )
    default:
      return null
  }
}
