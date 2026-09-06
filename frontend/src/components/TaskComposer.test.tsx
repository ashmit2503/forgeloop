import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import type { SystemStatus } from '../types/api'
import { TaskComposer } from './TaskComposer'

const status: SystemStatus = {
  docker: { available: true, detail: 'ready' },
  ollama: { available: true, detail: 'ready' },
  models: ['qwen-test'],
  default_model: 'qwen-test',
  sandbox_images: { python: true },
  providers: {},
}

it('submits Python/Ollama/Docker resource settings from the real composer', async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  render(<TaskComposer status={status} statusLoading={false} onRefreshStatus={vi.fn()} onSubmit={onSubmit} />)
  await userEvent.click(screen.getByRole('button', { name: 'Options' }))
  expect(screen.getByRole('combobox', { name: 'Ollama model' })).toHaveValue('qwen-test')
  await userEvent.type(screen.getByRole('textbox', { name: 'Task prompt' }), 'Build a Python CLI')
  await userEvent.click(screen.getByRole('button', { name: /Run/ }))
  expect(onSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      prompt: 'Build a Python CLI',
      runtime: 'python',
      model_provider: 'ollama',
      sandbox_provider: 'docker',
      model: 'qwen-test',
    }),
  )
})

it('does not queue a task while the local execution environment is unavailable', async () => {
  const onSubmit = vi.fn()
  render(
    <TaskComposer
      status={{ ...status, docker: { available: false, detail: 'Docker is stopped' } }}
      statusLoading={false}
      onRefreshStatus={vi.fn()}
      onSubmit={onSubmit}
    />,
  )

  await userEvent.type(screen.getByRole('textbox', { name: 'Task prompt' }), 'Build a Python CLI')
  expect(screen.getByRole('button', { name: /Run/ })).toBeDisabled()
  expect(onSubmit).not.toHaveBeenCalled()
})

it('shows an explicit local-service failure instead of an endless setup check', () => {
  render(
    <TaskComposer
      status={null}
      statusLoading={false}
      statusError="Failed to fetch"
      onRefreshStatus={vi.fn()}
      onSubmit={vi.fn()}
    />,
  )
  expect(screen.getByText('Local service unavailable')).toBeVisible()
})

it('rejects archive uploads because existing projects must be attached as individual files', async () => {
  const { container } = render(
    <TaskComposer status={status} statusLoading={false} onRefreshStatus={vi.fn()} onSubmit={vi.fn()} />,
  )
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  await userEvent.upload(
    input,
    new File(['not really an archive'], 'project.zip', { type: 'application/zip' }),
  )
  expect(screen.getByRole('alert')).toHaveTextContent('Attach individual text files, not an archive.')
})

it('rejects an oversized attachment selection instead of silently dropping files', async () => {
  const { container } = render(
    <TaskComposer status={status} statusLoading={false} onRefreshStatus={vi.fn()} onSubmit={vi.fn()} />,
  )
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  const files = Array.from({ length: 101 }, (_, index) => new File(['x'], `file-${index}.py`))
  await userEvent.upload(input, files)
  expect(screen.getByRole('alert')).toHaveTextContent('You can attach at most 100 files.')
})

it('rejects the private test namespace regardless of path casing', async () => {
  const { container } = render(
    <TaskComposer status={status} statusLoading={false} onRefreshStatus={vi.fn()} onSubmit={vi.fn()} />,
  )
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File(['assert True'], '.AUTOCODER_TESTS')
  Object.defineProperty(file, 'text', { value: async () => 'assert True' })
  await userEvent.upload(input, file)
  expect(screen.getByRole('alert')).toHaveTextContent('.autocoder_tests is reserved')
})
