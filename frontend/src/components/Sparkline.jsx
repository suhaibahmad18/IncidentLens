import { sparklinePoints } from '../format.js'

export default function Sparkline({ points, width = 160, height = 36, label }) {
  const values = (points ?? []).map((p) => p.value)
  const polyline = sparklinePoints(values, width, height)
  const zeroIndex = (points ?? []).findIndex((p) => p.minutes_from_incident >= 0)
  const markerX = zeroIndex > 0 ? 2 + (zeroIndex * (width - 4)) / Math.max(values.length - 1, 1) : null

  return (
    <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img"
      aria-label={label}>
      {markerX !== null && <line className="sparkline-marker" x1={markerX} x2={markerX} y1="0" y2={height} />}
      {polyline && <polyline className="sparkline-line" points={polyline} />}
    </svg>
  )
}
