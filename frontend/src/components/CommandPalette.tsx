import { ClockCounterClockwise } from '@phosphor-icons/react/dist/csr/ClockCounterClockwise'
import { Code } from '@phosphor-icons/react/dist/csr/Code'
import { MagnifyingGlass } from '@phosphor-icons/react/dist/csr/MagnifyingGlass'
import { Plus } from '@phosphor-icons/react/dist/csr/Plus'
import { SidebarSimple } from '@phosphor-icons/react/dist/csr/SidebarSimple'
import { Stop } from '@phosphor-icons/react/dist/csr/Stop'
import { TerminalWindow } from '@phosphor-icons/react/dist/csr/TerminalWindow'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Icon } from '@phosphor-icons/react'
import type { TaskDetail } from '../types/api'
import { terminalStatuses } from '../types/api'
import type { WorkspaceTab } from '../types/workspace'

interface PaletteAction {
  id: string
  label: string
  detail: string
  icon: Icon
  run: () => void
}

export function CommandPalette({
  open,
  task,
  onClose,
  onNew,
  onTab,
  onAttempt,
  onToggleLog,
  onCancel,
}: {
  open: boolean
  task: TaskDetail | null
  onClose: () => void
  onNew: () => void
  onTab: (tab: WorkspaceTab) => void
  onAttempt: (attempt: number) => void
  onToggleLog: () => void
  onCancel: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const actions = useMemo<PaletteAction[]>(() => {
    const base: PaletteAction[] = [
      { id: 'new', label: 'New task', detail: 'Start another autonomous run', icon: Plus, run: onNew },
    ]
    if (!task) return base
    base.push(
      {
        id: 'code',
        label: 'Show Code',
        detail: 'Open the generated workspace',
        icon: Code,
        run: () => onTab('code'),
      },
      {
        id: 'output',
        label: 'Show Output',
        detail: 'Inspect stdout and stderr',
        icon: TerminalWindow,
        run: () => onTab('output'),
      },
      {
        id: 'log',
        label: 'Toggle Agent',
        detail: 'Expand or collapse run activity',
        icon: SidebarSimple,
        run: onToggleLog,
      },
    )
    if (!terminalStatuses.includes(task.status)) {
      base.push({
        id: 'stop',
        label: 'Stop task',
        detail: 'Cancel the active model or sandbox',
        icon: Stop,
        run: onCancel,
      })
    }
    for (const attempt of task.attempts) {
      base.push({
        id: `attempt-${attempt.number}`,
        label: `Open Attempt ${attempt.number}`,
        detail: attempt.failure_message || attempt.summary || attempt.status,
        icon: ClockCounterClockwise,
        run: () => onAttempt(attempt.number),
      })
    }
    return base
  }, [onAttempt, onCancel, onNew, onTab, onToggleLog, task])
  const filtered = useMemo(
    () =>
      query
        ? actions.filter((action) =>
            `${action.label} ${action.detail}`.toLowerCase().includes(query.toLowerCase()),
          )
        : actions,
    [actions, query],
  )

  useEffect(() => {
    if (open && !dialog.current?.open) {
      setQuery('')
      setActiveIndex(0)
      dialog.current?.showModal()
    }
    if (!open && dialog.current?.open) dialog.current.close()
  }, [open])

  useEffect(() => setActiveIndex(0), [query])

  const select = (action: PaletteAction) => {
    action.run()
    onClose()
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLDialogElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((current) => (filtered.length ? (current + 1) % filtered.length : 0))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((current) => (filtered.length ? (current - 1 + filtered.length) % filtered.length : 0))
    } else if (event.key === 'Enter' && filtered[activeIndex]) {
      event.preventDefault()
      select(filtered[activeIndex])
    }
  }

  return (
    <dialog
      className="command-palette"
      ref={dialog}
      aria-label="Command palette"
      onCancel={onClose}
      onClose={onClose}
      onKeyDown={onKeyDown}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <label className="palette-search">
        <MagnifyingGlass aria-hidden="true" />
        <span className="sr-only">Search commands</span>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Type a command or attempt…"
          aria-label="Search commands"
          aria-activedescendant={filtered[activeIndex] ? `command-${filtered[activeIndex].id}` : undefined}
          autoFocus
        />
        <kbd aria-hidden="true">Esc</kbd>
      </label>
      <div className="palette-results" role="listbox" aria-label="Available commands">
        {filtered.map((action, index) => {
          const Icon = action.icon
          return (
            <button
              id={`command-${action.id}`}
              className={index === activeIndex ? 'active' : ''}
              type="button"
              role="option"
              aria-selected={index === activeIndex}
              key={action.id}
              onMouseMove={() => setActiveIndex(index)}
              onClick={() => select(action)}
            >
              <span className="palette-icon">
                <Icon aria-hidden="true" />
              </span>
              <span>
                <strong>{action.label}</strong>
                <small>{action.detail}</small>
              </span>
            </button>
          )
        })}
        {filtered.length === 0 ? <p>No matching commands.</p> : null}
      </div>
      <footer>
        <span>Navigate with keyboard</span>
        <span>
          <kbd>↵</kbd> Select
        </span>
      </footer>
    </dialog>
  )
}
