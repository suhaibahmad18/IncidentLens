// Placeholder shaped like the content that is loading (static when reduced motion is preferred).
export default function LoadingCard({ label = 'Loading…', lines = 3 }) {
  return (
    <div className="card skeleton" aria-busy="true" aria-live="polite">
      <span className="visually-hidden">{label}</span>
      <span className="skeleton-line skeleton-title" aria-hidden="true" />
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton-line" aria-hidden="true" />
      ))}
    </div>
  )
}
