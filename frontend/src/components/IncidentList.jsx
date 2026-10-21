import { formatTimestamp } from '../format.js'

export default function IncidentList({ incidents, selected, onSelect, error, loading }) {
  if (loading) return <p className="muted">Loading incidents…</p>
  if (error) return <p className="error" role="alert">{error}</p>
  if (!incidents.length) return <p className="muted">No incidents found. Run the seed command to load scenarios.</p>

  return (
    <ul className="incident-list">
      {incidents.map((incident) => (
        <li key={incident.slug}>
          <button
            type="button"
            className={incident.slug === selected ? 'incident-item selected' : 'incident-item'}
            onClick={() => onSelect(incident.slug)}
            aria-current={incident.slug === selected ? 'true' : undefined}
          >
            <span className="incident-title">{incident.title}</span>
            <span className="incident-meta">
              {formatTimestamp(incident.incident_start)}
              {incident.last_run_status && <span className={`badge ${incident.last_run_status}`}>{incident.last_run_status}</span>}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
