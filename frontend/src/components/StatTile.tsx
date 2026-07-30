import Tip from './Tip'

interface Props {
  label: string
  value: string | number | null | undefined
  sub?: string
  tone?: 'pos' | 'neg' | 'warn' | 'neutral'
  tooltip?: string
}

export default function StatTile({ label, value, sub, tone = 'neutral', tooltip }: Props) {
  const cls = tone === 'neutral' ? '' : tone
  return (
    <div className="tile">
      <div className="label">
        {label}
        {tooltip && <Tip text={tooltip} />}
      </div>
      <div className={`value ${cls}`}>{value ?? '—'}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}
