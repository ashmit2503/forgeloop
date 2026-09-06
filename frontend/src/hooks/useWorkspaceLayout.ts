import { useCallback, useEffect, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import type { Task } from '../types/api'
import { elapsedMilliseconds } from '../lib/runModel'

export function useElapsed(task: Task | null): number {
  const [now, setNow] = useState(() => Date.now())
  const terminal = Boolean(task?.completed_at)
  const taskId = task?.id
  const startedAt = task?.run_started_at

  useEffect(() => {
    if (!taskId || !startedAt || terminal) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [startedAt, taskId, terminal])

  return task ? elapsedMilliseconds(task, now) : 0
}

export function usePersistentBoolean(key: string, fallback: boolean) {
  const [value, setValue] = useState(() => readStoredBoolean(key, fallback))
  useEffect(() => {
    try {
      window.localStorage.setItem(key, String(value))
    } catch {
      // Persistence is optional when storage is blocked or full.
    }
  }, [key, value])
  return [value, setValue] as const
}

export function useResizableWidth(key: string, fallback: number, min: number, max: number) {
  const [width, setWidth] = useState(() => readStoredNumber(key, fallback, min, max))
  useEffect(() => {
    try {
      window.localStorage.setItem(key, String(width))
    } catch {
      // Resizing still works when persistence is unavailable.
    }
  }, [key, width])

  const beginResize = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const startX = event.clientX
      const startWidth = width
      const direction = event.currentTarget.dataset.resizeDirection === 'left' ? -1 : 1
      event.currentTarget.setPointerCapture(event.pointerId)

      const move = (moveEvent: PointerEvent) => {
        setWidth(clamp(startWidth + (moveEvent.clientX - startX) * direction, min, max))
      }
      const finish = () => {
        window.removeEventListener('pointermove', move)
        window.removeEventListener('pointerup', finish)
        window.removeEventListener('pointercancel', finish)
      }
      window.addEventListener('pointermove', move)
      window.addEventListener('pointerup', finish)
      window.addEventListener('pointercancel', finish)
    },
    [max, min, width],
  )

  const resizeWithKeyboard = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
      event.preventDefault()
      const direction = event.currentTarget.dataset.resizeDirection === 'left' ? -1 : 1
      const delta = event.key === 'ArrowRight' ? 16 : -16
      setWidth((current) => clamp(current + delta * direction, min, max))
    },
    [max, min],
  )

  return { width, beginResize, resizeWithKeyboard }
}

function readStoredBoolean(key: string, fallback: boolean): boolean {
  try {
    const value = window.localStorage.getItem(key)
    return value === null ? fallback : value === 'true'
  } catch {
    return fallback
  }
}

function readStoredNumber(key: string, fallback: number, min: number, max: number): number {
  try {
    const value = Number(window.localStorage.getItem(key))
    return Number.isFinite(value) && value >= min && value <= max ? value : fallback
  } catch {
    return fallback
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}
