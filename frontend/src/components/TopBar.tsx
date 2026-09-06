import { CaretDown } from '@phosphor-icons/react/dist/csr/CaretDown'
import { Clock } from '@phosphor-icons/react/dist/csr/Clock'
import { ClockCounterClockwise } from '@phosphor-icons/react/dist/csr/ClockCounterClockwise'
import { CubeFocus } from '@phosphor-icons/react/dist/csr/CubeFocus'
import { DotsThree as Ellipsis } from '@phosphor-icons/react/dist/csr/DotsThree'
import { DownloadSimple } from '@phosphor-icons/react/dist/csr/DownloadSimple'
import { Flask } from '@phosphor-icons/react/dist/csr/Flask'
import { Plus } from '@phosphor-icons/react/dist/csr/Plus'
import { Stop } from '@phosphor-icons/react/dist/csr/Stop'
import { Trash } from '@phosphor-icons/react/dist/csr/Trash'
import { X } from '@phosphor-icons/react/dist/csr/X'
import { useEffect, useRef, useState } from 'react'
import type { TaskDetail } from '../types/api'
import { formatClock, statusLabelFor, taskIsTerminal, visualStateFor } from '../lib/runModel'
import { StatusPill } from './StatusPill'

export function TopBar({
  task,
  elapsed,
  onHistory,
  onNew,
  onCancel,
  onDelete,
  onBenchmarks,
}: {
  task: TaskDetail | null
  elapsed: number
  onHistory: () => void
  onNew: () => void
  onCancel: () => void
  onDelete: () => void
  onBenchmarks: () => void
}) {
  const [promptOpen, setPromptOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const promptWrap = useRef<HTMLDivElement>(null)
  const overflowWrap = useRef<HTMLDivElement>(null)
  const terminal = task ? taskIsTerminal(task) : false
  const attempt = task?.active_attempt || task?.attempts.at(-1)?.number || 0
  const totalAttempts = task ? task.max_retries + 1 : 0

  useEffect(() => {
    setPromptOpen(false)
    setMenuOpen(false)
  }, [task?.id])

  useEffect(() => {
    if (!promptOpen && !menuOpen) return
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Node
      if (promptOpen && !promptWrap.current?.contains(target)) setPromptOpen(false)
      if (menuOpen && !overflowWrap.current?.contains(target)) setMenuOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setPromptOpen(false)
        setMenuOpen(false)
      }
    }
    window.addEventListener('pointerdown', dismiss)
    window.addEventListener('keydown', escape)
    return () => {
      window.removeEventListener('pointerdown', dismiss)
      window.removeEventListener('keydown', escape)
    }
  }, [menuOpen, promptOpen])

  return (
    <header className={`topbar${task ? ' has-task' : ''}`}>
      <button className="brand-lockup" type="button" onClick={onNew} aria-label="ForgeLoop home">
        <span className="brand-icon">
          <CubeFocus weight="regular" aria-hidden="true" />
        </span>
        <span className="brand-name">ForgeLoop</span>
      </button>
      <button className="history-button runs-button" type="button" onClick={onHistory} aria-label="Runs">
        <ClockCounterClockwise aria-hidden="true" />
        <span>Runs</span>
      </button>
      <button
        className="history-button benchmarks-button"
        type="button"
        onClick={onBenchmarks}
        aria-label="Benchmarks"
      >
        <Flask aria-hidden="true" />
        <span>Benchmarks</span>
      </button>
      {task ? (
        <div className="task-summary-wrap" ref={promptWrap}>
          <button
            className="task-summary"
            type="button"
            onClick={() => setPromptOpen((open) => !open)}
            aria-expanded={promptOpen}
            aria-controls="task-details-popover"
          >
            <span className="task-text">{task.prompt}</span>
            <CaretDown aria-hidden="true" />
          </button>
          {promptOpen ? (
            <section className="task-popover" id="task-details-popover" aria-label="Task details">
              <header>
                <span>Task prompt</span>
                <button
                  className="icon-button compact"
                  type="button"
                  onClick={() => setPromptOpen(false)}
                  aria-label="Close task details"
                >
                  <X aria-hidden="true" />
                </button>
              </header>
              <p>{task.prompt}</p>
              <dl>
                <div>
                  <dt>Runtime</dt>
                  <dd>{task.resolved_runtime || task.runtime}</dd>
                </div>
                <div>
                  <dt>Model</dt>
                  <dd>{task.model}</dd>
                </div>
                <div>
                  <dt>Sandbox</dt>
                  <dd>{task.sandbox_provider}</dd>
                </div>
              </dl>
            </section>
          ) : null}
        </div>
      ) : (
        <div className="topbar-spacer" />
      )}
      <div className="run-meta">
        {task ? <StatusPill state={visualStateFor(task.status)} label={statusLabelFor(task.status)} /> : null}
        {task ? (
          <span
            className="attempt-count"
            title="Current attempt"
            aria-label={
              attempt
                ? `Attempt ${attempt} of ${totalAttempts}`
                : `Attempts not started; ${totalAttempts} available`
            }
          >
            {attempt || '—'} / {totalAttempts}
          </span>
        ) : null}
        {task ? (
          <span className="elapsed" aria-label={`Elapsed time ${formatClock(elapsed)}`}>
            <Clock aria-hidden="true" />
            {formatClock(elapsed)}
          </span>
        ) : null}
        {task && !terminal ? (
          <button className="stop-button" type="button" onClick={onCancel} disabled={task.cancel_requested}>
            <Stop weight="fill" aria-hidden="true" />
            {task.cancel_requested ? 'Stopping' : 'Stop'}
          </button>
        ) : null}
        {task?.attempts.length && terminal ? (
          <a className="icon-button" href={`/api/tasks/${task.id}/download`} aria-label="Download workspace">
            <DownloadSimple aria-hidden="true" />
          </a>
        ) : null}
        {task ? (
          <div className="overflow-wrap" ref={overflowWrap}>
            <button
              className="icon-button"
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              aria-label="More actions"
              aria-expanded={menuOpen}
              aria-controls="task-overflow-menu"
            >
              <Ellipsis aria-hidden="true" />
            </button>
            {menuOpen ? (
              <div className="overflow-menu" id="task-overflow-menu">
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false)
                    onNew()
                  }}
                >
                  <Plus aria-hidden="true" />
                  New task
                </button>
                {task.attempts.length && terminal ? (
                  <a href={`/api/tasks/${task.id}/download`} onClick={() => setMenuOpen(false)}>
                    <DownloadSimple aria-hidden="true" />
                    Download ZIP
                  </a>
                ) : null}
                {terminal ? (
                  <button
                    className="danger-item"
                    type="button"
                    onClick={() => {
                      setMenuOpen(false)
                      onDelete()
                    }}
                  >
                    <Trash aria-hidden="true" />
                    Delete task
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </header>
  )
}
