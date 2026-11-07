import { useCallback, useEffect, useState } from 'react'

import { api, setCsrfToken, setSessionLostHandler } from './api.js'
import { loginRedirect, parseRoute, requiresAccount, routeHash } from './auth.js'
import AddEvidence from './components/AddEvidence.jsx'
import AuthPage from './components/AuthPage.jsx'
import EvidenceSummary from './components/EvidenceSummary.jsx'
import IncidentList from './components/IncidentList.jsx'
import Investigation from './components/Investigation.jsx'
import LoadingCard from './components/LoadingCard.jsx'
import NewIncidentForm from './components/NewIncidentForm.jsx'
import Overview from './components/Overview.jsx'
import Telemetry from './components/Telemetry.jsx'
import ThemeToggle from './components/ThemeToggle.jsx'
import { formatTimestamp, pluralNoun } from './format.js'
import { EMPTY_INCIDENT, evidenceStatus } from './incidentForm.js'
import { applyTheme, otherTheme, readSavedTheme, resolveTheme, saveTheme } from './theme.js'
import { runTone } from './ui.js'

function LensMark() {
  return (
    <svg className="logo" viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
      <circle cx="14" cy="14" r="9" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <path d="M8.5 15.5 L11.5 12.5 L14 15 L17 10 L19.5 13" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" />
      <path d="M21 21 L27 27" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

function navigate(hash, { replace = false } = {}) {
  if (window.location.hash === hash) return
  if (replace) window.history.replaceState(null, '', hash)
  else window.location.hash = hash
  if (replace) window.dispatchEvent(new HashChangeEvent('hashchange'))
}

function useRoute() {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash))
  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.hash))
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  return route
}

function prefersDark() {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

// The theme is already on <html> (public/theme-init.js ran before first paint); this keeps React in
// sync, saves explicit choices, and follows the OS setting until the user picks one.
function useTheme() {
  const [theme, setTheme] = useState(() => {
    const current = document.documentElement.dataset.theme
    return current === 'dark' || current === 'light' ? current : resolveTheme(readSavedTheme(window.localStorage), prefersDark())
  })

  useEffect(() => { applyTheme(document.documentElement, theme) }, [theme])

  useEffect(() => {
    const query = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!query) return undefined
    const follow = (event) => { if (!readSavedTheme(window.localStorage)) setTheme(event.matches ? 'dark' : 'light') }
    query.addEventListener('change', follow)
    return () => query.removeEventListener('change', follow)
  }, [])

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next = otherTheme(current)
      saveTheme(window.localStorage, next)
      return next
    })
  }, [])
  return [theme, toggle]
}

function UserMenu({ auth, registrationEnabled, onLogout, route, theme, onToggleTheme }) {
  const toggle = <ThemeToggle theme={theme} onToggle={onToggleTheme} />
  if (auth.status === 'loading') return <div className="user-menu">{toggle}</div>
  if (auth.status === 'signed-in') {
    return (
      <div className="user-menu">
        {toggle}
        <span className="user-email" title={`Signed in as ${auth.user.email}`}>{auth.user.email}</span>
        <button type="button" className="topbar-button" onClick={onLogout} disabled={auth.busy}>
          {auth.busy ? 'Signing out…' : 'Sign out'}
        </button>
      </div>
    )
  }
  const next = route.name === 'login' || route.name === 'register' ? route.next : routeHash(route)
  return (
    <div className="user-menu">
      {toggle}
      <a className="topbar-button" href={routeHash({ name: 'login', next })}>Sign in</a>
      {registrationEnabled && (
        <a className="topbar-button strong" href={routeHash({ name: 'register', next })} aria-label="Create account">
          <span className="label-long" aria-hidden="true">Create account</span>
          <span className="label-short" aria-hidden="true">Sign up</span>
        </a>
      )}
    </div>
  )
}

function App() {
  const route = useRoute()
  const [theme, toggleTheme] = useTheme()
  const [config, setConfig] = useState(null)
  // status: 'loading' until /api/auth/me answers, then 'signed-in' or 'anonymous'.
  const [auth, setAuth] = useState({ status: 'loading', user: null, busy: false })
  const [authNotice, setAuthNotice] = useState(null)
  // The incident list as last loaded, tagged with the account it was loaded for.
  const [list, setList] = useState({ key: null, items: [], error: null })
  const [draft, setDraft] = useState(EMPTY_INCIDENT) // kept while navigating, cleared on success or sign-out
  const [summaries, setSummaries] = useState({}) // slug -> latest evidence summary from the server
  const [detailVersion, setDetailVersion] = useState(0)
  // { slug, version, data, error } for the most recently loaded incident; stale entries are ignored.
  const [loaded, setLoaded] = useState(null)
  const [deleting, setDeleting] = useState(false)

  const signedIn = auth.status === 'signed-in'
  const userId = auth.user?.id ?? null

  const forgetAccountState = useCallback(() => {
    setCsrfToken(null)
    setDraft(EMPTY_INCIDENT)
    setSummaries({})
    setLoaded(null)
  }, [])

  // Session restoration on page load / refresh.
  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig({ investigations_enabled: false, registration_enabled: false }))
    api.me()
      .then((session) => {
        if (session) {
          setCsrfToken(session.csrf_token)
          setAuth({ status: 'signed-in', user: session.user, busy: false })
        } else {
          setAuth({ status: 'anonymous', user: null, busy: false })
        }
      })
      .catch(() => setAuth({ status: 'anonymous', user: null, busy: false }))
  }, [])

  // The server rejected our session mid-use (expired or signed out elsewhere).
  useEffect(() => {
    setSessionLostHandler(() => {
      forgetAccountState()
      setAuth({ status: 'anonymous', user: null, busy: false })
      setAuthNotice('Your session has ended. Sign in again to continue.')
      const current = parseRoute(window.location.hash)
      navigate(loginRedirect(current.name === 'login' || current.name === 'register' ? { name: 'home' } : current))
    })
    return () => setSessionLostHandler(null)
  }, [forgetAccountState])

  // The list depends on who is signed in, so it is reloaded whenever the account changes.
  const listKey = auth.status === 'loading' ? null : String(userId ?? 'visitor')
  const loadIncidents = useCallback(() => {
    if (listKey === null) return
    api.incidents()
      .then((items) => setList({ key: listKey, items, error: null }))
      .catch((err) => setList((current) => ({ key: listKey, items: current.items, error: err.message })))
  }, [listKey])

  useEffect(() => { loadIncidents() }, [loadIncidents])
  const listLoading = list.key !== listKey
  const incidents = listLoading ? [] : list.items
  const listError = listLoading ? null : list.error

  // Protected pages: visitors are sent to sign in, then brought back.
  useEffect(() => {
    if (auth.status === 'anonymous' && requiresAccount(route)) navigate(loginRedirect(route), { replace: true })
    if (signedIn && (route.name === 'login' || route.name === 'register')) navigate(route.next, { replace: true })
  }, [auth.status, signedIn, route])

  const defaultSlug = incidents.find((i) => i.kind === 'user')?.slug ?? incidents.find((i) => i.kind === 'demo')?.slug
    ?? null
  const selected = route.name === 'incident' ? route.slug : route.name === 'home' ? defaultSlug : null
  const creating = route.name === 'new' && signedIn

  useEffect(() => {
    if (!selected || auth.status === 'loading') return undefined
    let cancelled = false
    api.incident(selected)
      .then((data) => { if (!cancelled) setLoaded({ slug: selected, version: detailVersion, user: userId, data, error: null }) })
      .catch((err) => {
        if (!cancelled) setLoaded({ slug: selected, version: detailVersion, user: userId, data: null, error: err })
      })
    return () => { cancelled = true }
  }, [selected, detailVersion, auth.status, userId])

  function handleSignedIn(session) {
    setCsrfToken(session.csrf_token)
    setLoaded(null)
    setAuthNotice(null)
    setAuth({ status: 'signed-in', user: session.user, busy: false })
    navigate(route.next ?? '#/', { replace: true })
  }

  async function handleLogout() {
    setAuth((current) => ({ ...current, busy: true }))
    try {
      await api.logout()
    } catch {
      // Even if the request fails, forget the session locally; the cookie expires on its own.
    }
    forgetAccountState()
    setAuth({ status: 'anonymous', user: null, busy: false })
    navigate('#/')
  }

  function handleCreated(created) {
    setSummaries((current) => ({ ...current, [created.slug]: created.evidence }))
    setDraft(EMPTY_INCIDENT)
    navigate(routeHash({ name: 'incident', slug: created.slug }))
    loadIncidents()
  }

  function handleEvidenceAdded(slug, summary) {
    setSummaries((current) => ({ ...current, [slug]: summary }))
    setDetailVersion((v) => v + 1)
  }

  async function handleDelete(incident) {
    if (!window.confirm(`Delete "${incident.title}" with all its evidence and investigations? This cannot be undone.`)) return
    setDeleting(true)
    try {
      await api.deleteIncident(incident.slug)
      setSummaries(({ [incident.slug]: _removed, ...rest }) => rest)
      navigate('#/')
      loadIncidents()
    } catch (err) {
      window.alert(err.message)
    } finally {
      setDeleting(false)
    }
  }

  const current = loaded?.slug === selected && loaded?.user === userId ? loaded : null
  const detailTone = runTone(incidents.find((incident) => incident.slug === selected)?.last_run_status)
  const detail = current?.data
  const detailError = current?.error
  const status = detail ? evidenceStatus(detail.evidence) : null
  const isUser = detail?.kind === 'user'
  const registrationEnabled = Boolean(config?.registration_enabled)
  const authPage = route.name === 'login' || route.name === 'register'
  const loginHref = routeHash({ name: 'login', next: routeHash(route.name === 'home' ? { name: 'new' } : route) })
  const registerHref = registrationEnabled
    ? routeHash({ name: 'register', next: routeHash(route.name === 'home' ? { name: 'new' } : route) })
    : null

  return (
    <div className="layout">
      <header className="topbar">
        <a className="brand" href="#/">
          <LensMark />
          <div>
            <h1>IncidentLens</h1>
            <p className="subtitle">AI incident triage · read-only investigation · evidence-backed answers</p>
          </div>
        </a>
        <div className="topbar-meta">
          {config && !config.public_demo_mode && <span className="env-chip">Local workspace</span>}
          {config?.model && <span className="model-chip" title="Language model">{config.model}</span>}
          <UserMenu auth={auth} registrationEnabled={registrationEnabled} onLogout={handleLogout} route={route}
            theme={theme} onToggleTheme={toggleTheme} />
        </div>
      </header>

      <aside className="sidebar">
        {signedIn && (
          <p className="sidebar-account small">Signed in as <strong>{auth.user.email}</strong></p>
        )}
        <IncidentList incidents={incidents} selected={creating || authPage ? null : selected}
          onSelect={(slug) => navigate(routeHash({ name: 'incident', slug }))}
          onNew={() => navigate('#/new')} creating={creating} error={listError} loading={listLoading}
          signedIn={signedIn} signInHref={loginHref} registerHref={registerHref} />
        <p className="sidebar-note small">
          The agent only reads evidence for the selected incident and never changes systems; every
          recommendation needs a human decision.
        </p>
      </aside>

      <main className="content">
        {authPage && auth.status === 'anonymous' && (
          <AuthPage key={route.name} mode={route.name} next={route.next} registrationEnabled={registrationEnabled}
            notice={authNotice} onSignedIn={handleSignedIn} />
        )}

        {(auth.status === 'loading' || (route.name === 'new' && !signedIn)) && !authPage && (
          <LoadingCard />
        )}

        {creating && (
          <NewIncidentForm draft={draft} onDraftChange={setDraft} onCreated={handleCreated}
            onCancel={() => navigate('#/')} />
        )}

        {route.name === 'home' && !listLoading && !listError && incidents.length > 0 && (
          <Overview incidents={incidents} signedIn={signedIn} />
        )}

        {selected && auth.status !== 'loading' && (
          <>
            {detailError && (
              <div className="error-box state-box" role="alert">
                <strong className="state-title">
                  {detailError.status === 404 ? 'Incident not available' : 'Could not load this incident'}
                </strong>
                {detailError.status === 404 ? (
                  <>
                    <p>This incident does not exist, or it belongs to another account.</p>
                    {!signedIn && <p className="small">If it is yours, <a href={loginHref}>sign in</a> to open it.</p>}
                  </>
                ) : <p>{detailError.message}</p>}
              </div>
            )}
            {!detail && !detailError && <LoadingCard label="Loading incident…" lines={4} />}
            {detail && (
              <>
                <section className={`card alert-card kind-${isUser ? 'user' : 'demo'} tone-${detailTone.tone}`}>
                  <div className="alert-head">
                    <span className={`tag tag-${isUser ? 'user' : 'demo'}`}>{isUser ? 'Your incident · private' : 'Demo · simulated'}</span>
                    <span className={`status-badge tone-${detailTone.tone}`}>
                      <span className="status-dot" aria-hidden="true" />{detailTone.label}
                    </span>
                    <span className="muted small">Incident start {formatTimestamp(detail.incident_start)}</span>
                    {isUser && (
                      <button type="button" className="link-button danger" onClick={() => handleDelete(detail)}
                        disabled={deleting}>
                        {deleting ? 'Deleting…' : 'Delete incident'}
                      </button>
                    )}
                  </div>
                  <h2>{detail.title}</h2>
                  <p className="alert-text">{detail.alert_summary}</p>
                  {detail.evidence && (
                    <div className="evidence-stats" aria-label="Evidence status">
                      <div className="stat-tile tone-log">
                        <strong>{detail.evidence.logs.toLocaleString('en-US')}</strong>
                        <span>{pluralNoun(detail.evidence.logs, 'log entry', 'log entries')}</span>
                      </div>
                      <div className="stat-tile tone-metric">
                        <strong>{detail.evidence.metric_points.toLocaleString('en-US')}</strong>
                        <span>{pluralNoun(detail.evidence.metric_points, 'metric point')}</span>
                      </div>
                      <div className="stat-tile tone-deploy">
                        <strong>{detail.evidence.deployments.toLocaleString('en-US')}</strong>
                        <span>{pluralNoun(detail.evidence.deployments, 'deployment')}</span>
                      </div>
                      {detail.services?.length > 0 && (
                        <div className="services" aria-label="Services">
                          {detail.services.map((s) => <span key={s} className="service">{s}</span>)}
                        </div>
                      )}
                    </div>
                  )}
                  {status && !status.ready && <p className="notice small">{status.message}</p>}
                  {isUser && (
                    <p className="muted small">Shown as supplied (credential-like values redacted). User-provided descriptions are treated as unverified.</p>
                  )}
                </section>

                <EvidenceSummary summary={summaries[detail.slug]}
                  onDismiss={() => setSummaries((s) => ({ ...s, [detail.slug]: null }))} />

                <Investigation key={detail.slug} slug={detail.slug} config={config} onRunFinished={loadIncidents}
                  blockedReason={status && !status.ready ? status.message : null}
                  evidenceUpdatedAt={detail.evidence?.updated_at} flagOutdated={isUser} />

                {isUser && signedIn && (
                  <AddEvidence key={`add-${detail.slug}`} slug={detail.slug} services={detail.services ?? []}
                    onAdded={(summary) => handleEvidenceAdded(detail.slug, summary)} />
                )}

                <Telemetry detail={detail} />
              </>
            )}
          </>
        )}

        {!selected && !creating && !authPage && route.name === 'home' && !listLoading && auth.status !== 'loading' && (
          <div className="empty state-box">
            <strong className="state-title">No incident selected</strong>
            <p>Choose an incident from the list to see its evidence and investigation.</p>
          </div>
        )}
      </main>
    </div>
  )
}

export default App
