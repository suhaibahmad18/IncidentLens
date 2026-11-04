import { incidentSummary } from '../ui.js'

// Home-page overview: counts derived from the incident list already on the page.
export default function Overview({ incidents, signedIn }) {
  const summary = incidentSummary(incidents)
  const tiles = [
    { key: 'total', label: 'Incidents', value: summary.total,
      hint: signedIn ? `${summary.mine} yours · ${summary.demos} demo` : `${summary.demos} demo` },
    { key: 'ok', label: 'Investigated', value: summary.investigated, hint: 'report available' },
    { key: 'running', label: 'In progress', value: summary.running, hint: 'investigating now' },
    { key: 'failed', label: 'Failed runs', value: summary.failed, hint: 'last run failed' },
    { key: 'idle', label: 'Not investigated', value: summary.idle, hint: 'awaiting a run' },
  ]
  return (
    <section className="overview" aria-label="Incident overview">
      {tiles.map((tile) => (
        <div key={tile.key} className={`overview-tile tone-${tile.key}`}>
          <span className="overview-label">{tile.label}</span>
          <span className="overview-value">{tile.value}</span>
          <span className="overview-hint">{tile.hint}</span>
        </div>
      ))}
    </section>
  )
}
