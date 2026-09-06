import type { Attempt, CommandResult, Task, TaskDetail, TaskStatus } from '../types/api'
import { terminalStatuses } from '../types/api'
import { diagnoseAttempt, diagnosisLabel } from './diagnosis'
import type { AgentLogEntry, VisualRunState, WorkspaceFile } from '../types/workspace'

export function visualStateFor(status: TaskStatus | undefined): VisualRunState {
  if (!status) return 'idle'
  if (status === 'succeeded') return 'pass'
  if (status === 'failed' || status === 'stopped' || status === 'cancelled' || status === 'interrupted')
    return 'fail'
  return 'running'
}

export function statusLabelFor(status: TaskStatus | undefined): string {
  const labels: Record<TaskStatus, string> = {
    queued: 'Queued',
    intake: 'Checking',
    awaiting_clarification: 'Needs input',
    planning: 'Planning',
    generating: 'Writing',
    installing: 'Installing',
    testing: 'Testing',
    executing: 'Executing',
    repairing: 'Repairing',
    succeeded: 'Passed',
    stopped: 'Stopped',
    failed: 'Failed',
    cancelled: 'Cancelled',
    interrupted: 'Interrupted',
  }
  return status ? labels[status] : 'Ready'
}

export function activePhaseLabel(status: TaskStatus): string {
  const labels: Record<TaskStatus, string> = {
    queued: 'Waiting for the agent',
    intake: 'Checking the request',
    awaiting_clarification: 'Waiting for your answer',
    planning: 'Planning the attempt',
    generating: 'Writing code',
    installing: 'Preparing sandbox',
    testing: 'Checking result',
    executing: 'Running in sandbox',
    repairing: 'Repairing code',
    succeeded: 'Passed',
    stopped: 'Stopped',
    failed: 'Failed',
    cancelled: 'Cancelled',
    interrupted: 'Interrupted',
  }
  return labels[status]
}

export function recommendedTab(status: TaskStatus): 'code' | 'output' {
  return status === 'installing' || status === 'testing' || status === 'executing' ? 'output' : 'code'
}

export function taskIsTerminal(task: Task | TaskDetail): boolean {
  return terminalStatuses.includes(task.status)
}

export function elapsedMilliseconds(task: Task, now = Date.now()): number {
  if (!task.run_started_at) return 0
  const start = new Date(task.run_started_at).getTime()
  const end = task.completed_at ? new Date(task.completed_at).getTime() : now
  return Math.max(0, end - start)
}

export function formatClock(milliseconds: number): string {
  const totalSeconds = Math.floor(milliseconds / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return hours > 0
    ? `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
    : `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
}

export function formatDuration(milliseconds: number | null): string {
  if (milliseconds === null) return 'now'
  if (milliseconds < 1000) return `${milliseconds}ms`
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(milliseconds < 10_000 ? 1 : 0)}s`
  return formatClock(milliseconds)
}

export function formatRelativeTime(value: string): string {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60_000))
  if (minutes < 1) return 'now'
  if (minutes < 60) return `${minutes}m`
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h`
  return new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function consumePersistedPrefix(
  live: string,
  previousPersisted: string,
  nextPersisted: string,
): string {
  if (!nextPersisted.startsWith(previousPersisted)) return live
  const newlyPersisted = nextPersisted.slice(previousPersisted.length)
  if (newlyPersisted.startsWith(live)) return ''
  return live.startsWith(newlyPersisted) ? live.slice(newlyPersisted.length) : live
}

export function stripInternalOutput(value: string): string {
  return value
    .split(/(?<=\n)/)
    .filter((line) => !line.trimStart().startsWith('AUTOCODER_OFFICIAL_TESTS_PASSED='))
    .join('')
}

export function buildAgentLog(task: TaskDetail): AgentLogEntry[] {
  const entries: AgentLogEntry[] = []

  if (task.attempts.length === 0) {
    entries.push({
      id: 'generation-pending',
      attempt: 1,
      label: task.status === 'queued' ? 'Queued' : 'Writing code',
      detail: task.status === 'queued' ? 'Waiting for the current run' : task.model,
      state: task.status === 'queued' ? 'idle' : 'running',
      phase: 'generate',
      durationMs: null,
      active: true,
    })
    return entries
  }

  for (const attempt of task.attempts) {
    entries.push(generationEntry(attempt))
    for (const result of attempt.results) entries.push(resultEntry(attempt, result))

    if (attempt.status === 'succeeded') {
      entries.push({
        id: `${attempt.id}-passed`,
        attempt: attempt.number,
        label: 'Passed',
        detail: `${attempt.results.length} success gates`,
        state: 'pass',
        phase: attempt.phase,
        durationMs: attempt.duration_ms,
        active: false,
      })
    } else if (attempt.status === 'failed') {
      const diagnosis = diagnoseAttempt(attempt)
      entries.push({
        id: `${attempt.id}-failed`,
        attempt: attempt.number,
        label: 'Failed',
        detail: diagnosisLabel(diagnosis),
        state: 'fail',
        phase: attempt.phase,
        durationMs: attempt.duration_ms,
        active: false,
      })
    }
  }

  const activeAttempt = task.attempts.find((attempt) => attempt.number === task.active_attempt)
  if (!taskIsTerminal(task) && task.status !== 'generating' && task.status !== 'queued') {
    const attemptNumber = task.active_attempt || task.attempts.at(-1)?.number || 1
    const lastEntry = entries.at(-1)
    const activeLabel = activePhaseLabel(task.status)
    const previousFailure = [...task.attempts].reverse().find((attempt) => attempt.status === 'failed')
    const diagnosis = diagnoseAttempt(previousFailure)
    const detail =
      task.status === 'repairing'
        ? diagnosisLabel(diagnosis)
        : activeAttempt?.phase || task.resolved_runtime || task.runtime

    if (!lastEntry?.active || lastEntry.label !== activeLabel) {
      entries.push({
        id: `active-${attemptNumber}-${task.status}`,
        attempt: attemptNumber,
        label: activeLabel,
        detail,
        state: 'running',
        phase: task.status,
        durationMs: null,
        active: true,
      })
    }
  }

  return entries
}

export function mergeWorkspaceFiles(
  current: Array<{ path: string; size: number }>,
  previous: Array<{ path: string; size: number }>,
  changed: string[],
): WorkspaceFile[] {
  const currentMap = new Map(current.map((file) => [file.path, file]))
  const previousMap = new Map(previous.map((file) => [file.path, file]))
  const paths = new Set([...currentMap.keys(), ...changed.filter((path) => previousMap.has(path))])
  return [...paths]
    .sort((a, b) => a.localeCompare(b))
    .map((path) => {
      const currentFile = currentMap.get(path)
      const previousFile = previousMap.get(path)
      let change: WorkspaceFile['change'] = null
      if (changed.includes(path)) {
        change = !currentFile ? 'deleted' : !previousFile ? 'added' : 'modified'
      }
      return { path, size: currentFile?.size || previousFile?.size || 0, change }
    })
}

function generationEntry(attempt: Attempt): AgentLogEntry {
  const repaired = attempt.number > 1
  return {
    id: `${attempt.id}-generation`,
    attempt: attempt.number,
    label: repaired ? 'Repaired code' : 'Wrote code',
    detail: `${attempt.changed_files.length} file${attempt.changed_files.length === 1 ? '' : 's'} changed`,
    state: 'pass',
    phase: repaired ? 'repair' : 'generate',
    durationMs: null,
    active: false,
  }
}

function resultEntry(attempt: Attempt, result: CommandResult): AgentLogEntry {
  const label =
    result.phase === 'install'
      ? 'Prepared sandbox'
      : result.phase === 'test'
        ? 'Checked result'
        : result.phase === 'judge'
          ? 'Judged behavior'
          : result.phase === 'healthcheck'
            ? 'Checked health endpoint'
            : 'Ran in sandbox'
  return {
    id: `${attempt.id}-${result.phase}-${result.duration_ms}`,
    attempt: attempt.number,
    label,
    detail: result.timed_out ? 'Timed out' : `Exit ${result.exit_code ?? '—'}`,
    state: result.exit_code === 0 && !result.timed_out ? 'pass' : 'fail',
    phase: result.phase,
    durationMs: result.duration_ms,
    active: false,
  }
}
