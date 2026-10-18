/** Tiny single-series trend line. One hue, 2px line, end marker, and a hover
 *  tooltip per point (wide invisible hit targets carrying a native <title>). */
interface Props {
  values: number[]
  labels?: string[]          // per-point tooltip text (e.g. date + value)
  width?: number
  height?: number
  ariaLabel: string
}

export function Sparkline({ values, labels, width = 132, height = 30, ariaLabel }: Props) {
  if (values.length < 2) {
    return <span className="adm-spark-empty">{values.length === 1 ? 'first run — no trend yet' : 'no history'}</span>
  }
  const pad = 5
  const min = Math.min(...values), max = Math.max(...values)
  const span = max - min || 1
  const x = (i: number) => pad + (i * (width - 2 * pad)) / (values.length - 1)
  // Flat series sit mid-height instead of hugging an edge.
  const y = (v: number) => max === min ? height / 2 : height - pad - ((v - min) / span) * (height - 2 * pad)
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const last = values.length - 1
  const hitW = (width - 2 * pad) / (values.length - 1)
  return (
    <svg className="adm-spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
      <path d={d} fill="none" stroke="var(--indigo-light)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(last)} cy={y(values[last])} r={4} fill="var(--indigo-light)" stroke="var(--surface)" strokeWidth={2} />
      {values.map((v, i) => (
        <rect key={i} x={x(i) - hitW / 2} y={0} width={hitW} height={height} fill="transparent">
          <title>{labels?.[i] ?? String(v)}</title>
        </rect>
      ))}
    </svg>
  )
}
