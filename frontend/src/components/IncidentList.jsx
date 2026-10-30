import { formatTimestamp } from '../format.js'

const STATUS_TEXT = { completed: 'investigated', failed: 'last run failed', running: 'investigating', pending: 'queued' }

// Signed in: "Your incidents" (private to the account) above the public demos.
// Visitors: the demos, plus an invitation to sign in for private incidents.
export default function IncidentList({ incidents, selected, onSelect, onNew, creating, error, loading,
  signedIn, signInHref, registerHref }) {
  const mine = incidents.filter((incident) => incident.kind === 'user')
  const demos = incidents.filter((incident) => incident.kind !== 'user')

  return (
    <nav aria-label="Incidents">
      {signedIn ? (
        <button type="button" className={creating ? 'primary new-incident active' : 'primary new-incident'}
          onClick={onNew} aria-pressed={creating}>
          <span aria-hidden="true">+</span> New incident
        </button>
      ) : (
        <div className="callout signin-callout">
          <strong>Investigate your own incidents</strong>
          <p className="muted small">
            <a href={signInHref}>Sign in</a>
            {registerHref && <> or <a href={registerHref}>create an account</a></>} to add private incidents
            with your own logs, metrics and deployments.
          </p>
        </div>
      )}

      {loading && <p className="muted small">Loading incidents…</p>}
      {error && <p className="error-box small" role="alert">{error}</p>}

      {!loading && !error && (
        <>
          {signedIn && (
            <IncidentGroup title="Your incidents" kind="user" items={mine} selected={selected} onSelect={onSelect}
              empty="None yet. Create one to investigate your own logs and metrics. Only you can see them." />
          )}
          <IncidentGroup title="Demo incidents" kind="demo" items={demos} selected={selected} onSelect={onSelect}
            empty="No demo incidents found. Run the seed command to load them." />
        </>
      )}
    </nav>
  )
}

function IncidentGroup({ title, kind, items, selected, onSelect, empty }) {
  const headingId = `group-${kind}`
  return (
    <section className="incident-group" aria-labelledby={headingId}>
      <h2 id={headingId} className="group-title">
        {title} <span className="count">{items.length}</span>
      </h2>
      {items.length === 0 ? (
        <p className="muted small">{empty}</p>
      ) : (
        <ul className="incident-list">
          {items.map((incident) => {
            const status = incident.last_run_status
            return (
              <li key={incident.slug}>
                <button
                  type="button"
                  className={`incident-item kind-${incident.kind}${incident.slug === selected ? ' selected' : ''}`}
                  onClick={() => onSelect(incident.slug)}
                  aria-current={incident.slug === selected ? 'true' : undefined}
                >
                  <span className="incident-title">{incident.title}</span>
                  <span className="incident-meta">
                    <span className={`status-dot status-${status ?? 'none'}`} aria-hidden="true" />
                    <span>{status ? STATUS_TEXT[status] ?? status : 'not investigated'}</span>
                    <span aria-hidden="true">·</span>
                    <span>{formatTimestamp(incident.incident_start)}</span>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
