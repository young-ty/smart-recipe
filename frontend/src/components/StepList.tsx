type Props = {
  steps: string[]
}

export function StepList({ steps }: Props) {
  return (
    <ol className="steps">
      {steps.map((step, index) => (
        <li key={`${index}-${step}`}>{step}</li>
      ))}
    </ol>
  )
}