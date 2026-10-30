import { useState } from 'react'

import { api } from '../api.js'
import { PASSWORD_MIN, authErrorsFromApi, credentialsPayload, routeHash, validateCredentials } from '../auth.js'

const COPY = {
  login: { title: 'Sign in', submit: 'Sign in', busy: 'Signing in…' },
  register: { title: 'Create your account', submit: 'Create account', busy: 'Creating account…' },
}

// One page for both modes; `next` is where to go afterwards (already sanitised by the router).
export default function AuthPage({ mode, next, registrationEnabled, notice, onSignedIn }) {
  const [values, setValues] = useState({ email: '', password: '', confirm: '' })
  const [errors, setErrors] = useState({})
  const [submitting, setSubmitting] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const copy = COPY[mode]
  const registering = mode === 'register'
  const other = registering ? 'login' : 'register'

  function update(name, value) {
    setValues((current) => ({ ...current, [name]: value }))
    if (errors[name] || errors._form) setErrors((current) => ({ ...current, [name]: null, _form: null }))
  }

  async function submit(event) {
    event.preventDefault()
    const found = validateCredentials(values, mode)
    if (Object.keys(found).length) {
      setErrors(found)
      document.getElementById(`auth-${['email', 'password', 'confirm'].find((f) => found[f])}`)?.focus()
      return
    }
    setSubmitting(true)
    setErrors({})
    try {
      const session = await (registering ? api.register : api.login)(credentialsPayload(values))
      setValues({ email: '', password: '', confirm: '' })
      onSignedIn(session)
    } catch (err) {
      setErrors(authErrorsFromApi(err.detail, err.message))
      setValues((current) => ({ ...current, password: '', confirm: '' }))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="card auth-card" aria-labelledby="auth-title">
      <p className="eyebrow">Your workspace</p>
      <h2 id="auth-title">{copy.title}</h2>
      <p className="muted">
        {registering
          ? 'An account keeps the incidents you create private to you. Demo incidents stay open to everyone.'
          : 'Sign in to create incidents, add evidence and investigate them privately.'}
      </p>

      {notice && <p className="notice small" role="status">{notice}</p>}
      {registering && !registrationEnabled ? (
        <p className="error-box" role="alert">Registration is closed on this deployment.</p>
      ) : (
        <form onSubmit={submit} noValidate className="incident-form auth-form">
          {errors._form && <p className="error-box" role="alert">{errors._form}</p>}

          <Field id="auth-email" label="Email" error={errors.email}>
            <input id="auth-email" type="email" autoComplete="email" inputMode="email" value={values.email}
              disabled={submitting} maxLength={254} required onChange={(e) => update('email', e.target.value)}
              aria-invalid={errors.email ? 'true' : undefined}
              aria-describedby={errors.email ? 'auth-email-error' : undefined} />
          </Field>

          <Field id="auth-password" label="Password" error={errors.password}
            hint={registering ? `At least ${PASSWORD_MIN} characters. A few unrelated words work well.` : null}>
            <input id="auth-password" type={showPassword ? 'text' : 'password'}
              autoComplete={registering ? 'new-password' : 'current-password'} value={values.password}
              disabled={submitting} maxLength={128} required onChange={(e) => update('password', e.target.value)}
              aria-invalid={errors.password ? 'true' : undefined}
              aria-describedby={[registering && 'auth-password-hint', errors.password && 'auth-password-error']
                .filter(Boolean).join(' ') || undefined} />
          </Field>

          {registering && (
            <Field id="auth-confirm" label="Confirm password" error={errors.confirm}>
              <input id="auth-confirm" type={showPassword ? 'text' : 'password'} autoComplete="new-password"
                value={values.confirm} disabled={submitting} maxLength={128} required
                onChange={(e) => update('confirm', e.target.value)}
                aria-invalid={errors.confirm ? 'true' : undefined}
                aria-describedby={errors.confirm ? 'auth-confirm-error' : undefined} />
            </Field>
          )}

          <label className="checkbox small">
            <input type="checkbox" checked={showPassword} onChange={(e) => setShowPassword(e.target.checked)} />
            Show password
          </label>

          <div className="form-actions">
            <button type="submit" className="primary" disabled={submitting}>
              {submitting ? copy.busy : copy.submit}
            </button>
          </div>
        </form>
      )}

      <p className="muted small auth-switch">
        {registering ? 'Already have an account? ' : 'New here? '}
        {(registering || registrationEnabled) && (
          <a href={routeHash({ name: other, next })}>{registering ? 'Sign in' : 'Create an account'}</a>
        )}
        {!registering && !registrationEnabled && 'Registration is closed on this deployment.'}
        {' · '}<a href="#/">Browse the demo incidents</a>
      </p>
    </section>
  )
}

function Field({ id, label, hint, error, children }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint && <span id={`${id}-hint`} className="muted small">{hint}</span>}
      {error && <p id={`${id}-error`} className="error small" role="alert">{error}</p>}
    </div>
  )
}
