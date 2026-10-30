// Routing and account-form logic, kept free of React so it can be unit tested.

const SLUG_PATTERN = /^[a-z0-9-]{1,64}$/
const EMAIL_PATTERN = /^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$/

export const PASSWORD_MIN = 12
export const PASSWORD_MAX = 128
// Pages that need an account. Incident pages do not: demos are public, and the server answers
// "not found" for anyone else's incident.
const PROTECTED = new Set(['new'])

// '#/', '#/incident/<slug>', '#/new', '#/login?next=…', '#/register?next=…'. Anything else is home.
export function parseRoute(hash) {
  const raw = (hash ?? '').replace(/^#/, '')
  const [path, query = ''] = raw.split('?')
  const parts = path.split('/').filter(Boolean)
  const params = new URLSearchParams(query)

  if (parts[0] === 'incident' && parts.length === 2 && SLUG_PATTERN.test(parts[1])) {
    return { name: 'incident', slug: parts[1] }
  }
  if (parts.length === 1 && parts[0] === 'new') return { name: 'new' }
  if (parts.length === 1 && (parts[0] === 'login' || parts[0] === 'register')) {
    return { name: parts[0], next: safeNext(params.get('next')) }
  }
  return { name: 'home' }
}

export function routeHash(route) {
  switch (route.name) {
    case 'incident': return `#/incident/${route.slug}`
    case 'new': return '#/new'
    case 'login':
    case 'register': {
      const next = safeNext(route.next)
      return next === '#/' ? `#/${route.name}` : `#/${route.name}?next=${encodeURIComponent(next)}`
    }
    default: return '#/'
  }
}

// Only ever return to a page of this app (never an external URL or another login page).
export function safeNext(next) {
  if (typeof next !== 'string' || !next.startsWith('#/')) return '#/'
  const route = parseRoute(next)
  if (route.name === 'login' || route.name === 'register') return '#/'
  return route.name === 'home' ? '#/' : routeHash(route)
}

export function requiresAccount(route) {
  return PROTECTED.has(route.name)
}

export function loginRedirect(route) {
  return routeHash({ name: 'login', next: routeHash(route) })
}

export function validateCredentials(values, mode) {
  const errors = {}
  const email = values.email.trim()
  if (!email) errors.email = 'Enter your email address.'
  else if (!EMAIL_PATTERN.test(email.toLowerCase())) errors.email = 'Enter a valid email address.'

  if (!values.password) errors.password = 'Enter your password.'
  else if (values.password.length > PASSWORD_MAX) errors.password = `Use at most ${PASSWORD_MAX} characters.`
  else if (mode === 'register') {
    if (values.password.length < PASSWORD_MIN) errors.password = `Use at least ${PASSWORD_MIN} characters.`
    else if (new Set(values.password).size < 4) errors.password = 'Use a less repetitive password.'
    else if (values.password.toLowerCase() === email.toLowerCase()) errors.password = 'The password must not be your email.'
  }
  if (mode === 'register' && !errors.password && values.confirm !== values.password) {
    errors.confirm = 'The passwords do not match.'
  }
  return errors
}

export function credentialsPayload(values) {
  return { email: values.email.trim().toLowerCase(), password: values.password }
}

// The API reports field problems as [{loc: ['body', 'email'], msg}] and others as a string.
export function authErrorsFromApi(detail, fallback) {
  const errors = {}
  if (Array.isArray(detail)) {
    for (const problem of detail) {
      const field = (problem.loc ?? []).find((part) => part === 'email' || part === 'password') ?? '_form'
      errors[field] = errors[field] ?? (problem.msg ?? 'Invalid value.').replace(/^Value error, /, '')
    }
  } else if (typeof detail === 'string' && /password/.test(detail) && !/invalid email or password/.test(detail)) {
    errors.password = detail
  }
  if (!Object.keys(errors).length) errors._form = fallback
  return errors
}
