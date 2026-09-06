import { describe, expect, it } from 'vitest'
import {
  consumePersistedPrefix,
  elapsedMilliseconds,
  formatClock,
  mergeWorkspaceFiles,
  recommendedTab,
  statusLabelFor,
  stripInternalOutput,
  visualStateFor,
} from './runModel'

describe('run presentation model', () => {
  it('maps state to compact visual intent', () => {
    expect(visualStateFor('repairing')).toBe('running')
    expect(visualStateFor('succeeded')).toBe('pass')
    expect(visualStateFor('failed')).toBe('fail')
    expect(statusLabelFor('failed')).toBe('Failed')
    expect(statusLabelFor('cancelled')).toBe('Cancelled')
    expect(statusLabelFor('planning')).toBe('Planning')
    expect(statusLabelFor('generating')).toBe('Writing')
    expect(statusLabelFor('testing')).toBe('Testing')
    expect(statusLabelFor('repairing')).toBe('Repairing')
    expect(recommendedTab('repairing')).toBe('code')
    expect(recommendedTab('queued')).toBe('code')
    expect(recommendedTab('testing')).toBe('output')
    expect(formatClock(78_000)).toBe('01:18')
  })

  it('marks added, modified, and deleted workspace files', () => {
    const files = mergeWorkspaceFiles(
      [
        { path: 'keep.py', size: 8 },
        { path: 'added.py', size: 12 },
      ],
      [
        { path: 'keep.py', size: 6 },
        { path: 'deleted.py', size: 4 },
      ],
      ['keep.py', 'added.py', 'deleted.py'],
    )
    expect(files).toEqual([
      { path: 'added.py', size: 12, change: 'added' },
      { path: 'deleted.py', size: 4, change: 'deleted' },
      { path: 'keep.py', size: 8, change: 'modified' },
    ])
  })

  it('uses the run start rather than task creation time for the elapsed clock', () => {
    expect(
      elapsedMilliseconds(
        {
          id: 'task',
          prompt: 'Build a CLI',
          runtime: 'python',
          resolved_runtime: 'python',
          model_provider: 'ollama',
          model: 'qwen',
          sandbox_provider: 'docker',
          max_retries: 3,
          status: 'executing',
          summary: null,
          error: null,
          active_attempt: 1,
          cancel_requested: false,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:05:32Z',
          completed_at: null,
          run_started_at: '2026-01-01T00:05:30Z',
        },
        new Date('2026-01-01T00:05:32Z').getTime(),
      ),
    ).toBe(2_000)
  })

  it('reconciles persisted logs without duplicating live SSE chunks', () => {
    expect(consumePersistedPrefix('alpha\nbeta\n', '', 'alpha\n')).toBe('beta\n')
    expect(consumePersistedPrefix('alpha\n', '', 'alpha\nbeta\n')).toBe('')
    expect(consumePersistedPrefix('new\n', 'old\n', 'unrelated\n')).toBe('new\n')
  })

  it('keeps internal official-test markers out of user-facing output', () => {
    expect(stripInternalOutput('.  [100%]\nAUTOCODER_OFFICIAL_TESTS_PASSED=1\nhello\n')).toBe(
      '.  [100%]\nhello\n',
    )
  })
})
