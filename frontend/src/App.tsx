import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './lib/api'
import { useRunHistory } from './hooks/useRunHistory'
import { useSystemStatus } from './hooks/useSystemStatus'
import { useTaskEvents } from './hooks/useTaskEvents'
import type { AgentEvent, FileInfo, TaskCreate, TaskDetail } from './types/api'
import { terminalStatuses } from './types/api'
import { ClarificationPrompt } from './components/ClarificationPrompt'
import { HistoryDrawer } from './components/HistoryDrawer'
import { useElapsed, usePersistentBoolean, useResizableWidth } from './hooks/useWorkspaceLayout'
import {
  consumePersistedPrefix,
  mergeWorkspaceFiles,
  recommendedTab,
  stripInternalOutput,
} from './lib/runModel'
import { TaskComposer } from './components/TaskComposer'
import { TaskWorkspace } from './components/TaskWorkspace'
import { Toast } from './components/Toast'
import { TopBar } from './components/TopBar'
import type { WorkspaceTab } from './types/workspace'

const BenchmarkView = lazy(async () => ({
  default: (await import('./features/benchmarks/BenchmarkView')).BenchmarkView,
}))
const CommandPalette = lazy(async () => ({
  default: (await import('./components/CommandPalette')).CommandPalette,
}))
const COMPACT_LAYOUT_QUERY = '(max-width: 1100px)'

export default function App() {
  const { systemStatus, systemStatusLoading, systemStatusError, refreshStatus } = useSystemStatus()
  const [view, setView] = useState<'tasks' | 'benchmarks'>('tasks')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [selectedAttempt, setSelectedAttempt] = useState<number | null>(null)
  const [followLatest, setFollowLatest] = useState(true)
  const [currentFiles, setCurrentFiles] = useState<FileInfo[]>([])
  const [previousFiles, setPreviousFiles] = useState<FileInfo[]>([])
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [currentContent, setCurrentContent] = useState<string | null>(null)
  const [previousContent, setPreviousContent] = useState<string | null>(null)
  const [fileLoading, setFileLoading] = useState(false)
  const [liveLogs, setLiveLogs] = useState({ stdout: '', stderr: '' })
  const [streamedFile, setStreamedFile] = useState<{ path: string; content: string } | null>(null)
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('code')
  const [historyOpen, setHistoryOpen] = usePersistentBoolean('coding-sandbox:history-open:v1', true)
  const historyPanel = useResizableWidth('coding-sandbox:history-width:v1', 270, 220, 420)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [agentLogCollapsed, setAgentLogCollapsed] = usePersistentBoolean(
    'coding-sandbox:agent-log-collapsed:v1',
    true,
  )
  const [toast, setToast] = useState<{
    id: number
    message: string
    tone: 'success' | 'error' | 'info'
  } | null>(null)
  const toastSequence = useRef(0)
  const mobileAtMount = useRef(window.matchMedia(COMPACT_LAYOUT_QUERY).matches)
  const detailRef = useRef<TaskDetail | null>(null)
  const selectedIdRef = useRef<string | null>(null)
  const liveAttemptIdRef = useRef<string | null>(null)
  const liveAttemptNumberRef = useRef<number | null>(null)
  const detailRequestRef = useRef(0)
  const selectedAttemptRef = useRef(selectedAttempt)
  const selectedFileRef = useRef(selectedFile)
  const elapsed = useElapsed(detail)

  selectedIdRef.current = selectedId
  selectedAttemptRef.current = selectedAttempt
  selectedFileRef.current = selectedFile
  detailRef.current = detail
  const persistedActiveAttemptId = detail?.attempts.find((item) => item.number === detail.active_attempt)?.id
  if (persistedActiveAttemptId) liveAttemptIdRef.current = persistedActiveAttemptId
  if (detail?.active_attempt) liveAttemptNumberRef.current = detail.active_attempt

  const notify = useCallback((message: string, tone: 'success' | 'error' | 'info' = 'success') => {
    toastSequence.current += 1
    setToast({ id: toastSequence.current, message, tone })
  }, [])

  useEffect(() => {
    if (mobileAtMount.current) setHistoryOpen(false)
  }, [setHistoryOpen])

  const { tasks, setTasks, historyHasMore, historyLoadingMore, refreshTasks, loadMoreTasks } =
    useRunHistory(notify)

  const refreshDetail = useCallback(async (id: string) => {
    const requestId = ++detailRequestRef.current
    try {
      const value = await api.task(id)
      if (selectedIdRef.current !== id || requestId !== detailRequestRef.current) return null
      const previous = detailRef.current
      if (previous?.id === value.id) {
        const previousAttempt = previous.attempts.find((item) => item.number === previous.active_attempt)
        const nextAttempt = value.attempts.find((item) => item.number === value.active_attempt)
        if (previousAttempt && nextAttempt && previousAttempt.id === nextAttempt.id) {
          setLiveLogs((logs) => ({
            stdout: consumePersistedPrefix(logs.stdout, previousAttempt.stdout, nextAttempt.stdout),
            stderr: consumePersistedPrefix(logs.stderr, previousAttempt.stderr, nextAttempt.stderr),
          }))
        }
      }
      setDetail(value)
      setDetailError(null)
      detailRef.current = value
      return value
    } catch (reason) {
      if (selectedIdRef.current === id && requestId === detailRequestRef.current) {
        setDetailError(reason instanceof Error ? reason.message : 'Could not load this task.')
      }
      return null
    }
  }, [])

  useEffect(() => {
    void refreshTasks()
  }, [refreshTasks])

  const hasActiveHistory = tasks.some((task) => !terminalStatuses.includes(task.status))
  useEffect(() => {
    if (!hasActiveHistory) return
    const interval = window.setInterval(() => void refreshTasks(true), 3_000)
    return () => window.clearInterval(interval)
  }, [hasActiveHistory, refreshTasks])

  useEffect(() => {
    if (!selectedId) {
      detailRequestRef.current += 1
      setDetail(null)
      setSelectedAttempt(null)
      setSelectedFile(null)
      return
    }
    let cancelled = false
    setDetailError(null)
    setDetailLoading(true)
    void refreshDetail(selectedId).finally(() => {
      if (!cancelled) setDetailLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [refreshDetail, selectedId])

  useEffect(() => {
    if (!detail?.attempts.length) {
      setSelectedAttempt(null)
      return
    }
    const latest = detail.active_attempt || detail.attempts.at(-1)!.number
    setSelectedAttempt((current) => {
      if (followLatest || current === null) return latest
      return detail.attempts.some((attempt) => attempt.number === current) ? current : latest
    })
  }, [detail?.active_attempt, detail?.attempts, followLatest])

  const eventTaskId =
    selectedId && detail?.id === selectedId && !terminalStatuses.includes(detail.status) ? selectedId : null

  const connection = useTaskEvents(
    eventTaskId,
    detail?.event_cursor || 0,
    useCallback(
      (name: string, event: AgentEvent) => {
        if (name === 'log' && event.stream && event.chunk) {
          if (event.attempt_id && liveAttemptIdRef.current && event.attempt_id !== liveAttemptIdRef.current) {
            return
          }
          setLiveLogs((current) => ({ ...current, [event.stream!]: current[event.stream!] + event.chunk }))
          return
        }
        if (name === 'file_stream_start' && event.path) {
          if (!followLatest && selectedAttempt !== event.attempt) return
          if (event.attempt && liveAttemptNumberRef.current !== event.attempt) return
          setSelectedFile(event.path)
          setStreamedFile({ path: event.path, content: '' })
          return
        }
        if (name === 'file_stream_chunk' && event.path && event.chunk !== undefined) {
          if (!followLatest && selectedAttempt !== event.attempt) return
          if (event.attempt && liveAttemptNumberRef.current !== event.attempt) return
          const path = event.path
          const chunk = event.chunk
          setStreamedFile((current) =>
            current?.path === path ? { path, content: current.content + chunk } : { path, content: chunk },
          )
          return
        }
        if (name === 'file_stream_complete') {
          if (!followLatest && selectedAttempt !== event.attempt) return
          const currentTask = detailRef.current
          const attemptNumber = event.attempt || currentTask?.active_attempt
          const path = event.path
          if (selectedId && attemptNumber && path && liveAttemptNumberRef.current === attemptNumber) {
            void Promise.all([
              api.files(selectedId, attemptNumber),
              api.file(selectedId, attemptNumber, path),
            ])
              .then(([files, file]) => {
                if (
                  selectedIdRef.current !== selectedId ||
                  detailRef.current?.active_attempt !== attemptNumber ||
                  (selectedAttemptRef.current !== null && selectedAttemptRef.current !== attemptNumber)
                )
                  return
                setCurrentFiles(files)
                if (selectedFileRef.current === path) setCurrentContent(file.content)
                setStreamedFile((current) => (current?.path === path ? null : current))
                setDetail((current) =>
                  current
                    ? {
                        ...current,
                        attempts: current.attempts.map((attempt) =>
                          attempt.number === attemptNumber
                            ? { ...attempt, changed_files: [...new Set([...attempt.changed_files, path])] }
                            : attempt,
                        ),
                      }
                    : current,
                )
              })
              .catch(() => notify('Could not synchronize the generated file.', 'error'))
          }
          return
        }
        if (!selectedId) return
        if (name === 'state' && event.status) {
          const status = event.status as TaskDetail['status']
          setDetail((current) =>
            current
              ? {
                  ...current,
                  status,
                  active_attempt: event.attempt || current.active_attempt,
                }
              : current,
          )
          setTasks((current) =>
            current.map((task) =>
              task.id === selectedId
                ? {
                    ...task,
                    status,
                    active_attempt: event.attempt || task.active_attempt,
                  }
                : task,
            ),
          )
          if (status === 'planning' && !detailRef.current?.run_started_at) {
            void refreshDetail(selectedId)
          }
          return
        }
        if (name === 'tool' && event.index && event.tool) {
          setDetail((current) =>
            current
              ? {
                  ...current,
                  attempts: current.attempts.map((attempt) =>
                    attempt.number === current.active_attempt
                      ? {
                          ...attempt,
                          inner_iterations: event.index,
                          tool_events: [
                            ...(attempt.tool_events || []).filter((tool) => tool.index !== event.index),
                            event as NonNullable<typeof attempt.tool_events>[number],
                          ],
                        }
                      : attempt,
                  ),
                }
              : current,
          )
          return
        }
        if (name === 'attempt_started') {
          liveAttemptIdRef.current = event.attempt_id || null
          liveAttemptNumberRef.current = event.attempt || null
          setLiveLogs({ stdout: '', stderr: '' })
          setStreamedFile(null)
        }
        void refreshDetail(selectedId)
        if (name === 'attempt_complete' || name === 'task_complete') void refreshTasks()
        if (name === 'task_complete') {
          notify(
            event.status === 'succeeded'
              ? `Passed on attempt ${event.attempt || '—'}.`
              : 'Task run finished.',
            event.status === 'succeeded' ? 'success' : 'info',
          )
        }
      },
      [followLatest, notify, refreshDetail, refreshTasks, selectedAttempt, selectedId, setTasks],
    ),
    () => {
      if (selectedId) void refreshDetail(selectedId)
    },
  )

  useEffect(() => {
    if (!eventTaskId) return
    const timer = window.setInterval(() => void refreshDetail(eventTaskId), 5_000)
    return () => window.clearInterval(timer)
  }, [eventTaskId, refreshDetail])

  const attempt = useMemo(
    () => detail?.attempts.find((item) => item.number === selectedAttempt) || null,
    [detail?.attempts, selectedAttempt],
  )
  const previousAttempt = useMemo(
    () => detail?.attempts.find((item) => item.number === (selectedAttempt || 0) - 1) || null,
    [detail?.attempts, selectedAttempt],
  )
  const attemptNumber = attempt?.number ?? null
  const previousAttemptNumber = previousAttempt?.number ?? null
  const changedFilesKey = attempt?.changed_files.join('\u0000') || ''
  const detailStatus = detail?.status

  useEffect(() => {
    if (!selectedId || attemptNumber === null) {
      setCurrentFiles([])
      setPreviousFiles([])
      setSelectedFile(null)
      return
    }
    let cancelled = false
    const currentRequest = api.files(selectedId, attemptNumber)
    const previousRequest =
      previousAttemptNumber !== null
        ? api.files(selectedId, previousAttemptNumber).catch(() => [] as FileInfo[])
        : Promise.resolve([] as FileInfo[])
    void Promise.all([currentRequest, previousRequest])
      .then(([current, previous]) => {
        if (cancelled) return
        setCurrentFiles(current)
        setPreviousFiles(previous)
        const changedFiles = changedFilesKey ? changedFilesKey.split('\u0000') : []
        const merged = mergeWorkspaceFiles(current, previous, changedFiles)
        setSelectedFile((currentPath) =>
          currentPath && merged.some((file) => file.path === currentPath)
            ? currentPath
            : merged.find((file) => file.change)?.path || merged[0]?.path || null,
        )
      })
      .catch(() => {
        if (!cancelled) {
          setCurrentFiles([])
          setPreviousFiles([])
          setSelectedFile(null)
          notify('Could not load workspace files.', 'error')
        }
      })
    return () => {
      cancelled = true
    }
  }, [attemptNumber, changedFilesKey, notify, previousAttemptNumber, selectedId])

  const workspaceFiles = useMemo(
    () => mergeWorkspaceFiles(currentFiles, previousFiles, attempt?.changed_files || []),
    [attempt?.changed_files, currentFiles, previousFiles],
  )

  useEffect(() => {
    if (!selectedId || attemptNumber === null || !selectedFile) {
      setCurrentContent(null)
      setPreviousContent(null)
      setFileLoading(false)
      return
    }
    let cancelled = false
    setFileLoading(true)
    const hasCurrent = currentFiles.some((file) => file.path === selectedFile)
    const hasPrevious =
      previousAttemptNumber !== null && previousFiles.some((file) => file.path === selectedFile)
    const currentRequest = hasCurrent
      ? api.file(selectedId, attemptNumber, selectedFile).then((file) => file.content)
      : Promise.resolve(null)
    const previousRequest =
      hasPrevious && previousAttemptNumber !== null
        ? api.file(selectedId, previousAttemptNumber, selectedFile).then((file) => file.content)
        : Promise.resolve(null)
    void Promise.allSettled([currentRequest, previousRequest]).then(([current, previous]) => {
      if (cancelled) return
      setCurrentContent(current.status === 'fulfilled' ? current.value : null)
      setPreviousContent(previous.status === 'fulfilled' ? previous.value : null)
      setFileLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [attemptNumber, currentFiles, previousAttemptNumber, previousFiles, selectedFile, selectedId])

  useEffect(() => {
    if (!detailStatus || terminalStatuses.includes(detailStatus)) return
    setActiveTab(recommendedTab(detailStatus))
  }, [detailStatus])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen(true)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const newTask = useCallback(() => {
    selectedIdRef.current = null
    detailRequestRef.current += 1
    detailRef.current = null
    setDetail(null)
    setDetailError(null)
    setDetailLoading(false)
    setView('tasks')
    setSelectedId(null)
    setSelectedAttempt(null)
    setCurrentFiles([])
    setPreviousFiles([])
    setSelectedFile(null)
    setCurrentContent(null)
    setPreviousContent(null)
    setFileLoading(false)
    setPaletteOpen(false)
    setFollowLatest(true)
    setLiveLogs({ stdout: '', stderr: '' })
    setStreamedFile(null)
    liveAttemptIdRef.current = null
    liveAttemptNumberRef.current = null
  }, [])

  const selectTask = useCallback(
    (id: string) => {
      selectedIdRef.current = id
      if (id !== selectedId) {
        selectedAttemptRef.current = null
        selectedFileRef.current = null
        detailRequestRef.current += 1
        detailRef.current = null
        setDetail(null)
        setDetailError(null)
        setSelectedAttempt(null)
        setCurrentFiles([])
        setPreviousFiles([])
        setSelectedFile(null)
        setCurrentContent(null)
        setPreviousContent(null)
        setFileLoading(false)
        setLiveLogs({ stdout: '', stderr: '' })
        setStreamedFile(null)
        liveAttemptIdRef.current = null
        liveAttemptNumberRef.current = null
      }
      setView('tasks')
      setSelectedId(id)
      setActiveTab('code')
      setFollowLatest(true)
      if (window.matchMedia(COMPACT_LAYOUT_QUERY).matches) setHistoryOpen(false)
    },
    [selectedId, setHistoryOpen],
  )

  const createTask = useCallback(
    async (task: TaskCreate) => {
      const created = await api.createTask(task)
      setTasks((current) => [created, ...current.filter((item) => item.id !== created.id)])
      selectTask(created.id)
      notify('Task added to the local queue.')
    },
    [notify, selectTask, setTasks],
  )

  const selectAttempt = useCallback(
    (number: number) => {
      selectedAttemptRef.current = number
      setSelectedAttempt(number)
      setFollowLatest(number === detail?.active_attempt)
      setStreamedFile(null)
    },
    [detail?.active_attempt],
  )

  const selectTab = useCallback((tab: WorkspaceTab) => {
    setActiveTab(tab)
  }, [])

  const cancelTask = useCallback(async () => {
    if (!detail) return
    try {
      await api.cancelTask(detail.id)
      notify('Stop requested.', 'info')
      await refreshDetail(detail.id)
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Could not stop the task.', 'error')
    }
  }, [detail, notify, refreshDetail])

  const deleteTask = useCallback(async () => {
    if (
      !detail ||
      !window.confirm('Delete this task and every stored workspace snapshot? This cannot be undone.')
    )
      return
    try {
      await api.deleteTask(detail.id)
      setTasks((current) => current.filter((task) => task.id !== detail.id))
      newTask()
      await refreshTasks()
      notify('Task and artifacts deleted.')
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : 'Could not delete the task.', 'error')
    }
  }, [detail, newTask, notify, refreshTasks, setTasks])

  const answerClarification = useCallback(
    async (answer: string) => {
      if (!detail) return
      const updated = await api.answerClarification(detail.id, answer)
      if (selectedIdRef.current !== detail.id) return
      detailRequestRef.current += 1
      detailRef.current = updated
      setDetail(updated)
      notify('Clarification received. The run is continuing.', 'info')
    },
    [detail, notify],
  )

  const shouldExpandAgentLog = Boolean(
    detail && !terminalStatuses.includes(detail.status) && detail.status !== 'awaiting_clarification',
  )
  useEffect(() => {
    if (shouldExpandAgentLog) {
      setAgentLogCollapsed(false)
    }
  }, [setAgentLogCollapsed, shouldExpandAgentLog])

  const activeAttemptSelected =
    attempt?.number === detail?.active_attempt && detail && !terminalStatuses.includes(detail.status)
  const stdout = stripInternalOutput(
    `${attempt?.stdout || ''}${activeAttemptSelected ? liveLogs.stdout : ''}`,
  )
  const stderr = `${attempt?.stderr || ''}${activeAttemptSelected ? liveLogs.stderr : ''}`

  return (
    <div className="live-app-shell">
      <TopBar
        task={view === 'tasks' ? detail : null}
        elapsed={elapsed}
        onHistory={() => setHistoryOpen((value) => !value)}
        onBenchmarks={() => setView('benchmarks')}
        onNew={newTask}
        onCancel={() => void cancelTask()}
        onDelete={() => void deleteTask()}
      />
      <div
        className={`app-body${historyOpen ? ' history-open' : ''}`}
        style={{ '--history-width': `${historyPanel.width}px` } as React.CSSProperties}
      >
        <HistoryDrawer
          open={historyOpen}
          tasks={tasks}
          selectedId={selectedId}
          hasMore={historyHasMore}
          loadingMore={historyLoadingMore}
          onLoadMore={() => void loadMoreTasks()}
          onClose={() => setHistoryOpen(false)}
          onNew={newTask}
          onSelect={selectTask}
          onResize={historyPanel.beginResize}
          onResizeKeyDown={historyPanel.resizeWithKeyboard}
        />
        {historyOpen ? (
          <button
            className="history-backdrop"
            type="button"
            aria-label="Close run history"
            onClick={() => setHistoryOpen(false)}
          />
        ) : null}
        <div className="app-content">
          {view === 'tasks' && selectedId && detail && (connection === 'reconnecting' || detailError) ? (
            <div className="connection-banner" role="status">
              <span>Reconnecting to the local service. Your last saved results are still available.</span>
              <button type="button" onClick={() => void refreshDetail(selectedId)}>
                Retry now
              </button>
            </div>
          ) : null}
          {view === 'benchmarks' ? (
            <Suspense fallback={<LoadingWorkspace />}>
              <BenchmarkView status={systemStatus} onOpenTask={selectTask} onToast={notify} />
            </Suspense>
          ) : selectedId ? (
            detailError && !detail && !detailLoading ? (
              <main className="workspace-error" role="alert">
                <h1>Could not open this run</h1>
                <p>{detailError}</p>
                <div>
                  <button
                    className="run-task-button"
                    type="button"
                    onClick={() => {
                      setDetailLoading(true)
                      void refreshDetail(selectedId).finally(() => setDetailLoading(false))
                    }}
                  >
                    Try again
                  </button>
                  <button className="advanced-toggle" type="button" onClick={newTask}>
                    Back to new task
                  </button>
                </div>
              </main>
            ) : detail && !detailLoading ? (
              detail.status === 'awaiting_clarification' ? (
                <ClarificationPrompt task={detail} onAnswer={answerClarification} />
              ) : (
                <TaskWorkspace
                  task={detail}
                  attempt={attempt}
                  files={workspaceFiles}
                  selectedFile={selectedFile}
                  currentContent={streamedFile?.path === selectedFile ? streamedFile.content : currentContent}
                  previousContent={previousContent}
                  fileLoading={fileLoading}
                  stdout={stdout}
                  stderr={stderr}
                  activeTab={activeTab}
                  agentLogCollapsed={agentLogCollapsed}
                  liveFileStreaming={Boolean(
                    streamedFile?.path === selectedFile && detail.status === 'generating',
                  )}
                  onTab={selectTab}
                  onAttempt={selectAttempt}
                  onFile={setSelectedFile}
                  onToggleAgentLog={() => setAgentLogCollapsed((value) => !value)}
                  onToast={notify}
                />
              )
            ) : (
              <LoadingWorkspace />
            )
          ) : (
            <TaskComposer
              status={systemStatus}
              statusLoading={systemStatusLoading}
              statusError={systemStatusError}
              onRefreshStatus={() => void refreshStatus()}
              onSubmit={createTask}
            />
          )}
        </div>
      </div>
      {paletteOpen && view === 'tasks' ? (
        <Suspense fallback={null}>
          <CommandPalette
            open
            task={detail}
            onClose={() => setPaletteOpen(false)}
            onNew={newTask}
            onTab={selectTab}
            onAttempt={selectAttempt}
            onToggleLog={() => setAgentLogCollapsed((value) => !value)}
            onCancel={() => void cancelTask()}
          />
        </Suspense>
      ) : null}
      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  )
}

function LoadingWorkspace() {
  return (
    <main className="loading-workspace" aria-live="polite">
      <span className="loading-mark" />
      <p>Opening workspace…</p>
    </main>
  )
}
