import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Toast } from './Toast'

afterEach(() => vi.useRealTimers())

it('announces errors assertively and dismisses them after the longer timeout', () => {
  vi.useFakeTimers()
  const onClose = vi.fn()
  render(<Toast toast={{ id: 1, message: 'Sandbox failed.', tone: 'error' }} onClose={onClose} />)

  expect(screen.getByRole('alert')).toHaveTextContent('Sandbox failed.')
  act(() => vi.advanceTimersByTime(7_999))
  expect(onClose).not.toHaveBeenCalled()
  act(() => vi.advanceTimersByTime(1))
  expect(onClose).toHaveBeenCalledOnce()
})
