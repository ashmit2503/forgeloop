import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

function BrokenView(): never {
  throw new Error('render failed')
}

afterEach(() => {
  vi.restoreAllMocks()
})

it('keeps a render failure from blanking the entire app', () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
  render(
    <ErrorBoundary>
      <BrokenView />
    </ErrorBoundary>,
  )
  expect(screen.getByRole('alert')).toHaveTextContent('The interface stopped unexpectedly.')
  expect(screen.getByRole('button', { name: 'Reload app' })).toBeVisible()
})
