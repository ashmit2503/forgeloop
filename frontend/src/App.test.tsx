import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import { api } from './lib/api'
import type { AgentEvent, Attempt, TaskDetail } from './types/api'

const events = vi.hoisted(() => ({ callback: vi.fn<(name: string, event: AgentEvent) => void>() }))
vi.mock('./hooks/useTaskEvents', () => ({
  useTaskEvents: (_id: string, _after: number, callback: typeof events.callback) => {
    events.callback = callback
    return 'connected'
  },
}))
vi.mock('./components/HistoryDrawer', () => ({
  HistoryDrawer: ({ onSelect }: { onSelect: (id: string) => void }) => (
    <button onClick={() => onSelect('task-1')}>Open saved run</button>
  ),
}))
vi.mock('./components/TaskWorkspace', () => ({
  TaskWorkspace: ({
    task,
    attempt,
    currentContent,
    onAttempt,
  }: {
    task: TaskDetail
    attempt: Attempt | null
    currentContent: string | null
    onAttempt: (number: number) => void
  }) => (
    <main>
      <h1>{task.prompt}</h1>
      <p>Viewing attempt {attempt?.number}</p>
      <pre>{currentContent}</pre>
      <button onClick={() => onAttempt(1)}>Inspect first attempt</button>
    </main>
  ),
}))

function task(): TaskDetail {
  return {
    id: 'task-1',
    prompt: 'Build a Python greeting',
    status: 'succeeded',
    runtime: 'python',
    resolved_runtime: 'python',
    model_provider: 'ollama',
    model: 'test',
    sandbox_provider: 'docker',
    max_retries: 1,
    summary: null,
    error: null,
    active_attempt: null,
    cancel_requested: false,
    created_at: '2026-09-06T00:00:00Z',
    updated_at: '2026-09-06T00:00:00Z',
    completed_at: '2026-09-06T00:00:01Z',
    attempts: [],
    event_cursor: 0,
  }
}

beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
  localStorage.clear()
  vi.spyOn(api, 'status').mockResolvedValue({
    docker: { available: true, detail: 'Ready' },
    ollama: { available: true, detail: 'Ready' },
    models: ['test'],
    default_model: 'test',
    sandbox_images: { python: true },
    providers: {},
  })
  vi.spyOn(api, 'tasks').mockResolvedValue([task()])
  vi.spyOn(api, 'files').mockResolvedValue([{ path: 'app.py', size: 20 }])
  vi.spyOn(api, 'file').mockImplementation(async (_id, attempt) => ({
    path: 'app.py',
    content: `version ${attempt}`,
  }))
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('offers recovery when a task fails to load and opens it after retrying', async () => {
  vi.spyOn(api, 'task')
    .mockRejectedValueOnce(new Error('The service is unavailable'))
    .mockResolvedValue(task())
  render(<App />)
  await userEvent.click(screen.getByRole('button', { name: 'Open saved run' }))
  expect(await screen.findByRole('heading', { name: 'Could not open this run' })).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
  expect(await screen.findByRole('heading', { name: 'Build a Python greeting' })).toBeInTheDocument()
})

it('keeps an earlier attempt visible while the active attempt streams a file', async () => {
  const saved = task()
  saved.status = 'generating'
  saved.completed_at = null
  saved.active_attempt = 2
  saved.attempts = [1, 2].map(
    (number) =>
      ({ id: `attempt-${number}`, number, changed_files: ['app.py'], stdout: '', stderr: '' }) as Attempt,
  )
  vi.spyOn(api, 'task').mockResolvedValue(saved)
  render(<App />)
  await userEvent.click(screen.getByRole('button', { name: 'Open saved run' }))
  await screen.findByText('version 2')
  await userEvent.click(screen.getByRole('button', { name: 'Inspect first attempt' }))
  await screen.findByText('version 1')
  act(() => {
    events.callback('file_stream_start', {
      task_id: saved.id,
      timestamp: saved.updated_at,
      attempt: 2,
      path: 'app.py',
    })
    events.callback('file_stream_chunk', {
      task_id: saved.id,
      timestamp: saved.updated_at,
      attempt: 2,
      path: 'app.py',
      chunk: 'new live content',
    })
    events.callback('file_stream_complete', {
      task_id: saved.id,
      timestamp: saved.updated_at,
      attempt: 2,
      path: 'app.py',
    })
  })
  await waitFor(() => expect(screen.getByText('version 1')).toBeInTheDocument())
  expect(screen.queryByText('new live content')).not.toBeInTheDocument()
})
