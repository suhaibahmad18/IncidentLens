import { useCallback, useEffect, useState } from 'react'

import { api } from './api.js'
import IncidentList from './components/IncidentList.jsx'
import Investigation from './components/Investigation.jsx'
import Telemetry from './components/Telemetry.jsx'
import { formatTimestamp } from './format.js'

function App() {
  const [config, setConfig] = useState(null)
  const [incidents, setIncidents] = useState([])
  const [listError, setListError] = useState(null)
  const [listLoading, setListLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  // { slug, data, error } for the most recently loaded incident; stale entries are ignored below.
  const [loaded, setLoaded] = useState(null)

  const loadIncidents = useCallback(() => {
    api.incidents()
      .then((items) => {
        setIncidents(items)
        setListError(null)
        setSelected((current) => current ?? items[0]?.slug ?? null)
      })
      .catch((err) => setListError(err.message))
      .finally(() => setListLoading(false))
  }, [])

  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig({ investigations_enabled: false }))
    loadIncidents()
  }, [loadIncidents])

  useEffect(() => {
    if (!selected) return undefined
    let cancelled = false
    api.incident(selected)
      .then((data) => { if (!cancelled) setLoaded({ slug: selected, data, error: null }) })
      .catch((err) => { if (!cancelled) setLoaded({ slug: selected, data: null, error: err.message }) })
    return () => { cancelled = true }
  }, [selected])

  const current = loaded?.slug === selected ? loaded : null
  const detail = current?.data
  const detailError = current?.error

  return (
    <div className="layout">
      <header className="topbar">
        <div>
          <h1>IncidentLens</h1>
          <p className="subtitle">AI incident triage copilot: read-only investigation, evidence-backed answers</p>
        </div>
        {config?.model && <span className="badge">{config.model}</span>}
      </header>

      <aside className="sidebar">
        <h2>Incidents</h2>
        <IncidentList incidents={incidents} selected={selected} onSelect={setSelected}
          error={listError} loading={listLoading} />
      </aside>

      <main className="content">
        {!selected && !listLoading && <p className="muted">Select an incident.</p>}
        {detailError && <p className="error" role="alert">{detailError}</p>}
        {selected && !detail && !detailError && <p className="muted">Loading incident…</p>}
        {detail && (
          <>
            <section className="card alert-card">
              <h2>{detail.title}</h2>
              <p className="alert-text">{detail.alert_summary}</p>
              <p className="muted small">Incident start: {formatTimestamp(detail.incident_start)}</p>
            </section>
            <Investigation key={detail.slug} slug={detail.slug} config={config} onRunFinished={loadIncidents} />
            <Telemetry detail={detail} />
          </>
        )}
      </main>
    </div>
  )
}

export default App
