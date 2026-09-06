import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTaskEvents } from './useTaskEvents'

class FakeEventSource {
  static instances: FakeEventSource[] = []

  readonly url: string
  readonly listeners = new Map<string, Set<EventListener>>()
  closed = false

  constructor(url: string | URL) {
    this.url = String(url)
    FakeEventSource.instances.push(this)
  }

  addEventListener(name: string, listener: EventListener) {
    const listeners = this.listeners.get(name) || new Set<EventListener>()
    listeners.add(listener)
    this.listeners.set(name, listeners)
  }

  removeEventListener(name: string, listener: EventListener) {
    this.listeners.get(name)?.delete(listener)
  }

  close() {
    this.closed = true
  }

  emit(name: string, data: object, lastEventId = '') {
    const event = new MessageEvent(name, { data: JSON.stringify(data), lastEventId })
    this.listeners.get(name)?.forEach((listener) => listener(event))
  }
}

describe('useTaskEvents', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  afterEach(() => vi.unstubAllGlobals())

  it('subscribes to streamed files, tool activity, and test lifecycle events', () => {
    const onEvent = vi.fn()
    const { unmount } = renderHook(() => useTaskEvents('task-1', 42, onEvent))
    const source = FakeEventSource.instances[0]

    expect(source.url).toBe('/api/tasks/task-1/events?after=42')
    source.emit('file_stream_chunk', { task_id: 'task-1', path: 'app.py', chunk: 'print' })
    source.emit('tool', { task_id: 'task-1', index: 1, tool: 'write_file' })
    source.emit('official_test_revised', { task_id: 'task-1', attempt: 2 })

    expect(onEvent).toHaveBeenNthCalledWith(
      1,
      'file_stream_chunk',
      expect.objectContaining({ chunk: 'print' }),
    )
    expect(onEvent).toHaveBeenNthCalledWith(2, 'tool', expect.objectContaining({ tool: 'write_file' }))
    expect(onEvent).toHaveBeenNthCalledWith(
      3,
      'official_test_revised',
      expect.objectContaining({ attempt: 2 }),
    )

    unmount()
    expect(source.closed).toBe(true)
  })

  it('reconciles snapshots after reconnecting and closes on completion', () => {
    const onReconnect = vi.fn()
    const { result } = renderHook(() => useTaskEvents('task-1', 42, vi.fn(), onReconnect))
    const source = FakeEventSource.instances[0]
    act(() => source.emit('open', {}))
    expect(result.current).toBe('connected')
    act(() => source.emit('error', {}))
    expect(result.current).toBe('reconnecting')
    act(() => source.emit('open', {}))
    expect(onReconnect).toHaveBeenCalledTimes(2)
    act(() => source.emit('task_complete', { status: 'interrupted' }))
    expect(source.closed).toBe(true)
  })

  it('does not append a log already included in a refreshed task snapshot', () => {
    const callback = vi.fn()
    const { rerender } = renderHook(({ after }) => useTaskEvents('task-1', after, callback), {
      initialProps: { after: 42 },
    })
    rerender({ after: 50 })
    const source = FakeEventSource.instances[0]
    source.emit('log', { chunk: 'already persisted' }, '49')
    source.emit('log', { chunk: 'new output' }, '51')
    expect(callback).toHaveBeenCalledTimes(1)
    expect(callback).toHaveBeenCalledWith('log', { chunk: 'new output' })
  })
})
