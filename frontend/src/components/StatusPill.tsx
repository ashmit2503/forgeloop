import { CheckCircle } from '@phosphor-icons/react/dist/csr/CheckCircle'
import { Circle } from '@phosphor-icons/react/dist/csr/Circle'
import { CircleNotch } from '@phosphor-icons/react/dist/csr/CircleNotch'
import { XCircle } from '@phosphor-icons/react/dist/csr/XCircle'
import type { VisualRunState } from '../types/workspace'

export function StatusPill({
  state,
  label,
  compact = false,
}: {
  state: VisualRunState
  label: string
  compact?: boolean
}) {
  const Icon =
    state === 'pass' ? CheckCircle : state === 'fail' ? XCircle : state === 'running' ? CircleNotch : Circle
  return (
    <span className={`status-pill ${state}${compact ? ' compact' : ''}`}>
      <Icon aria-hidden="true" />
      <span>{label}</span>
    </span>
  )
}
