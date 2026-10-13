import { useEffect, useState } from 'react'

function App() {
  const [health, setHealth] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch('/api/health')
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Backend responded with HTTP ${response.status}`)
        }
        return response.json()
      })
      .then(setHealth)
      .catch((err) => setError(err.message))
  }, [])

  return (
    <main>
      <h1>IncidentLens</h1>
      <p className="subtitle">AI Incident Triage Copilot</p>

      <section className="card">
        <h2>Backend status</h2>
        {error && (
          <p className="status error">
            Backend unavailable: {error}. Is the API running on port 8000?
          </p>
        )}
        {!error && !health && <p className="status">Checking…</p>}
        {health && (
          <p className="status ok">
            {health.service}: <strong>{health.status}</strong>
          </p>
        )}
      </section>
    </main>
  )
}

export default App
