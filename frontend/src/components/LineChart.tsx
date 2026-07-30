import { useEffect, useRef } from 'react'
import {
  createChart,
  LineSeries,
  type UTCTimestamp,
  LineStyle,
} from 'lightweight-charts'
import type { Point } from '../api'

export interface Line {
  points: Point[]
  color: string
  title?: string
  lineWidth?: number
}

interface Props {
  lines: Line[]
  height?: number
  /** horizontal guide lines, e.g. z-score bands at +/-2 */
  guides?: { value: number; color: string; title?: string }[]
}

export default function LineChart({ lines, height = 260, guides = [] }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el || lines.length === 0) return
    const chart = createChart(el, {
      layout: { background: { color: '#161b22' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale: { borderColor: '#30363d' },
      height,
      width: el.clientWidth,
    })

    lines.forEach((ln, i) => {
      const s = chart.addSeries(LineSeries, {
        color: ln.color,
        lineWidth: (ln.lineWidth ?? 2) as 1 | 2 | 3 | 4,
        priceLineVisible: false,
        title: ln.title ?? '',
      })
      s.setData(ln.points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })))
      if (i === 0) {
        for (const g of guides) {
          s.createPriceLine({
            price: g.value,
            color: g.color,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: g.title ?? '',
          })
        }
      }
    })

    chart.timeScale().fitContent()
    const onResize = () => chart.applyOptions({ width: el.clientWidth })
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.remove()
    }
  }, [lines, height, guides])

  return <div ref={containerRef} />
}
