import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TopBar } from './TopBar'
import type { TaskDetail } from '../types/api'

describe('TopBar', () => {
  it('keeps icon-only navigation accessible when compact styles hide its text', () => {
    render(
      <TopBar
        task={null}
        elapsed={0}
        onHistory={vi.fn()}
        onNew={vi.fn()}
        onCancel={vi.fn()}
        onDelete={vi.fn()}
        onBenchmarks={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'ForgeLoop home' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Runs' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Benchmarks' })).toBeInTheDocument()
  })

  it('does not announce a fake attempt before intake and clarification finish', () => {
    const task = {
      id: 'task',
      prompt: 'Build a small Python CLI',
      runtime: 'python',
      resolved_runtime: null,
      model_provider: 'ollama',
      model: 'qwen',
      sandbox_provider: 'docker',
      max_retries: 2,
      status: 'awaiting_clarification',
      summary: null,
      error: null,
      active_attempt: null,
      cancel_requested: false,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      completed_at: null,
      run_started_at: null,
      attempts: [],
      event_cursor: 1,
    } satisfies TaskDetail

    render(
      <TopBar
        task={task}
        elapsed={0}
        onHistory={vi.fn()}
        onNew={vi.fn()}
        onCancel={vi.fn()}
        onDelete={vi.fn()}
        onBenchmarks={vi.fn()}
      />,
    )

    expect(screen.getByLabelText('Attempts not started; 3 available')).toHaveTextContent('— / 3')
    expect(screen.getByLabelText('Elapsed time 00:00')).toBeInTheDocument()
  })
})
