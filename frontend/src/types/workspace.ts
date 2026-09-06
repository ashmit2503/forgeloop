import type { CommandResult } from './api'

export type VisualRunState = 'idle' | 'running' | 'pass' | 'fail'
export type WorkspaceTab = 'code' | 'output'

export interface AgentLogEntry {
  id: string
  attempt: number | null
  label: string
  detail: string | null
  state: VisualRunState
  phase: string | null
  durationMs: number | null
  active: boolean
}

export interface AttemptDiagnosis {
  exception: string | null
  message: string
  file: string | null
  line: number | null
  phase: string | null
  exitCode: number | null
  timedOut: boolean
  detail: string
}

export interface WorkspaceFile {
  path: string
  size: number
  change: 'added' | 'modified' | 'deleted' | null
}

export interface DiffLine {
  kind: 'context' | 'added' | 'removed'
  oldLine: number | null
  newLine: number | null
  content: string
}

export interface OutputPhaseSummary extends CommandResult {
  succeeded: boolean
}
