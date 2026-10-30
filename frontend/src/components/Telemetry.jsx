import { useState } from 'react'

import { baselineText, pluralize, relativeMinutes } from '../format.js'
import Sparkline from './Sparkline.jsx'

const TABS = ['Logs', 'Metrics', 'Deployments']

export default function Telemetry({ detail }) {
  const [tab, setTab] = useState('Logs')
  const counts = {
    Logs: detail.logs?.distinct_messages ?? 0,
    Metrics: detail.metrics?.length ?? 0,
    Deployments: detail.deployments?.length ?? 0,
  }

  return (
    <section className="card">
      <div className="card-header">
        <div>
          <p className="eyebrow">Evidence explorer</p>
          <h3>Telemetry</h3>
        </div>
        <div className="tabs" role="tablist" aria-label="Telemetry type">
          {TABS.map((name) => (
            <button key={name} type="button" role="tab" aria-selected={tab === name}
              className={tab === name ? 'tab active' : 'tab'} onClick={() => setTab(name)}>
              {name} <span className="tab-count">{counts[name]}</span>
            </button>
          ))}
        </div>
      </div>
      <div role="tabpanel">
        {tab === 'Logs' && <Logs logs={detail.logs} />}
        {tab === 'Metrics' && <Metrics metrics={detail.metrics} />}
        {tab === 'Deployments' && <Deployments deployments={detail.deployments} />}
      </div>
    </section>
  )
}

function Logs({ logs }) {
  if (!logs?.entries?.length) return <p className="empty">No warnings or errors in the window.</p>
  return (
    <>
      <p className="muted small">
        WARN and above, identical lines grouped. Showing {logs.entries.length} of {pluralize(logs.distinct_messages, 'distinct message')}.
      </p>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr><th>First seen</th><th>Service</th><th>Level</th><th>Message</th><th className="num">Count</th><th>ID</th></tr>
          </thead>
          <tbody>
            {logs.entries.map((entry) => (
              <tr key={entry.evidence_id}>
                <td className="nowrap">{relativeMinutes(entry.first_seen_minutes_from_incident)}</td>
                <td><span className="service">{entry.service}</span></td>
                <td><span className={`level level-${entry.level}`}>{entry.level}</span></td>
                <td className="message">{entry.message}</td>
                <td className="num">{entry.occurrences}</td>
                <td><code className="chip chip-log">{entry.evidence_id}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Metrics({ metrics }) {
  if (!metrics?.length) return <p className="empty">No metrics for this incident.</p>
  return (
    <div className="metric-grid">
      {metrics.map((series) => {
        const deviated = series.first_deviation_minutes_from_incident !== null
        return (
          <div key={series.evidence_id} className={deviated ? 'metric deviated' : 'metric'}>
            <div className="metric-head">
              <span className="metric-name">{series.metric}</span>
              <span className="service">{series.evidence_id.split(':')[1]}</span>
            </div>
            <div className="metric-value">
              {series.latest}<span className="unit"> {series.unit}</span>
            </div>
            <Sparkline points={series.points} baseline={series.baseline_avg}
              deviationMinutes={series.first_deviation_minutes_from_incident}
              label={`${series.metric} trend, ${baselineText(series)}, max ${series.max}`} />
            <div className="metric-foot">
              <span>{baselineText(series)} → max {series.max}</span>
              {deviated
                ? <span className="pill pill-warn">deviates {relativeMinutes(series.first_deviation_minutes_from_incident)}</span>
                : series.baseline_avg === null && <span className="pill">baseline unavailable</span>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Deployments({ deployments }) {
  if (!deployments?.length) return <p className="empty">No deployments in the window.</p>
  return (
    <ol className="deployments">
      {deployments.map((d) => (
        <li key={d.evidence_id}>
          <div className="deploy-head">
            <strong>{d.service}</strong> <code className="version">{d.version}</code>
            <span className={`pill ${d.status === 'succeeded' ? '' : 'pill-error'}`}>{d.status.replace('_', ' ')}</span>
            <span className="muted small">{relativeMinutes(d.minutes_from_incident)}</span>
          </div>
          {d.change_summary && <p>{d.change_summary}</p>}
          <code className="chip chip-deploy">{d.evidence_id}</code>
        </li>
      ))}
    </ol>
  )
}
