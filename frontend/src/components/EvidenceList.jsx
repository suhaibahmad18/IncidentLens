import { evidenceKind, formatTimestamp, relativeMinutes } from '../format.js'
import Sparkline from './Sparkline.jsx'

export default function EvidenceList({ citations, records, error }) {
  if (!citations?.length) return <p className="muted">No evidence was cited.</p>
  const byId = Object.fromEntries((records ?? []).map((item) => [item.evidence_id, item.record]))

  return (
    <>
      {error && <p className="error small">Could not load evidence details: {error}</p>}
      <ul className="evidence-list">
        {citations.map((citation) => (
          <li key={citation.evidence_id}>
            <details>
              <summary>
                <span className="badge kind">{evidenceKind(citation.evidence_id)}</span>
                <code>{citation.evidence_id}</code>
                <span className="why">{citation.why}</span>
              </summary>
              <EvidenceRecord record={byId[citation.evidence_id]} loading={!records && !error} />
            </details>
          </li>
        ))}
      </ul>
    </>
  )
}

function EvidenceRecord({ record, loading }) {
  if (loading) return <p className="muted small">Loading…</p>
  if (!record) return <p className="muted small">This evidence could not be found.</p>

  switch (record.kind) {
    case 'log':
      return (
        <div className="record">
          <div className="muted small">{formatTimestamp(record.ts)} · {record.service} · {record.level}</div>
          <pre>{record.message}</pre>
        </div>
      )
    case 'deploy':
      return (
        <div className="record">
          <div><strong>{record.service} {record.version}</strong> ({record.status}) at {formatTimestamp(record.deployed_at)}</div>
          <div>{record.change_summary}</div>
        </div>
      )
    case 'metric':
      return (
        <div className="record">
          <Sparkline points={record.points} width={240} height={48} label={`${record.metric} trend`} />
          <div className="muted small">
            baseline {record.baseline_avg}, max {record.max}, latest {record.latest} {record.unit}
            {record.first_deviation_minutes_from_incident !== null &&
              `; first deviation ${relativeMinutes(record.first_deviation_minutes_from_incident)}`}
          </div>
        </div>
      )
    case 'runbook':
      return (
        <div className="record">
          <div className="muted small">{record.source}</div>
          <pre>{record.content}</pre>
        </div>
      )
    default:
      return null
  }
}
