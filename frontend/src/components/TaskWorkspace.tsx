import { Code } from '@phosphor-icons/react/dist/csr/Code'
import { Copy } from '@phosphor-icons/react/dist/csr/Copy'
import { MagnifyingGlass } from '@phosphor-icons/react/dist/csr/MagnifyingGlass'
import { Pulse } from '@phosphor-icons/react/dist/csr/Pulse'
import { TerminalWindow } from '@phosphor-icons/react/dist/csr/TerminalWindow'
import { X } from '@phosphor-icons/react/dist/csr/X'
import { useMemo, useState } from 'react'
import type { Attempt, TaskDetail } from '../types/api'
import { diagnoseAttempt } from '../lib/diagnosis'
import { useResizableWidth } from '../hooks/useWorkspaceLayout'
import { AgentLog } from './AgentLog'
import { CodeWorkspace } from './CodeWorkspace'
import { OutputWorkspace } from './OutputWorkspace'
import { activePhaseLabel } from '../lib/runModel'
import type { WorkspaceFile, WorkspaceTab } from '../types/workspace'

export function TaskWorkspace({
  task,
  attempt,
  files,
  selectedFile,
  currentContent,
  previousContent,
  fileLoading,
  stdout,
  stderr,
  activeTab,
  agentLogCollapsed,
  onTab,
  onAttempt,
  onFile,
  onToggleAgentLog,
  onToast,
  liveFileStreaming = false,
}: {
  task: TaskDetail
  attempt: Attempt | null
  files: WorkspaceFile[]
  selectedFile: string | null
  currentContent: string | null
  previousContent: string | null
  fileLoading: boolean
  stdout: string
  stderr: string
  activeTab: WorkspaceTab
  agentLogCollapsed: boolean
  onTab: (tab: WorkspaceTab) => void
  onAttempt: (attempt: number) => void
  onFile: (path: string) => void
  onToggleAgentLog: () => void
  onToast: (message: string, tone?: 'success' | 'error' | 'info') => void
  liveFileStreaming?: boolean
}) {
  const [searchOpen, setSearchOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const [mobileLogOpen, setMobileLogOpen] = useState(false)
  const [dismissedDiagnosis, setDismissedDiagnosis] = useState<string | null>(null)
  const agentPanel = useResizableWidth('coding-sandbox:agent-log-width:v2', 380, 300, 560)
  const previousFailure = useMemo(
    () =>
      [...task.attempts]
        .reverse()
        .find(
          (item) => item.status === 'failed' && item.number < (attempt?.number || Number.POSITIVE_INFINITY),
        ),
    [attempt?.number, task.attempts],
  )
  const diagnosis = diagnoseAttempt(attempt?.status === 'failed' ? attempt : previousFailure)
  const diagnosisKey = diagnosis ? `${attempt?.id || previousFailure?.id}:${diagnosis.message}` : null
  const visibleDiagnosis = diagnosisKey === dismissedDiagnosis ? null : diagnosis
  const showRelevantOutput = () => {
    if (attempt?.status !== 'failed' && previousFailure) onAttempt(previousFailure.number)
    onTab('output')
  }
  const showAttemptOutput = (attemptNumber: number) => {
    onAttempt(attemptNumber)
    onTab('output')
  }

  const copyContent = async () => {
    if (currentContent === null) return
    try {
      await navigator.clipboard.writeText(currentContent)
      onToast('File copied to clipboard.')
    } catch {
      onToast('Could not copy this file.', 'error')
    }
  }

  return (
    <main
      className={`task-workspace${agentLogCollapsed ? ' log-collapsed' : ''}`}
      style={{ '--agent-log-width': `${agentLogCollapsed ? 44 : agentPanel.width}px` } as React.CSSProperties}
    >
      <section className="workspace" aria-label="Generated workspace">
        <div className="workspace-toolbar">
          <div className="workspace-tabs" role="tablist" aria-label="Workspace view">
            <button
              id="workspace-tab-code"
              className={`workspace-tab${activeTab === 'code' ? ' active' : ''}`}
              type="button"
              role="tab"
              aria-controls="workspace-tab-panel"
              aria-selected={activeTab === 'code'}
              onClick={() => onTab('code')}
            >
              <Code aria-hidden="true" />
              Code
            </button>
            <button
              id="workspace-tab-output"
              className={`workspace-tab${activeTab === 'output' ? ' active' : ''}`}
              type="button"
              role="tab"
              aria-controls="workspace-tab-panel"
              aria-selected={activeTab === 'output'}
              onClick={() => onTab('output')}
            >
              <TerminalWindow aria-hidden="true" />
              Output
              {stderr ? <span className="tab-count">{stderr.split('\n').filter(Boolean).length}</span> : null}
            </button>
          </div>
          <div className="toolbar-actions">
            {searchOpen ? (
              <label className="file-filter">
                <span className="sr-only">Filter files</span>
                <MagnifyingGlass aria-hidden="true" />
                <input
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                  placeholder="Filter files"
                  autoFocus
                />
                <button
                  type="button"
                  onClick={() => {
                    setSearchOpen(false)
                    setFilter('')
                  }}
                  aria-label="Close file filter"
                >
                  <X aria-hidden="true" />
                </button>
              </label>
            ) : (
              <button
                className="toolbar-button icon-only"
                type="button"
                onClick={() => setSearchOpen(true)}
                aria-label="Find a file"
              >
                <MagnifyingGlass aria-hidden="true" />
              </button>
            )}
            <button
              className="toolbar-button icon-only"
              type="button"
              onClick={() => void copyContent()}
              disabled={currentContent === null}
              aria-label="Copy file"
            >
              <Copy aria-hidden="true" />
            </button>
            <button
              className="toolbar-button icon-only mobile-activity-button"
              type="button"
              onClick={() => setMobileLogOpen(true)}
              aria-label="Open activity"
            >
              <Pulse aria-hidden="true" />
            </button>
          </div>
        </div>
        <div
          id="workspace-tab-panel"
          className="workspace-tab-panel"
          role="tabpanel"
          aria-labelledby={`workspace-tab-${activeTab}`}
        >
          {activeTab === 'code' ? (
            <CodeWorkspace
              attempt={attempt}
              files={files}
              selectedFile={selectedFile}
              currentContent={currentContent}
              previousContent={previousContent}
              loading={fileLoading}
              liveStreaming={liveFileStreaming}
              filter={filter}
              diagnosis={visibleDiagnosis}
              onDismissDiagnosis={() => setDismissedDiagnosis(diagnosisKey)}
              onFile={onFile}
              onShowOutput={showRelevantOutput}
            />
          ) : (
            <OutputWorkspace
              attempt={attempt}
              taskStatus={task.status}
              stdout={stdout}
              stderr={stderr}
              diagnosis={visibleDiagnosis}
              onDismissDiagnosis={() => setDismissedDiagnosis(diagnosisKey)}
            />
          )}
        </div>
      </section>
      {!agentLogCollapsed ? (
        <div
          className="panel-resizer agent-resizer"
          role="separator"
          aria-label="Resize Agent Log"
          aria-orientation="vertical"
          tabIndex={0}
          data-resize-direction="left"
          onPointerDown={agentPanel.beginResize}
          onKeyDown={agentPanel.resizeWithKeyboard}
        />
      ) : null}
      <AgentLog
        task={task}
        selectedAttempt={attempt?.number || null}
        collapsed={agentLogCollapsed}
        mobileOpen={mobileLogOpen}
        onToggle={onToggleAgentLog}
        onMobileClose={() => setMobileLogOpen(false)}
        onAttempt={onAttempt}
        onShowOutput={showAttemptOutput}
      />
      {mobileLogOpen ? (
        <button
          className="mobile-log-backdrop"
          type="button"
          aria-label="Close Agent Log"
          onClick={() => setMobileLogOpen(false)}
        />
      ) : null}
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        Attempt {task.active_attempt || 1}: {activePhaseLabel(task.status)}
      </p>
    </main>
  )
}
