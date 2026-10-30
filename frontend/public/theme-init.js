// Runs before the page renders (a plain, blocking script in <head>), so the first paint already
// uses the right theme. Same rules as src/theme.js: a saved choice wins, otherwise the OS setting.
// It is a separate file, not inline, because the Content-Security-Policy only allows 'self' scripts.
(function () {
  var theme = null
  try {
    theme = window.localStorage.getItem('incidentlens-theme')
  } catch {
    theme = null
  }
  if (theme !== 'light' && theme !== 'dark') {
    theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  document.documentElement.setAttribute('data-theme', theme)
  document.documentElement.style.colorScheme = theme
})()
