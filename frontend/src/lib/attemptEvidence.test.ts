import { describe, expect, it } from 'vitest'
import { evidenceFor } from './attemptEvidence'

describe('attempt evidence', () => {
  it('describes the expected and observed test result', () => {
    const evidence = evidenceFor({
      phase: 'test',
      argv: ['pytest', '-q'],
      exit_code: 1,
      stdout: '5 passed, 1 failed\n',
      stderr: "NameError: name 'requests' is not defined\n",
      timed_out: false,
      duration_ms: 8100,
      truncated: false,
    })

    expect(evidence.expected).toContain('every test passes')
    expect(evidence.actual).toContain('Exit 1')
    expect(evidence.actual).toContain('NameError')
    expect(evidence.difference).toBe('Expected exit code 0, but received 1.')
  })

  it('reports a matching successful gate', () => {
    const evidence = evidenceFor({
      phase: 'execute',
      argv: ['python', 'app.py'],
      exit_code: 0,
      stdout: 'ok\n',
      stderr: '',
      timed_out: false,
      duration_ms: 80,
      truncated: false,
    })
    expect(evidence.passed).toBe(true)
    expect(evidence.difference).toContain('matched')
  })

  it('does not present the internal test marker as observed behavior', () => {
    const evidence = evidenceFor({
      phase: 'test',
      argv: ['python', '-I', '-c', "print('AUTOCODER_OFFICIAL_TESTS_PASSED=1')"],
      exit_code: 0,
      stdout: '. [100%]\n1 passed in 0.25s\nAUTOCODER_OFFICIAL_TESTS_PASSED=1\n',
      stderr: '',
      timed_out: false,
      duration_ms: 300,
      truncated: false,
    })
    expect(evidence.actual).toContain('1 passed in 0.25s')
    expect(evidence.actual).not.toContain('AUTOCODER_OFFICIAL')
    expect(evidence.command).toBe('python -m pytest .autocoder_tests -q · isolated runner')
  })
})
