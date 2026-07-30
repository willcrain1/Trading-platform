interface Props {
  values: number[]
  width?: number
  height?: number
}

export default function Sparkline({ values, width = 90, height = 26 }: Props) {
  if (values.length < 2) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width
      const y = height - ((v - min) / span) * (height - 4) - 2
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
  const up = values[values.length - 1] >= values[0]
  return (
    <svg width={width} height={height}>
      <polyline
        points={pts}
        fill="none"
        stroke={up ? '#3fb950' : '#f85149'}
        strokeWidth="1.5"
      />
    </svg>
  )
}
