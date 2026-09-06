import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import type { TaskDetail } from '../types/api'
import { ClarificationPrompt } from './ClarificationPrompt'

it('keeps clarification inline with the original task and continues with the answer', async () => {
  const onAnswer = vi.fn().mockResolvedValue(undefined)
  const task = {
    id: 'task-1',
    prompt: 'Build a parser',
    runtime: 'python',
    resolved_runtime: null,
    model_provider: 'ollama',
    model: 'qwen',
    sandbox_provider: 'docker',
    max_retries: 3,
    status: 'awaiting_clarification',
    summary: null,
    error: null,
    active_attempt: null,
    cancel_requested: false,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    completed_at: null,
    clarification_question: 'Which input format should it accept?',
    event_cursor: 0,
    attempts: [],
  } satisfies TaskDetail

  render(<ClarificationPrompt task={task} onAnswer={onAnswer} />)
  expect(screen.getByText('Build a parser')).toBeInTheDocument()
  expect(screen.getByText('Which input format should it accept?')).toBeInTheDocument()
  await userEvent.type(screen.getByRole('textbox'), 'JSON lines')
  await userEvent.click(screen.getByRole('button', { name: 'Continue run' }))
  expect(onAnswer).toHaveBeenCalledWith('JSON lines')
})
