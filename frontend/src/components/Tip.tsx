interface Props {
  text: string
}

/** Small inline "?" info icon with a hover/focus tooltip bubble. */
export default function Tip({ text }: Props) {
  return (
    <span className="tip">
      <button type="button" className="tip-icon" aria-label={`info: ${text}`}>
        ?
      </button>
      <span className="tip-bubble" role="tooltip">
        {text}
      </span>
    </span>
  )
}
