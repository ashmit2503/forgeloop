import { diffLines as compareLines } from 'diff'
import type { DiffLine } from '../types/workspace'

export function createLineDiff(previous: string | null, current: string | null): DiffLine[] {
  const before = previous ?? ''
  const after = current ?? ''
  if (!before && !after) return []

  let oldLine = 1
  let newLine = 1
  const output: DiffLine[] = []

  for (const part of compareLines(before, after)) {
    const lines = splitPart(part.value)
    for (const content of lines) {
      if (part.added) {
        output.push({ kind: 'added', oldLine: null, newLine, content })
        newLine += 1
      } else if (part.removed) {
        output.push({ kind: 'removed', oldLine, newLine: null, content })
        oldLine += 1
      } else {
        output.push({ kind: 'context', oldLine, newLine, content })
        oldLine += 1
        newLine += 1
      }
    }
  }

  return output
}

export function sourceLines(
  content: string | null,
  limit?: number,
): Array<{ number: number; content: string }> {
  if (content === null) return []
  return content.split('\n', limit).map((line, index) => ({ number: index + 1, content: line }))
}

function splitPart(value: string): string[] {
  const lines = value.split('\n')
  if (lines.at(-1) === '') lines.pop()
  return lines.length ? lines : ['']
}
