import { Check } from '@phosphor-icons/react/dist/csr/Check'
import { Scroll } from '@phosphor-icons/react/dist/csr/Scroll'
import { TextAlignLeft } from '@phosphor-icons/react/dist/csr/TextAlignLeft'
import { Warning } from '@phosphor-icons/react/dist/csr/Warning'
import { X } from '@phosphor-icons/react/dist/csr/X'
import { XCircle } from '@phosphor-icons/react/dist/csr/XCircle'
import { useEffect, useRef, useState } from 'react'
import type { Attempt, TaskStatus } from '../types/api'
import { terminalStatuses } from '../types/api'
import { diagnosisLabel } from '../lib/diagnosis'
import { formatDuration } from '../lib/runModel'
import type { AttemptDiagnosis } from '../types/workspace'

export function OutputWorkspace({
  attempt,
  taskStatus,
  stdout,
  stderr,
  diagnosis,
  onDismissDiagnosis,
}: {
  attempt: Attempt | null
  taskStatus: TaskStatus
  stdout: string
  stderr: string
  diagnosis: AttemptDiagnosis | null
  onDismissDiagnosis: () => void
}) {
  const [stream, setStream] = useState<'stdout' | 'stderr'>(() => (stderr ? 'stderr' : 'stdout'))
  const [follow, setFollow] = useState(true)
  const [wrap, setWrap] = useState(true)
  const terminal = useRef<HTMLPreElement>(null)
  const autoSelectedState = useRef<string | null>(null)
  const attemptId = attempt?.id ?? null
  const attemptStatus = attempt?.status ?? null
  const content = stream === 'stdout' ? stdout : stderr
  const streamTruncated = stream === 'stdout' ? attempt?.stdout_truncated : attempt?.stderr_truncated

  useEffect(() => {
    const stateKey = attemptId ? `${attemptId}:${attemptStatus}` : null
    if (stateKey !== autoSelectedState.current) {
      autoSelectedState.current = stateKey
      setStream(attemptStatus === 'failed' && stderr ? 'stderr' : 'stdout')
    }
  }, [attemptId, attemptStatus, stderr])

  useEffect(() => {
    if (follow && terminal.current) terminal.current.scrollTop = terminal.current.scrollHeight
  }, [content, follow])

  const waitingForOutput = !attempt && !terminalStatuses.includes(taskStatus)
  if (waitingForOutput) return <OutputSkeleton />

  return (
    <section className="output-workspace" aria-label="Sandbox output">
      {diagnosis ? <DiagnosisBanner diagnosis={diagnosis} onDismiss={onDismissDiagnosis} /> : null}
      {attempt?.results.length ? (
        <div className="phase-strip" aria-label="Success gates">
          {attempt.results.map((result, index) => {
            const passed = result.exit_code === 0 && !result.timed_out
            return (
              <span className={passed ? 'passed' : 'failed'} key={`${result.phase}-${index}`}>
                {passed ? <Check aria-hidden="true" /> : <XCircle aria-hidden="true" />}
                <span>
                  <strong>{phaseTitle(result.phase)}</strong>
                  <small>
                    {result.timed_out
                      ? 'Timed out'
                      : `Exit ${result.exit_code ?? '—'} · ${formatDuration(result.duration_ms)}`}
                  </small>
                </span>
              </span>
            )
          })}
        </div>
      ) : null}
      <div className="output-toolbar">
        <div className="stream-tabs" role="tablist" aria-label="Output stream">
          <button
            id="output-tab-stdout"
            className={stream === 'stdout' ? 'active' : ''}
            type="button"
            role="tab"
            aria-controls="output-stream-panel"
            aria-selected={stream === 'stdout'}
            onClick={() => setStream('stdout')}
          >
            stdout <span>{countLines(stdout)}</span>
          </button>
          <button
            id="output-tab-stderr"
            className={stream === 'stderr' ? 'active' : ''}
            type="button"
            role="tab"
            aria-controls="output-stream-panel"
            aria-selected={stream === 'stderr'}
            onClick={() => setStream('stderr')}
          >
            stderr <span>{countLines(stderr)}</span>
          </button>
        </div>
        <div className="output-options">
          <button
            className={follow ? 'active' : ''}
            type="button"
            onClick={() => setFollow((value) => !value)}
            aria-pressed={follow}
          >
            <Scroll aria-hidden="true" />
            Follow
          </button>
          <button
            className={wrap ? 'active' : ''}
            type="button"
            onClick={() => setWrap((value) => !value)}
            aria-pressed={wrap}
          >
            <TextAlignLeft aria-hidden="true" />
            Wrap
          </button>
        </div>
      </div>
      <pre
        id="output-stream-panel"
        className={wrap ? 'wrap' : ''}
        ref={terminal}
        role="tabpanel"
        aria-labelledby={`output-tab-${stream}`}
      >
        {content || <span className="empty-output">No {stream} output for this attempt.</span>}
        {streamTruncated ? (
          <span className="output-truncation-notice">{`\n\n[${stream} truncated at 1 MiB]`}</span>
        ) : null}
      </pre>
      <footer className="output-footer">
        <span>{attempt ? `Attempt ${attempt.number}` : 'Waiting'}</span>
        <span>{streamTruncated ? `${stream} truncated at 1 MiB` : 'Complete captured stream'}</span>
        <span>{attempt?.timed_out ? 'Timed out' : `Exit ${attempt?.exit_code ?? '—'}`}</span>
      </footer>
    </section>
  )
}

function DiagnosisBanner({ diagnosis, onDismiss }: { diagnosis: AttemptDiagnosis; onDismiss: () => void }) {
  return (
    <div className="diagnosis-banner">
      <details>
        <summary>
          <Warning weight="fill" aria-hidden="true" />
          <strong>{diagnosisLabel(diagnosis)}</strong>
          <span>{diagnosis.message}</span>
          <span className="diagnosis-expand">Details</span>
        </summary>
        <div>
          <dl>
            <div>
              <dt>Phase</dt>
              <dd>{diagnosis.phase || 'unknown'}</dd>
            </div>
            <div>
              <dt>Location</dt>
              <dd>
                {diagnosis.file
                  ? `${diagnosis.file}${diagnosis.line ? `:${diagnosis.line}` : ''}`
                  : 'Not reported'}
              </dd>
            </div>
            <div>
              <dt>Exit</dt>
              <dd>{diagnosis.timedOut ? 'Timed out' : (diagnosis.exitCode ?? '—')}</dd>
            </div>
          </dl>
          <p>{diagnosis.detail}</p>
        </div>
      </details>
      <button
        className="diagnosis-banner-dismiss"
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss diagnosis"
      >
        <X aria-hidden="true" />
      </button>
    </div>
  )
}

function OutputSkeleton() {
  return (
    <div className="output-skeleton" aria-label="Waiting for sandbox output">
      <span />
      <span />
      <span />
      <span />
      <span />
      <p>The first sandbox output will appear here.</p>
    </div>
  )
}

function phaseTitle(phase: string) {
  if (phase === 'install') return 'Install'
  if (phase === 'test') return 'Tests'
  if (phase === 'healthcheck') return 'Health'
  if (phase === 'judge') return 'Judge'
  return 'Execute'
}

function countLines(value: string) {
  return value ? value.split('\n').length - (value.endsWith('\n') ? 1 : 0) : 0
}
