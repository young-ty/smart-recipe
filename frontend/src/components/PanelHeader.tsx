import type { ReactNode } from 'react'

type Props = {
  title: string
  trailing?: ReactNode
}

export function PanelHeader({ title, trailing }: Props) {
  return (
    <header className="panel-head">
      <h2 className="panel-title">{title}</h2>
      {trailing}
    </header>
  )
}