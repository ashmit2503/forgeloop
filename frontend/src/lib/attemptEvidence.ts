import type { Attempt, CommandResult, TaskDetail } from '../types/api'
import { diagnoseAttempt, diagnosisLabel } from './diagnosis'
import { formatDuration, stripInternalOutput } from './runModel'

export interface GateEvidence {
  phase: CommandResult['phase']
  command: string
  expected: string
  actual: string
  difference: string
  output: string
  passed: boolean
}

export function evidenceFor(result: CommandResult): GateEvidence {
  const passed = result.exit_code === 0 && !result.timed_out
  const command = displayCommand(result)
  const output = meaningfulOutput(result)

  return {
    phase: result.phase,
    command,
    expected: expectationFor(result.phase),
    actual: actualFor(result, output),
    difference: differenceFor(result, passed),
    output,
    passed,
  }
}

function displayCommand(result: CommandResult): string {
  if (
    result.phase === 'test' &&
    result.argv.some((value) => value.includes('AUTOCODER_OFFICIAL_TESTS_PASSED='))
  ) {
    return 'python -m pytest .autocoder_tests -q · isolated runner'
  }
  return clamp(result.argv.map(quoteArgument).join(' '), 360)
}

export function agentNoteFor(task: TaskDetail, attempt: Attempt): string {
  const diagnosis = diagnoseAttempt(attempt)
  const nextAttempt = task.attempts.find((item) => item.number === attempt.number + 1)

  if (attempt.status === 'succeeded') {
    return 'Every required gate matched its expected result. No further repair was needed.'
  }
  if (attempt.status === 'running') {
    return attempt.summary || 'The agent is applying this workspace change before running the gates.'
  }
  if (attempt.status === 'cancelled') {
    return 'This attempt stopped before the agent could finish validating the workspace.'
  }
  if (nextAttempt?.summary) {
    return `The ${diagnosis?.phase || attempt.phase || 'validation'} gate exposed ${diagnosisLabel(diagnosis)}. Next fix: ${nextAttempt.summary}`
  }
  if (diagnosis) {
    return `The ${diagnosis.phase || 'validation'} gate exposed ${diagnosisLabel(diagnosis)}. No additional repair attempt is available.`
  }
  return attempt.summary || 'The attempt did not satisfy the required success gate.'
}

function expectationFor(phase: CommandResult['phase']): string {
  if (phase === 'install') return 'Dependencies install completely and the command exits with code 0.'
  if (phase === 'test') return 'The automated test command exits with code 0 and every test passes.'
  if (phase === 'healthcheck')
    return 'The service starts before the deadline and its health endpoint returns a 2xx response.'
  if (phase === 'judge') return 'The observed behavior satisfies the task-specific judging criteria.'
  return 'The generated program completes before the deadline and exits with code 0.'
}

function actualFor(result: CommandResult, output: string): string {
  if (result.timed_out)
    return `Timed out after ${formatDuration(result.duration_ms)}${output ? ` · ${output}` : ''}`
  return `Exit ${result.exit_code ?? '—'} after ${formatDuration(result.duration_ms)}${output ? ` · ${output}` : ''}`
}

function differenceFor(result: CommandResult, passed: boolean): string {
  if (passed) return 'No difference. The observed result matched the expected gate.'
  if (result.timed_out) return 'Expected the gate to finish successfully, but it exceeded its time limit.'
  return `Expected exit code 0, but received ${result.exit_code ?? 'no exit code'}.`
}

function meaningfulOutput(result: CommandResult): string {
  const stderr = usefulLine(result.stderr)
  const stdout = usefulLine(stripInternalOutput(result.stdout))
  return clamp(stderr || stdout, 240)
}

function usefulLine(value: string): string {
  const lines = value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  return (
    [...lines]
      .reverse()
      .find((line) => /(?:failed|error|exception|assert|expected|received|passed|timed out)/i.test(line)) ||
    lines.at(-1) ||
    ''
  )
}

function quoteArgument(value: string): string {
  return /\s/.test(value) ? JSON.stringify(value) : value
}

function clamp(value: string, length: number): string {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value
}
