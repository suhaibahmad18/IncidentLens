import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'

import { ApiError, api, describeError, setCsrfToken, setSessionLostHandler } from '../src/api.js'

const realFetch = globalThis.fetch
let calls = []

function respond(status, body) {
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options })
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => { if (body === undefined) throw new SyntaxError('no body'); return body },
    }
  }
}

afterEach(() => {
  globalThis.fetch = realFetch
  calls = []
  setCsrfToken(null)
  setSessionLostHandler(null)
})

test('the CSRF token is sent on state changes only, with same-origin cookies', async () => {
  setCsrfToken('token-123')
  respond(200, [])
  await api.incidents()
  respond(201, { slug: 'u-1' })
  await api.createIncident({ title: 't' })
  respond(204)
  await api.deleteIncident('u-1')

  assert.equal(calls[0].options.headers['X-CSRF-Token'], undefined)
  assert.equal(calls[1].options.headers['X-CSRF-Token'], 'token-123')
  assert.equal(calls[1].options.headers['Content-Type'], 'application/json')
  assert.equal(calls[2].options.method, 'DELETE')
  assert.equal(calls[2].options.headers['X-CSRF-Token'], 'token-123')
  assert.ok(calls.every((c) => c.options.credentials === 'same-origin'))
})

test('visitors are a normal state for /me, not an error', async () => {
  respond(401, { detail: 'login required' })
  assert.equal(await api.me(), null)
})

test('a rejected session is reported once and surfaces as an ApiError', async () => {
  let lost = 0
  setSessionLostHandler(() => { lost += 1 })
  setCsrfToken('stale')
  respond(401, { detail: 'login required' })

  await assert.rejects(api.createIncident({}), (err) => err instanceof ApiError && err.status === 401)
  assert.equal(lost, 1)

  respond(403, { detail: 'missing or invalid CSRF token' })
  await assert.rejects(api.addEvidence('u-1', {}))
  assert.equal(lost, 2)

  respond(401, { detail: 'invalid email or password' })  // a failed login is not a lost session
  await assert.rejects(api.login({ email: 'a@b.co', password: 'x' }))
  assert.equal(lost, 2)
})

test('without a session, 401s do not trigger the session-lost handler', async () => {
  let lost = 0
  setSessionLostHandler(() => { lost += 1 })
  respond(401, { detail: 'login required' })
  await assert.rejects(api.createIncident({}))
  assert.equal(lost, 0)
})

test('auth-related statuses have readable messages', () => {
  assert.match(describeError(401, null), /sign in/)
  assert.match(describeError(429, null), /Too many requests/)
  assert.equal(describeError(429, { detail: 'too many login attempts; try again in 15 minutes' }),
    'too many login attempts; try again in 15 minutes')
})
