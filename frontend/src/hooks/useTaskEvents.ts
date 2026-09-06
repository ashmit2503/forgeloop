import { useEffect, useRef, useState } from 'react'
import type { AgentEvent } from '../types/api'

const eventNames = [
  'state',
  'log',
  'files_changed',
  'attempt_started',
  'attempt_complete',
  'task_complete',
  'cancellation_requested',
  'clarification_required',
  'clarification_answered',
  'file_stream_start',
  'file_stream_chunk',
  'file_stream_complete',
  'tool',
  'official_test_created',
  'official_test_reused',
  'official_test_revised',
  'judge',
  'infrastructure_retry',
] as const

export function useTaskEvents(
  taskId: string | null,
  after: number,
  onEvent: (name: string, event: AgentEvent) => void,
  onReconnect?: () => void,
) {
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'reconnecting'>('connecting')
  const callbackRef = useRef(onEvent)
  const reconnectRef = useRef(onReconnect)
  const afterRef = useRef(after)
  callbackRef.current = onEvent
  reconnectRef.current = onReconnect
  afterRef.current = after

  useEffect(() => {
    if (!taskId) return
    setConnection('connecting')
    const source = new EventSource(
      `/api/tasks/${encodeURIComponent(taskId)}/events?after=${afterRef.current}`,
    )
    const listeners = eventNames.map((name) => {
      const listener = (raw: Event) => {
        const message = raw as MessageEvent<string>
        // A concurrent detail refresh may already contain this persisted log.
        if (name === 'log' && message.lastEventId && Number(message.lastEventId) <= afterRef.current) return
        let payload: AgentEvent
        try {
          payload = JSON.parse(message.data) as AgentEvent
        } catch {
          // Ignore malformed event data; the next persisted event can still be consumed.
          return
        }
        if (name === 'task_complete') source.close()
        callbackRef.current(name, payload)
      }
      source.addEventListener(name, listener)
      return [name, listener] as const
    })
    const onOpen = () => {
      setConnection('connected')
      // A snapshot reconciles logs and state compacted while we were disconnected.
      reconnectRef.current?.()
    }
    const onError = () => setConnection('reconnecting')
    source.addEventListener('open', onOpen)
    source.addEventListener('error', onError)
    return () => {
      source.removeEventListener('open', onOpen)
      source.removeEventListener('error', onError)
      listeners.forEach(([name, listener]) => source.removeEventListener(name, listener))
      source.close()
    }
  }, [taskId])
  return taskId ? connection : null
}
