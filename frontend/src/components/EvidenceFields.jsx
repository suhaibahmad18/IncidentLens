import { useState } from 'react'

import { EVIDENCE_FIELDS, LIMITS, readFileAsText } from '../incidentForm.js'

export default function EvidenceFields({ idPrefix, values, errors, onChange, disabled }) {
  const [fileErrors, setFileErrors] = useState({})

  async function loadFile(field, event) {
    const input = event.target
    const file = input.files?.[0]
    input.value = '' // allow re-selecting the same file
    if (!file) return
    if (values[field.name]?.trim() && !window.confirm(`Replace the current ${field.label.toLowerCase()} with "${file.name}"?`)) {
      return
    }
    try {
      const text = await readFileAsText(file, LIMITS[field.name])
      setFileErrors((current) => ({ ...current, [field.name]: null }))
      onChange(field.name, text)
    } catch (err) {
      setFileErrors((current) => ({ ...current, [field.name]: err.message }))
    }
  }

  return (
    <fieldset className="evidence-fields" disabled={disabled}>
      <legend>Evidence</legend>
      <p className="muted small">
        Recognisable credentials (passwords, tokens, keys) are redacted before storage, but please remove secrets
        yourself before pasting.
      </p>
      {EVIDENCE_FIELDS.map((field) => {
        const id = `${idPrefix}-${field.name}`
        const value = values[field.name] ?? ''
        const error = errors[field.name] ?? fileErrors[field.name]
        return (
          <div className="field" key={field.name}>
            <div className="field-label-row">
              <label htmlFor={id}>{field.label}</label>
              <label className="file-button">
                Load file…
                <input type="file" accept={field.accept} className="visually-hidden"
                  onChange={(event) => loadFile(field, event)} aria-label={`Load ${field.label} from a file`} />
              </label>
            </div>
            <textarea id={id} value={value} rows={field.name === 'logs' ? 8 : 4} spellCheck={false}
              placeholder={field.placeholder} onChange={(event) => onChange(field.name, event.target.value)}
              aria-invalid={error ? 'true' : undefined} aria-describedby={`${id}-hint${error ? ` ${id}-error` : ''}`} />
            <div className="field-meta">
              <span id={`${id}-hint`} className="muted small">{field.hint}</span>
              <span className={value.length > LIMITS[field.name] ? 'error small' : 'muted small'}>
                {value.length.toLocaleString('en-US')} / {LIMITS[field.name].toLocaleString('en-US')}
              </span>
            </div>
            {error && <p id={`${id}-error`} className="error small" role="alert">{error}</p>}
          </div>
        )
      })}
    </fieldset>
  )
}
