import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeAll, expect, it, vi } from 'vitest'
import type { TaskDetail } from '../types/api'
import { CommandPalette } from './CommandPalette'

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.open = true
  }
  HTMLDialogElement.prototype.close = function close() {
    this.open = false
    this.dispatchEvent(new Event('close'))
  }
})

it('filters commands and runs the active result from the keyboard', async () => {
  const onNew = vi.fn()
  const onClose = vi.fn()
  render(
    <CommandPalette
      open
      task={null}
      onClose={onClose}
      onNew={onNew}
      onTab={vi.fn()}
      onAttempt={vi.fn()}
      onToggleLog={vi.fn()}
      onCancel={vi.fn()}
    />,
  )

  const search = screen.getByRole('textbox', { name: 'Search commands' })
  await userEvent.type(search, 'new')
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Enter' })

  expect(onNew).toHaveBeenCalledOnce()
  expect(onClose).toHaveBeenCalled()
})

it('does not offer cancellation for a stopped task', () => {
  render(
    <CommandPalette
      open
      task={{ status: 'stopped', attempts: [] } as unknown as TaskDetail}
      onClose={vi.fn()}
      onNew={vi.fn()}
      onTab={vi.fn()}
      onAttempt={vi.fn()}
      onToggleLog={vi.fn()}
      onCancel={vi.fn()}
    />,
  )

  expect(screen.queryByRole('option', { name: /Stop task/ })).not.toBeInTheDocument()
})
