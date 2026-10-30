import { useId } from 'react'

import { sparklineGeometry } from '../format.js'

// Telemetry sparkline: soft area fill, dashed baseline (when one exists), incident-start marker
// and a dot at the first deviation. Purely presentational; values come from the API.
export default function Sparkline({ points, baseline = null, deviationMinutes = null, width = 180, height = 44, label }) {
  // useId() contains characters (":" or "«»") that are unreliable inside url(#...) references.
  const gradientId = `spark-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  const geometry = sparklineGeometry(points, { width, height, baseline, deviationMinutes })

  return (
    <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img"
      aria-label={label} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" className="sparkline-fill-top" />
          <stop offset="100%" className="sparkline-fill-bottom" />
        </linearGradient>
      </defs>
      {geometry && (
        <>
          <path d={geometry.area} fill={`url(#${gradientId})`} />
          {geometry.baselineY !== null && (
            <line className="sparkline-baseline" x1="0" x2={width} y1={geometry.baselineY} y2={geometry.baselineY} />
          )}
          {geometry.markerX !== null && (
            <line className="sparkline-marker" x1={geometry.markerX} x2={geometry.markerX} y1="0" y2={height} />
          )}
          <polyline className="sparkline-line" points={geometry.line} />
          {geometry.deviation && (
            <circle className="sparkline-deviation" cx={geometry.deviation.x} cy={geometry.deviation.y} r="3" />
          )}
        </>
      )}
    </svg>
  )
}
