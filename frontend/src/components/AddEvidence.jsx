import { useState } from 'react'

import { api } from '../api.js'
import { EMPTY_EVIDENCE, evidencePayload, fieldErrorsFromApi, validateEvidenceOnly } from '../incidentForm.js'
import EvidenceFields from './EvidenceFields.jsx'

// The service field starts EMPTY on purpose: lines without "service=" are attributed to it, so
// it must be the user's explicit choice. Existing services are offered as suggestions only.
export default function AddEvidence({ slug, services = [], onAdded }) {
  const [values, setValues] = useState({ ...EMPTY_EVIDENCE, default_service: '' })
  const [errors, setErrors] = useState({})
  const [submitting, setSubmitting] = useState(false)
  const serviceId = `add-${slug}-service`
  const listId = `${serviceId}-suggestions`

  function update(name, value) {
    setValues((current) => ({ ...current, [name]: value }))
    if (errors[name]) setErrors((current) => ({ ...current, [name]: null, _form: null }))
  }

  async function submit(event) {
    event.preventDefault()
    const found = validateEvidenceOnly(values)
    if (Object.keys(found).length) {
      setErrors(found)
      return
    }
    setSubmitting(true)
    setErrors({})
    try {
      const result = await api.addEvidence(slug, evidencePayload(values))
      setValues({ ...EMPTY_EVIDENCE, default_service: values.default_service }) // only cleared once saved
      onAdded(result.evidence)
    } catch (err) {
      const fromApi = fieldErrorsFromApi(err.detail)
      setErrors(Object.keys(fromApi).length ? fromApi : { _form: err.message })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <details className="card add-evidence">
      <summary>
        Add more evidence <span className="muted small">paste or load additional logs, metrics or deployments</span>
      </summary>
      <form onSubmit={submit} noValidate className="incident-form">
        {errors._form && <p className="error-box" role="alert">{errors._form}</p>}
        <div className="field">
          <label htmlFor={serviceId}>Service for lines without a service name <span aria-hidden="true">*</span></label>
          <input id={serviceId} list={listId} value={values.default_service} disabled={submitting}
            placeholder={services[0] ? `e.g. ${services[0]}` : 'e.g. orders-api'} autoComplete="off"
            onChange={(event) => update('default_service', event.target.value.toLowerCase())}
            aria-invalid={errors.default_service ? 'true' : undefined}
            aria-describedby={`${serviceId}-hint${errors.default_service ? ` ${serviceId}-error` : ''}`} />
          <datalist id={listId}>
            {services.map((name) => <option key={name} value={name} />)}
          </datalist>
          <span id={`${serviceId}-hint`} className="muted small">
            Choose deliberately: log lines that do not say <code>service=…</code> will be attributed to this service.
            {services.length > 0 && ` Existing services: ${services.join(', ')}.`}
          </span>
          {errors.default_service && (
            <p id={`${serviceId}-error`} className="error small" role="alert">{errors.default_service}</p>
          )}
        </div>
        <EvidenceFields idPrefix={`add-${slug}`} values={values} errors={errors} onChange={update} disabled={submitting} />
        <div className="form-actions">
          <button type="submit" className="primary" disabled={submitting}>
            {submitting && <span className="spinner" aria-hidden="true" />}
            {submitting ? 'Adding…' : 'Add evidence'}
          </button>
        </div>
      </form>
    </details>
  )
}
