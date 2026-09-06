import { render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { TaskDetail } from '../types/api'
import { AgentLog } from './AgentLog'

it('groups an attempt with its plan and expected-versus-observed gate evidence', () => {
  const task: TaskDetail = {
    id: 'task-1',
    prompt: 'Print hello',
    runtime: 'python',
    resolved_runtime: 'python',
    model_provider: 'ollama',
    model: 'qwen',
    sandbox_provider: 'docker',
    max_retries: 3,
    status: 'repairing',
    summary: null,
    error: null,
    active_attempt: 2,
    cancel_requested: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:03Z',
    completed_at: null,
    event_cursor: 4,
    attempts: [
      {
        id: 'attempt-1',
        number: 1,
        status: 'failed',
        phase: 'test',
        runtime: 'python',
        summary: 'Create the greeting command.',
        failure_message: 'test exited with code 1',
        exit_code: 1,
        timed_out: false,
        stdout: '1 failed\n',
        stderr: 'AssertionError: expected hello\n',
        logs_truncated: false,
        changed_files: ['app.py'],
        results: [
          {
            phase: 'test',
            argv: ['python', '-m', 'pytest', '.autocoder_tests'],
            exit_code: 1,
            stdout: '1 failed\n',
            stderr: 'AssertionError: expected hello\n',
            timed_out: false,
            duration_ms: 420,
            truncated: false,
          },
        ],
        started_at: '2026-01-01T00:00:01Z',
        completed_at: '2026-01-01T00:00:02Z',
        duration_ms: 1_000,
        plan: {
          summary: 'Build the first implementation.',
          steps: ['Write app.py', 'Run a scratch check'],
          failure_response: null,
        },
        tool_events: [],
        inner_iterations: 2,
        test_contract: {
          deterministic: true,
          summary: 'Greeting acceptance test',
          expected: 'The command prints hello.',
          test_files: [],
          test_command: { argv: ['python', '-m', 'pytest', '.autocoder_tests'] },
          execution: { kind: 'command', command: { argv: ['python', 'app.py'] } },
          judge_criteria: null,
          revision_reason: null,
        },
      },
    ],
  }

  render(
    <AgentLog
      task={task}
      selectedAttempt={1}
      collapsed={false}
      mobileOpen={false}
      onToggle={vi.fn()}
      onMobileClose={vi.fn()}
      onAttempt={vi.fn()}
      onShowOutput={vi.fn()}
    />,
  )

  expect(screen.getByText('Build the first implementation.')).toBeInTheDocument()
  expect(screen.getAllByText('The command prints hello.')).toHaveLength(2)
  expect(screen.getByText(/Exit 1 after 420ms/)).toBeInTheDocument()
  expect(screen.getByText('Expected exit code 0, but received 1.')).toBeInTheDocument()
})
