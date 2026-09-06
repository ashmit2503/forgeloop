import { describe, expect, it } from 'vitest'
import type { Attempt } from '../types/api'
import { diagnoseAttempt, diagnosisLabel } from './diagnosis'

function failedAttempt(overrides: Partial<Attempt> = {}): Attempt {
  return {
    id: 'attempt-1',
    number: 1,
    status: 'failed',
    phase: 'test',
    runtime: 'python',
    summary: null,
    failure_message: null,
    exit_code: 1,
    timed_out: false,
    stdout: '',
    stderr: '',
    logs_truncated: false,
    changed_files: [],
    results: [],
    started_at: '2026-08-21T00:00:00Z',
    completed_at: '2026-08-21T00:00:01Z',
    duration_ms: 1000,
    ...overrides,
  }
}

describe('diagnoseAttempt', () => {
  it('extracts a Python exception and source location', () => {
    const diagnosis = diagnoseAttempt(
      failedAttempt({
        stderr:
          '  File "/workspace/src/app.py", line 28, in run\nNameError: name \'client\' is not defined\n',
      }),
    )
    expect(diagnosis).toMatchObject({ exception: 'NameError', file: 'src/app.py', line: 28, exitCode: 1 })
    expect(diagnosisLabel(diagnosis)).toBe('NameError on line 28')
  })

  it('labels timeouts without inventing an exception', () => {
    const diagnosis = diagnoseAttempt(
      failedAttempt({ timed_out: true, exit_code: null, failure_message: 'Command timed out' }),
    )
    expect(diagnosisLabel(diagnosis)).toBe('Test timed out')
  })

  it('extracts pytest colon-style Python locations', () => {
    const diagnosis = diagnoseAttempt(
      failedAttempt({
        stdout: ".autocoder_tests/test_app.py:9: NameError: name 'client' is not defined\n",
      }),
    )

    expect(diagnosis).toMatchObject({ exception: 'NameError', file: '.autocoder_tests/test_app.py', line: 9 })
  })
})
