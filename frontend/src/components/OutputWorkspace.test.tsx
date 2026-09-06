import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { Attempt } from '../types/api'
import { OutputWorkspace } from './OutputWorkspace'

const attempt: Attempt = {
  id: 'attempt-1',
  number: 1,
  status: 'running',
  phase: 'test',
  runtime: 'python',
  summary: 'Check the program',
  failure_message: null,
  exit_code: null,
  timed_out: false,
  stdout: '',
  stderr: '',
  logs_truncated: false,
  changed_files: ['app.py'],
  results: [],
  started_at: '2026-01-01T00:00:00Z',
  completed_at: null,
  duration_ms: null,
}

it('switches to stderr when the selected attempt transitions to failed', () => {
  const { rerender } = render(
    <OutputWorkspace
      attempt={attempt}
      taskStatus="testing"
      stdout="test started\n"
      stderr=""
      diagnosis={null}
      onDismissDiagnosis={() => undefined}
    />,
  )
  expect(screen.getByRole('tab', { name: /stdout/ })).toHaveAttribute('aria-selected', 'true')

  rerender(
    <OutputWorkspace
      attempt={{ ...attempt, status: 'failed', exit_code: 1 }}
      taskStatus="stopped"
      stdout="test started\n"
      stderr="AssertionError: expected 2, got 3\n"
      diagnosis={null}
      onDismissDiagnosis={() => undefined}
    />,
  )

  expect(screen.getByRole('tab', { name: /stderr/ })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tabpanel')).toHaveTextContent('AssertionError: expected 2, got 3')
})
