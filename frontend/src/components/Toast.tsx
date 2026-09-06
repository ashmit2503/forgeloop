import { CheckCircle } from '@phosphor-icons/react/dist/csr/CheckCircle'
import { Info } from '@phosphor-icons/react/dist/csr/Info'
import { Warning } from '@phosphor-icons/react/dist/csr/Warning'
import { X } from '@phosphor-icons/react/dist/csr/X'
import { useEffect, useRef } from 'react'

export interface ToastMessage {
  id: number
  message: string
  tone: 'success' | 'error' | 'info'
}

export function Toast({ toast, onClose }: { toast: ToastMessage | null; onClose: () => void }) {
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    if (!toast) return
    const timeout = window.setTimeout(() => closeRef.current(), toast.tone === 'error' ? 8_000 : 5_000)
    return () => window.clearTimeout(timeout)
  }, [toast])

  if (!toast) return null
  const Icon = toast.tone === 'success' ? CheckCircle : toast.tone === 'error' ? Warning : Info
  return (
    <div
      className={`toast ${toast.tone}`}
      role={toast.tone === 'error' ? 'alert' : 'status'}
      aria-atomic="true"
    >
      <Icon aria-hidden="true" />
      <span>{toast.message}</span>
      <button type="button" onClick={onClose} aria-label="Dismiss notification">
        <X aria-hidden="true" />
      </button>
    </div>
  )
}
