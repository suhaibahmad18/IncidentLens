import { useState } from 'react'

import { relativeMinutes } from '../format.js'
import Sparkline from './Sparkline.jsx'

const TABS = ['Logs', 'Metrics', 'Deployments']

export default function Telemetry({ detail }) {
  const [tab, setTab] = useState('Logs')

  return (
    <section className="card">
      <div className="card-header">
        <h3>Telemetry</h3>
        <div className="tabs" role="tablist">
          {TABS.map((name) => (
            <button key={name} type="button" role="tab" aria-selected={tab === name}
              className={tab === name ? 'tab active' : 'tab'} onClick={() => setTab(name)}>
              {name}
            </button>
          ))}
        </div>
      </div>
      {tab === 'Logs' && <Logs logs={detail.logs} />}
      {tab === 'Metrics' && <Metrics metrics={detail.metrics} />}
      {tab === 'Deployments' && <Deployments deployments={detail.deployments} />}
    </section>
  )
}

function Logs({ logs }) {
  if (!logs?.entries?.length) return <p className="muted">No warnings or errors in the window.</p>
  return (
    <>
      <p className="muted small">
        WARN and above, identical lines grouped. Showing {logs.entries.length} of {logs.distinct_messages} distinct messages.
      </p>
      <table className="data-table">
        <thead>
          <tr><th>First seen</th><th>Service</th><th>Level</th><th>Message</th><th>Count</th><th>ID</th></tr>
        </thead>
        <tbody>
          {logs.entries.map((entry) => (
            <tr key={entry.evidence_id}>
              <td>{relativeMinutes(entry.first_seen_minutes_from_incident)}</td>
              <td>{entry.service}</td>
              <td><span className={`level ${entry.level}`}>{entry.level}</span></td>
              <td className="message">{entry.message}</td>
              <td>{entry.occurrences}</td>
              <td><code>{entry.evidence_id}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

function Metrics({ metrics }) {
  if (!metrics?.length) return <p className="muted">No metrics for this incident.</p>
  return (
    <div className="metric-grid">
      {metrics.map((series) => (
        <div key={series.evidence_id} className="metric">
          <div className="metric-name">{series.evidence_id.replace(/^metric:/, '')}</div>
          <Sparkline points={series.points} label={`${series.metric} trend`} />
          <div className="muted small">
            baseline {series.baseline_avg} → max {series.max} {series.unit}
            {series.first_deviation_minutes_from_incident !== null &&
              `, deviates ${relativeMinutes(series.first_deviation_minutes_from_incident)}`}
          </div>
        </div>
      ))}
    </div>
  )
}

function Deployments({ deployments }) {
  if (!deployments?.length) return <p className="muted">No deployments in the window.</p>
  return (
    <ul className="deployments">
      {deployments.map((d) => (
        <li key={d.evidence_id}>
          <strong>{d.service} {d.version}</strong> <span className="muted">({relativeMinutes(d.minutes_from_incident)}, {d.status})</span>
          <div>{d.change_summary}</div>
          <code>{d.evidence_id}</code>
        </li>
      ))}
    </ul>
  )
}
