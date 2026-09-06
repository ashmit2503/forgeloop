import type { Attempt } from '../types/api'
import type { AttemptDiagnosis } from '../types/workspace'

const pythonExceptionPattern = /^(?:E\s+)?([A-Za-z_][\w.]*(?:Error|Exception)):\s*(.+)$/gm
const genericExceptionPattern = /^(TypeError|ReferenceError|SyntaxError|RangeError|Error):\s*(.+)$/gm
const pytestInlineExceptionPattern =
  /(?:^|\n)(?:E\s+)?[^:\n]+:\d+(?::\d+)?:\s*([A-Z]\w*(?:Error|Exception)):\s*(.+)$/gm
const pythonFilePattern = /File ["']([^"']+)["'], line (\d+)/g
const pythonColonFilePattern = /(?:^|\n)(?:E\s+)?([^()\s:]+\.py):(\d+)(?::\d+)?:/g
const nodeFilePattern = /\(?([^()\s]+\.(?:js|jsx|ts|tsx|mjs|cjs)):(\d+)(?::\d+)?\)?/g

export function diagnoseAttempt(attempt: Attempt | null | undefined): AttemptDiagnosis | null {
  if (!attempt || (!attempt.failure_message && !attempt.stderr && !attempt.stdout)) return null

  const output = `${attempt.stderr}\n${attempt.stdout}`.trim()
  const exceptionMatch = latestMatch(output, [
    pythonExceptionPattern,
    genericExceptionPattern,
    pytestInlineExceptionPattern,
  ])
  const fileMatch = latestMatch(output, [pythonFilePattern, pythonColonFilePattern, nodeFilePattern])
  const fallbackMessage = attempt.failure_message || 'The run did not pass its success gate.'
  const exception = exceptionMatch?.[1] || null
  const exceptionDetail = exceptionMatch?.[2]?.trim() || ''
  const message = exception ? `${exception}${exceptionDetail ? `: ${exceptionDetail}` : ''}` : fallbackMessage
  const file = fileMatch?.[1] ? trimWorkspacePrefix(fileMatch[1]) : null
  const parsedLine = fileMatch?.[2] ? Number(fileMatch[2]) : null

  return {
    exception,
    message,
    file,
    line: Number.isFinite(parsedLine) ? parsedLine : null,
    phase: attempt.phase,
    exitCode: attempt.exit_code,
    timedOut: attempt.timed_out,
    detail: fallbackMessage,
  }
}

export function diagnosisLabel(diagnosis: AttemptDiagnosis | null): string {
  if (!diagnosis) return 'Run failed'
  const location = diagnosis.line ? ` on line ${diagnosis.line}` : ''
  if (diagnosis.exception) return `${diagnosis.exception}${location}`
  if (diagnosis.timedOut) return `${capitalize(diagnosis.phase || 'run')} timed out`
  return diagnosis.message
}

function lastMatch(value: string, pattern: RegExp): RegExpMatchArray | null {
  pattern.lastIndex = 0
  let latest: RegExpMatchArray | null = null
  for (const match of value.matchAll(pattern)) latest = match
  return latest
}

function latestMatch(value: string, patterns: RegExp[]): RegExpMatchArray | null {
  let latest: RegExpMatchArray | null = null
  for (const pattern of patterns) {
    const match = lastMatch(value, pattern)
    if (match && (latest?.index ?? -1) < (match.index ?? -1)) latest = match
  }
  return latest
}

function trimWorkspacePrefix(value: string): string {
  const normalized = value.replaceAll('\\', '/')
  const workspaceIndex = normalized.lastIndexOf('/workspace/')
  return workspaceIndex >= 0 ? normalized.slice(workspaceIndex + '/workspace/'.length) : normalized
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}
