import { describe, expect, it } from 'vitest'
import { createLineDiff, sourceLines } from './diff'

describe('workspace diff', () => {
  it('tracks old and new line numbers for a replacement', () => {
    const lines = createLineDiff('one\ntwo\n', 'one\nthree\n')
    expect(lines.map((line) => [line.kind, line.oldLine, line.newLine, line.content])).toEqual([
      ['context', 1, 1, 'one'],
      ['removed', 2, null, 'two'],
      ['added', null, 2, 'three'],
    ])
  })

  it('returns a numbered source view', () => {
    expect(sourceLines('alpha\nbeta')).toEqual([
      { number: 1, content: 'alpha' },
      { number: 2, content: 'beta' },
    ])
  })
})
