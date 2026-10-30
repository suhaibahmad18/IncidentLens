import { useState } from 'react'

import { api } from '../api.js'
import {
  EMPTY_INCIDENT,
  LIMITS,
  fieldErrorsFromApi,
  hasEvidence,
  incidentPayload,
  validateIncident,
} from '../incidentForm.js'
import EvidenceFields from './EvidenceFields.jsx'

const ORDER = ['title', 'alert_summary', 'default_service', 'incident_start', 'logs', 'metrics_csv', 'deployments_csv']

// The draft lives in the parent, so navigating away and back never loses what was typed.
export default function NewIncidentForm({ draft, onDraftChange, onCreated, onCancel }) {
  const [errors, setErrors] = useState({})
  const [submitting, setSubmitting] = useState(false)

  function update(name, value) {
    onDraftChange({ ...draft, [name]: value })
    if (errors[name]) setErrors((current) => ({ ...current, [name]: null, _form: null }))
  }

  function focusFirstError(found) {
    const first = ORDER.find((name) => found[name])
    if (first) document.getElementById(`new-${first}`)?.focus()
  }

  async function submit(event) {
    event.preventDefault()
    const found = validateIncident(draft)
    if (Object.keys(found).length) {
      setErrors(found)
      focusFirstError(found)
      return
    }
    setSubmitting(true)
    setErrors({})
    try {
      const created = await api.createIncident(incidentPayload(draft))
      onCreated(created)
    } catch (err) {
      const fromApi = fieldErrorsFromApi(err.detail)
      const next = Object.keys(fromApi).length ? fromApi : { _form: err.message }
      if (!next._form && !ORDER.some((name) => next[name])) next._form = err.message
      setErrors(next)
      focusFirstError(next)
    } finally {
      setSubmitting(false)
    }
  }

  const dirty = Object.keys(EMPTY_INCIDENT).some((key) => draft[key] !== EMPTY_INCIDENT[key])

  return (
    <section className="card">
      <div className="card-header">
        <div>
          <p className="eyebrow">Your workspace · private to your account</p>
          <h2>New incident</h2>
        </div>
        <button type="button" className="link-button" onClick={onCancel}>Back to incidents</button>
      </div>
      <p className="muted">
        Describe what is going wrong and paste or load the evidence you have. The agent investigates only this
        incident&apos;s evidence, read-only, and never takes action. IncidentLens does not connect to monitoring
        systems: it analyses exactly what you supply here.
      </p>

      <form onSubmit={submit} noValidate className="incident-form">
        {errors._form && <p className="error-box" role="alert">{errors._form}</p>}

        <TextField id="new-title" label="Title" value={draft.title} error={errors.title} disabled={submitting}
          maxLength={LIMITS.titleMax} placeholder="Orders API failing" onChange={(v) => update('title', v)} required />

        <div className="field">
          <label htmlFor="new-alert_summary">Alert or symptom <span aria-hidden="true">*</span></label>
          <textarea id="new-alert_summary" className="prose" rows={3} value={draft.alert_summary} disabled={submitting}
            maxLength={LIMITS.alertMax} placeholder="Orders 5xx above 20% since 14:00; customers cannot check out"
            onChange={(event) => update('alert_summary', event.target.value)} required
            aria-invalid={errors.alert_summary ? 'true' : undefined}
            aria-describedby={errors.alert_summary ? 'new-alert_summary-error' : undefined} />
          {errors.alert_summary && <p id="new-alert_summary-error" className="error small" role="alert">{errors.alert_summary}</p>}
        </div>

        <div className="field-row">
          <TextField id="new-default_service" label="Main service" value={draft.default_service}
            error={errors.default_service} disabled={submitting} placeholder="orders-api" required
            hint="Lowercase letters, digits and dashes. Used for log lines that do not name a service."
            onChange={(v) => update('default_service', v.toLowerCase())} />
          <TextField id="new-incident_start" type="datetime-local" step="1" label="Incident start (optional)"
            value={draft.incident_start} error={errors.incident_start} disabled={submitting}
            hint="Your local time. Leave empty to use the first warning or error in the logs."
            onChange={(v) => update('incident_start', v)} />
        </div>

        <EvidenceFields idPrefix="new" values={draft} errors={errors} onChange={update} disabled={submitting} />

        {!hasEvidence(draft) && (
          <p className="notice small">
            You can create the incident without evidence and add it later, but it cannot be investigated until it has some.
          </p>
        )}

        <div className="form-actions">
          <button type="submit" className="primary" disabled={submitting}>
            {submitting ? 'Creating…' : 'Create incident'}
          </button>
          <button type="button" className="secondary" disabled={submitting || !dirty}
            onClick={() => { if (window.confirm('Clear everything in this form?')) { onDraftChange(EMPTY_INCIDENT); setErrors({}) } }}>
            Clear form
          </button>
        </div>
      </form>
    </section>
  )
}

function TextField({ id, label, value, onChange, error, hint, required, ...rest }) {
  const describedBy = [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(' ') || undefined
  return (
    <div className="field">
      <label htmlFor={id}>{label}{required && <span aria-hidden="true"> *</span>}</label>
      <input id={id} value={value} onChange={(event) => onChange(event.target.value)} required={required}
        aria-invalid={error ? 'true' : undefined} aria-describedby={describedBy} {...rest} />
      {hint && <span id={`${id}-hint`} className="muted small">{hint}</span>}
      {error && <p id={`${id}-error`} className="error small" role="alert">{error}</p>}
    </div>
  )
}
