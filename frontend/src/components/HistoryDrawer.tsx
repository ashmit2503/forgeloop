import { CaretRight } from '@phosphor-icons/react/dist/csr/CaretRight'
import { Clock } from '@phosphor-icons/react/dist/csr/Clock'
import { Plus } from '@phosphor-icons/react/dist/csr/Plus'
import { X } from '@phosphor-icons/react/dist/csr/X'
import { useEffect, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import type { Task } from '../types/api'
import { formatRelativeTime, statusLabelFor, visualStateFor } from '../lib/runModel'
import { StatusPill } from './StatusPill'

export function HistoryDrawer({
  open,
  tasks,
  selectedId,
  hasMore,
  loadingMore,
  onLoadMore,
  onClose,
  onNew,
  onSelect,
  onResize,
  onResizeKeyDown,
}: {
  open: boolean
  tasks: Task[]
  selectedId: string | null
  hasMore: boolean
  loadingMore: boolean
  onLoadMore: () => void
  onClose: () => void
  onNew: () => void
  onSelect: (id: string) => void
  onResize: (event: PointerEvent<HTMLElement>) => void
  onResizeKeyDown: (event: KeyboardEvent<HTMLElement>) => void
}) {
  const [, setTimeTick] = useState(0)
  useEffect(() => {
    if (!open) return
    const timer = window.setInterval(() => setTimeTick((value) => value + 1), 60_000)
    return () => window.clearInterval(timer)
  }, [open])

  if (!open) return null
  return (
    <aside className="history-drawer" aria-labelledby="history-title">
      <header>
        <div>
          <span className="panel-eyebrow">Workspace history</span>
          <h2 id="history-title">Recent runs</h2>
        </div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Collapse run history">
          <X aria-hidden="true" />
        </button>
      </header>
      <button className="new-run-button" type="button" onClick={onNew}>
        <Plus aria-hidden="true" />
        New task
      </button>
      <nav className="history-list" aria-label="Recent tasks">
        {tasks.length === 0 ? (
          <p className="history-empty">Completed and active runs will appear here.</p>
        ) : null}
        {tasks.map((task) => (
          <button
            className={`history-row${selectedId === task.id ? ' selected' : ''}`}
            type="button"
            key={task.id}
            onClick={() => onSelect(task.id)}
          >
            <span className="history-row-title">{task.title || task.prompt}</span>
            <span className="history-row-meta">
              <StatusPill state={visualStateFor(task.status)} label={statusLabelFor(task.status)} compact />
              <span>
                <Clock aria-hidden="true" />
                {formatRelativeTime(task.created_at)}
              </span>
              <span>{task.resolved_runtime || task.runtime}</span>
            </span>
            <CaretRight aria-hidden="true" />
          </button>
        ))}
        {hasMore ? (
          <button className="history-load-more" type="button" disabled={loadingMore} onClick={onLoadMore}>
            {loadingMore ? 'Loading…' : 'Load older runs'}
          </button>
        ) : null}
      </nav>
      <div
        className="history-resizer"
        role="separator"
        aria-label="Resize run history"
        aria-orientation="vertical"
        tabIndex={0}
        data-resize-direction="right"
        onPointerDown={onResize}
        onKeyDown={onResizeKeyDown}
      />
    </aside>
  )
}
