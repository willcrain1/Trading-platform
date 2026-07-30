import { useEffect, useRef } from 'react'
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
  type IChartApi,
  type UTCTimestamp,
  LineStyle,
} from 'lightweight-charts'
import type { Candle, Point, Level } from '../api'

export interface Overlay {
  points: Point[]
  color: string
  title?: string
  lineWidth?: number
  dashed?: boolean
}

interface Props {
  candles: Candle[]
  overlays?: Overlay[]
  levels?: Level[]
  priceLines?: { price: number; color: string; title: string }[]
  height?: number
  showVolume?: boolean
}

const CHART_OPTS = {
  layout: { background: { color: '#161b22' }, textColor: '#8b949e' },
  grid: {
    vertLines: { color: '#21262d' },
    horzLines: { color: '#21262d' },
  },
  rightPriceScale: { borderColor: '#30363d' },
  timeScale: { borderColor: '#30363d' },
  crosshair: { mode: 0 },
}

export default function CandleChart({ candles, overlays = [], levels = [], priceLines = [], height = 420, showVolume = false }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = createChart(el, { ...CHART_OPTS, height, width: el.clientWidth })
    chartRef.current = chart

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#3fb950',
      downColor: '#f85149',
      borderVisible: false,
      wickUpColor: '#3fb950',
      wickDownColor: '#f85149',
    })
    candleSeries.setData(
      candles.map((c) => ({ ...c, time: c.time as UTCTimestamp })),
    )

    for (const ov of overlays) {
      const s = chart.addSeries(LineSeries, {
        color: ov.color,
        lineWidth: (ov.lineWidth ?? 1) as 1 | 2 | 3 | 4,
        lineStyle: ov.dashed ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        title: ov.title ?? '',
      })
      s.setData(ov.points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })))
    }

    for (const lv of levels) {
      candleSeries.createPriceLine({
        price: lv.price,
        color: lv.kind === 'resistance' ? '#f85149' : '#3fb950',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${lv.kind === 'resistance' ? 'R' : 'S'} (${lv.touches})`,
      })
    }

    for (const pl of priceLines) {
      candleSeries.createPriceLine({
        price: pl.price,
        color: pl.color,
        lineWidth: 2,
        lineStyle: LineStyle.LargeDashed,
        axisLabelVisible: true,
        title: pl.title,
      })
    }

    if (showVolume && candles.some((c) => c.volume > 0)) {
      const volSeries = chart.addSeries(HistogramSeries, {
        priceScaleId: 'vol',
        priceLineVisible: false,
        lastValueVisible: false,
        title: '',
      })
      chart.priceScale('vol').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
        visible: false,
      })
      volSeries.setData(
        candles.map((c) => ({
          time: c.time as UTCTimestamp,
          value: c.volume,
          color: c.close >= c.open ? 'rgba(63,185,80,0.35)' : 'rgba(248,81,73,0.35)',
        })),
      )
    }

    chart.timeScale().fitContent()

    const onResize = () => chart.applyOptions({ width: el.clientWidth })
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.remove()
      chartRef.current = null
    }
  }, [candles, overlays, levels, priceLines, height, showVolume])

  return <div ref={containerRef} />
}
