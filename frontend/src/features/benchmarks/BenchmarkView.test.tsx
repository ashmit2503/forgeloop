import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import type { BenchmarkRun, BenchmarkSet, SystemStatus } from '../../types/api'
import { BenchmarkView } from './BenchmarkView'

vi.mock('../../lib/api', () => ({
  api: {
    benchmarkSets: vi.fn(),
    benchmarkRuns: vi.fn(),
    runBenchmark: vi.fn(),
    cancelBenchmark: vi.fn(),
    importBenchmarkSet: vi.fn(),
    addBenchmarkTask: vi.fn(),
  },
}))

const set: BenchmarkSet = {
  id: 'set-1',
  name: 'Python core',
  version: '1.0',
  tasks: [{ id: 'task-a', prompt: 'Build a parser', attachments: [] }],
  created_at: '2026-01-01T00:00:00Z',
}
const run: BenchmarkRun = {
  id: 'run-1',
  benchmark_set_id: set.id,
  status: 'running',
  task_ids: ['task-1'],
  completed_tasks: 0,
  total_tasks: 1,
  report: null,
  created_at: '2026-01-01T00:00:00Z',
  completed_at: null,
}
const status: SystemStatus = {
  docker: { available: true, detail: 'ready' },
  ollama: { available: true, detail: 'ready' },
  models: ['qwen-local'],
  sandbox_images: { python: true },
  providers: {},
}

beforeEach(() => {
  vi.mocked(api.benchmarkSets).mockResolvedValue([set])
  vi.mocked(api.benchmarkRuns).mockResolvedValueOnce([]).mockResolvedValue([run])
  vi.mocked(api.runBenchmark).mockResolvedValue(run)
  vi.mocked(api.cancelBenchmark).mockResolvedValue({ ...run, status: 'cancelling' })
})

it('uses the selected model and attempt bound and can stop an active benchmark', async () => {
  const onToast = vi.fn()
  render(<BenchmarkView status={status} onOpenTask={vi.fn()} onToast={onToast} />)

  await screen.findByText('Python core')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Benchmark attempts' }), '2')
  await userEvent.click(screen.getByText('Run resources'))
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Benchmark execution timeout' }), '120')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Benchmark memory' }), '2048')
  await userEvent.click(screen.getByRole('checkbox', { name: 'Benchmark execution network' }))
  await userEvent.click(screen.getByRole('button', { name: 'Run all' }))

  await waitFor(() =>
    expect(api.runBenchmark).toHaveBeenCalledWith(
      'set-1',
      expect.objectContaining({
        model: 'qwen-local',
        max_retries: 1,
        execution_timeout_seconds: 120,
        memory_mb: 2048,
        execution_network: true,
      }),
    ),
  )
  expect(await screen.findByText('Task 1 of 1')).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Stop run' }))
  await waitFor(() => expect(api.cancelBenchmark).toHaveBeenCalledWith('run-1'))
})
