import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  authErrorsFromApi,
  credentialsPayload,
  loginRedirect,
  parseRoute,
  requiresAccount,
  routeHash,
  safeNext,
  validateCredentials,
} from '../src/auth.js'

test('parses every route and falls back to home', () => {
  assert.deepEqual(parseRoute(''), { name: 'home' })
  assert.deepEqual(parseRoute('#/'), { name: 'home' })
  assert.deepEqual(parseRoute('#/incident/u-0123abcd'), { name: 'incident', slug: 'u-0123abcd' })
  assert.deepEqual(parseRoute('#/new'), { name: 'new' })
  assert.deepEqual(parseRoute('#/login'), { name: 'login', next: '#/' })
  assert.deepEqual(parseRoute('#/register?next=%23%2Fnew'), { name: 'register', next: '#/new' })
  for (const junk of ['#/incident/Bad_Slug!', '#/incident', '#/incident/a/b', '#/admin', '#/new/x', 'javascript:alert(1)']) {
    assert.deepEqual(parseRoute(junk), { name: 'home' }, junk)
  }
})

test('route hashes round-trip', () => {
  for (const hash of ['#/', '#/new', '#/incident/checkout-db-pool-exhaustion', '#/login', '#/register?next=%23%2Fnew']) {
    assert.equal(routeHash(parseRoute(hash)), hash)
  }
})

test('the next parameter can only point inside the app', () => {
  assert.equal(safeNext('#/new'), '#/new')
  assert.equal(safeNext('#/incident/u-1'), '#/incident/u-1')
  for (const hostile of ['https://evil.example', '//evil.example', 'javascript:alert(1)', '#/login?next=%23%2Fnew',
    '#/register', null, undefined, 42]) {
    assert.equal(safeNext(hostile), '#/', String(hostile))
  }
  assert.deepEqual(parseRoute('#/login?next=https%3A%2F%2Fevil.example'), { name: 'login', next: '#/' })
})

test('only account pages require signing in, and redirect back after login', () => {
  assert.equal(requiresAccount({ name: 'new' }), true)
  assert.equal(requiresAccount({ name: 'incident', slug: 'x' }), false)
  assert.equal(requiresAccount({ name: 'home' }), false)
  assert.equal(loginRedirect({ name: 'new' }), '#/login?next=%23%2Fnew')
  assert.deepEqual(parseRoute(loginRedirect({ name: 'new' })), { name: 'login', next: '#/new' })
})

test('login validation needs an email and a password', () => {
  assert.deepEqual(validateCredentials({ email: '', password: '' }, 'login'), {
    email: 'Enter your email address.', password: 'Enter your password.' })
  assert.deepEqual(validateCredentials({ email: 'nope', password: 'x' }, 'login'), { email: 'Enter a valid email address.' })
  assert.deepEqual(validateCredentials({ email: ' A@Example.com ', password: 'short' }, 'login'), {})
})

test('registration validation mirrors the server policy', () => {
  const base = { email: 'a@example.com', confirm: '' }
  assert.match(validateCredentials({ ...base, password: 'short' }, 'register').password, /at least 12/)
  assert.match(validateCredentials({ ...base, password: 'aaaaaaaaaaaab' }, 'register').password, /less repetitive/)
  assert.match(validateCredentials({ ...base, password: 'x'.repeat(129) }, 'register').password, /at most 128/)
  assert.match(validateCredentials({ email: 'abcdefghij@ex.com', password: 'ABCDEFGHIJ@EX.COM' }, 'register').password,
    /not be your email/)
  assert.deepEqual(validateCredentials({ ...base, password: 'correct horse battery', confirm: 'different' }, 'register'),
    { confirm: 'The passwords do not match.' })
  assert.deepEqual(validateCredentials({ ...base, password: 'correct horse battery', confirm: 'correct horse battery' },
    'register'), {})
})

test('payload normalizes the email but never the password', () => {
  assert.deepEqual(credentialsPayload({ email: '  Ada@Example.COM ', password: ' Pass Word ' }),
    { email: 'ada@example.com', password: ' Pass Word ' })
})

test('server errors map to fields without leaking which accounts exist', () => {
  assert.deepEqual(authErrorsFromApi([{ loc: ['body', 'password'], msg: 'Value error, use at least 12 characters' }], 'x'),
    { password: 'use at least 12 characters' })
  assert.deepEqual(authErrorsFromApi('invalid email or password', 'invalid email or password'),
    { _form: 'invalid email or password' })
  assert.deepEqual(authErrorsFromApi('the password must not be your email', 'f'),
    { password: 'the password must not be your email' })
  assert.deepEqual(authErrorsFromApi(null, 'Cannot reach the API'), { _form: 'Cannot reach the API' })
})
