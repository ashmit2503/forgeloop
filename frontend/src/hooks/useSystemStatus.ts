import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { SystemStatus } from '../types/api'

export function useSystemStatus() {
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null)
  const [systemStatusLoading, setSystemStatusLoading] = useState(false)
  const [systemStatusError, setSystemStatusError] = useState<string | null>(null)
  const pending = useRef(false)

  const refreshStatus = useCallback(async () => {
    if (pending.current) return
    pending.current = true
    setSystemStatusLoading(true)
    try {
      setSystemStatus(await api.status())
      setSystemStatusError(null)
    } catch (reason) {
      setSystemStatus(null)
      setSystemStatusError(reason instanceof Error ? reason.message : 'The local service is unreachable.')
    } finally {
      pending.current = false
      setSystemStatusLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshStatus()
    const timer = window.setInterval(() => void refreshStatus(), 15_000)
    window.addEventListener('focus', refreshStatus)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', refreshStatus)
    }
  }, [refreshStatus])

  return { systemStatus, systemStatusLoading, systemStatusError, refreshStatus }
}
