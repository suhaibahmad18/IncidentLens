// Light/dark theme: the choice lives on <html data-theme="…"> and drives the CSS variables.
// public/theme-init.js applies the same rules before first paint (no flash of the wrong theme);
// keep the two in sync.

export const THEME_STORAGE_KEY = 'incidentlens-theme'
export const THEMES = ['light', 'dark']

export function isTheme(value) {
  return THEMES.includes(value)
}

// A saved explicit choice wins; otherwise follow the operating system.
export function resolveTheme(saved, prefersDark) {
  return isTheme(saved) ? saved : prefersDark ? 'dark' : 'light'
}

export function otherTheme(theme) {
  return theme === 'dark' ? 'light' : 'dark'
}

// Storage can be unavailable (private mode, blocked cookies); the theme then just isn't remembered.
export function readSavedTheme(storage) {
  try {
    const value = storage?.getItem(THEME_STORAGE_KEY)
    return isTheme(value) ? value : null
  } catch {
    return null
  }
}

export function saveTheme(storage, theme) {
  try {
    storage?.setItem(THEME_STORAGE_KEY, theme)
    return true
  } catch {
    return false
  }
}

export function applyTheme(root, theme) {
  root.dataset.theme = theme
  root.style.colorScheme = theme
}

export function toggleLabel(theme) {
  return theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'
}
