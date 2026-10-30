import { toggleLabel } from '../theme.js'

// Shows the theme you would switch TO: a moon in light mode, a sun in dark mode.
export default function ThemeToggle({ theme, onToggle }) {
  const label = toggleLabel(theme)
  return (
    <button type="button" className="icon-button theme-toggle" onClick={onToggle} aria-label={label} title={label}>
      {theme === 'dark' ? (
        <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" focusable="false">
          <circle cx="10" cy="10" r="3.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="M10 1.8v2.1M10 16.1v2.1M1.8 10h2.1M16.1 10h2.1M4.2 4.2l1.5 1.5M14.3 14.3l1.5 1.5M4.2 15.8l1.5-1.5M14.3 5.7l1.5-1.5"
            stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      ) : (
        <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" focusable="false">
          <path d="M16.5 12.3A7 7 0 0 1 7.7 3.5a7 7 0 1 0 8.8 8.8Z" fill="none" stroke="currentColor" strokeWidth="1.6"
            strokeLinejoin="round" />
        </svg>
      )}
    </button>
  )
}
