import { ArrowRight } from '@phosphor-icons/react/dist/csr/ArrowRight'
import { ChartBar } from '@phosphor-icons/react/dist/csr/ChartBar'
import { FileArrowUp } from '@phosphor-icons/react/dist/csr/FileArrowUp'
import { Flask } from '@phosphor-icons/react/dist/csr/Flask'
import { Plus } from '@phosphor-icons/react/dist/csr/Plus'
import { SlidersHorizontal } from '@phosphor-icons/react/dist/csr/SlidersHorizontal'
import { SpinnerGap } from '@phosphor-icons/react/dist/csr/SpinnerGap'
import { Stop } from '@phosphor-icons/react/dist/csr/Stop'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../lib/api'
import type { BenchmarkRun, BenchmarkSet, SystemStatus } from '../../types/api'
import { formatDuration } from '../../lib/runModel'

export function BenchmarkView({
  status,
  onOpenTask,
  onToast,
}: {
  status: SystemStatus | null
  onOpenTask: (id: string) => void
  onToast: (message: string, tone?: 'success' | 'error' | 'info') => void
}) {
  const [sets, setSets] = useState<BenchmarkSet[]>([])
  const [runs, setRuns] = useState<BenchmarkRun[]>([])
  const [runningSet, setRunningSet] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [targetSet, setTargetSet] = useState('')
  const [entryId, setEntryId] = useState('')
  const [entryPrompt, setEntryPrompt] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const refreshErrorShown = useRef(false)
  const models = useMemo(() => (status?.models.length ? status.models : ['qwen2.5-coder:7b']), [status])
  const preferredModel =
    status?.default_model && models.includes(status.default_model) ? status.default_model : models[0]
  const imageValues = status ? Object.values(status.sandbox_images) : []
  const [model, setModel] = useState(preferredModel)
  const [maxAttempts, setMaxAttempts] = useState(4)
  const [executionTimeout, setExecutionTimeout] = useState(60)
  const [memoryMb, setMemoryMb] = useState(1024)
  const [cpuLimit, setCpuLimit] = useState(1)
  const [diskMb, setDiskMb] = useState(512)
  const [executionNetwork, setExecutionNetwork] = useState(false)
  const environmentReady = Boolean(
    status?.docker.available &&
    status.ollama.available &&
    status.models.includes(model) &&
    imageValues.length &&
    imageValues.every(Boolean),
  )

  useEffect(() => {
    if (!models.includes(model)) setModel(preferredModel)
  }, [model, models, preferredModel])

  const refresh = useCallback(async () => {
    try {
      const [nextSets, nextRuns] = await Promise.all([api.benchmarkSets(), api.benchmarkRuns()])
      setSets(nextSets)
      setRuns(nextRuns)
      setTargetSet((current) => current || nextSets[0]?.id || '')
      refreshErrorShown.current = false
    } catch {
      if (!refreshErrorShown.current) {
        refreshErrorShown.current = true
        onToast('Could not load benchmarks.', 'error')
      }
    }
  }, [onToast])

  useEffect(() => {
    void refresh()
  }, [refresh])
  const hasActiveRun = useMemo(
    () => runs.some((run) => ['queued', 'running', 'cancelling'].includes(run.status)),
    [runs],
  )
  useEffect(() => {
    if (!hasActiveRun) return
    const interval = window.setInterval(() => void refresh(), 1500)
    return () => window.clearInterval(interval)
  }, [hasActiveRun, refresh])

  const importSet = async (file: File) => {
    try {
      const parsed = JSON.parse(await file.text()) as Omit<BenchmarkSet, 'id' | 'created_at'>
      await api.importBenchmarkSet(parsed)
      await refresh()
      onToast('Benchmark set imported.')
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Invalid benchmark JSON.', 'error')
    }
  }

  const runSet = async (setId: string) => {
    setRunningSet(setId)
    try {
      await api.runBenchmark(setId, {
        model,
        max_retries: maxAttempts - 1,
        execution_timeout_seconds: executionTimeout,
        memory_mb: memoryMb,
        cpu_limit: cpuLimit,
        disk_mb: diskMb,
        execution_network: executionNetwork,
      })
      await refresh()
      onToast('Benchmark tasks added to the sequential queue.', 'info')
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Could not start benchmark.', 'error')
    } finally {
      setRunningSet(null)
    }
  }

  const addTask = async () => {
    if (!targetSet || !entryId.trim() || !entryPrompt.trim()) return
    try {
      await api.addBenchmarkTask(targetSet, {
        id: entryId.trim(),
        prompt: entryPrompt.trim(),
        attachments: [],
      })
      setEntryId('')
      setEntryPrompt('')
      setAddOpen(false)
      await refresh()
      onToast('Task added to the versioned benchmark set.')
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Could not add benchmark task.', 'error')
    }
  }

  const cancelRun = async (runId: string) => {
    try {
      await api.cancelBenchmark(runId)
      await refresh()
      onToast('Benchmark stop requested.', 'info')
    } catch (reason) {
      onToast(reason instanceof Error ? reason.message : 'Could not stop the benchmark.', 'error')
    }
  }

  return (
    <main className="benchmark-view">
      <header className="benchmark-heading">
        <div>
          <span>
            <Flask aria-hidden="true" />
            Benchmarks
          </span>
          <h1>Evaluate the full agent loop.</h1>
          <p>
            Import versioned JSON task sets and run them sequentially with the same Ollama and Docker
            pipeline.
          </p>
        </div>
        <input
          ref={fileInput}
          hidden
          type="file"
          accept="application/json,.json"
          onChange={(event) => {
            const file = event.target.files?.[0]
            event.currentTarget.value = ''
            if (file) void importSet(file)
          }}
        />
        <div className="benchmark-heading-actions">
          <label className="benchmark-setting">
            <span>Model</span>
            <select
              aria-label="Benchmark model"
              value={model}
              onChange={(event) => setModel(event.target.value)}
            >
              {models.map((item) => (
                <option value={item} key={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="benchmark-setting">
            <span>Attempts</span>
            <select
              aria-label="Benchmark attempts"
              value={maxAttempts}
              onChange={(event) => setMaxAttempts(Number(event.target.value))}
            >
              {[1, 2, 3, 4, 5, 6].map((value) => (
                <option value={value} key={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <button
            className="advanced-toggle benchmark-import"
            type="button"
            disabled={!sets.length}
            onClick={() => setAddOpen((value) => !value)}
          >
            <Plus aria-hidden="true" />
            Add task
          </button>
          <button
            className="advanced-toggle benchmark-import"
            type="button"
            onClick={() => fileInput.current?.click()}
          >
            <FileArrowUp aria-hidden="true" />
            Import JSON
          </button>
        </div>
      </header>
      <details className="benchmark-run-options">
        <summary>
          <SlidersHorizontal aria-hidden="true" />
          Run resources
        </summary>
        <div>
          <label>
            <span>Execution timeout</span>
            <select
              aria-label="Benchmark execution timeout"
              value={executionTimeout}
              onChange={(event) => setExecutionTimeout(Number(event.target.value))}
            >
              {[30, 60, 120, 300, 600].map((value) => (
                <option value={value} key={value}>
                  {value}s
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Memory</span>
            <select
              aria-label="Benchmark memory"
              value={memoryMb}
              onChange={(event) => setMemoryMb(Number(event.target.value))}
            >
              {[512, 1024, 2048, 4096].map((value) => (
                <option value={value} key={value}>
                  {value >= 1024 ? `${value / 1024} GiB` : `${value} MiB`}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>CPU</span>
            <select
              aria-label="Benchmark CPU"
              value={cpuLimit}
              onChange={(event) => setCpuLimit(Number(event.target.value))}
            >
              {[0.5, 1, 2, 4].map((value) => (
                <option value={value} key={value}>
                  {value} CPU
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Workspace disk</span>
            <select
              aria-label="Benchmark workspace disk"
              value={diskMb}
              onChange={(event) => setDiskMb(Number(event.target.value))}
            >
              {[128, 256, 512, 1024, 2048].map((value) => (
                <option value={value} key={value}>
                  {value >= 1024 ? `${value / 1024} GiB` : `${value} MiB`}
                </option>
              ))}
            </select>
          </label>
          <label className="benchmark-network">
            <span>Execution network</span>
            <input
              aria-label="Benchmark execution network"
              type="checkbox"
              checked={executionNetwork}
              onChange={(event) => setExecutionNetwork(event.target.checked)}
            />
            <small>Installs always have network access.</small>
          </label>
        </div>
      </details>
      {addOpen ? (
        <section className="benchmark-entry" aria-label="Add benchmark task">
          <label>
            <span>Task set</span>
            <select value={targetSet} onChange={(event) => setTargetSet(event.target.value)}>
              {sets.map((set) => (
                <option value={set.id} key={set.id}>
                  {set.name} · v{set.version}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Stable task ID</span>
            <input
              value={entryId}
              onChange={(event) => setEntryId(event.target.value)}
              placeholder="json-parser-01"
            />
          </label>
          <label className="benchmark-entry-prompt">
            <span>Task prompt</span>
            <textarea
              value={entryPrompt}
              onChange={(event) => setEntryPrompt(event.target.value)}
              rows={3}
              placeholder="Describe the Python task, examples, and constraints…"
            />
          </label>
          <button
            className="run-task-button"
            type="button"
            disabled={!targetSet || !entryId.trim() || !entryPrompt.trim()}
            onClick={() => void addTask()}
          >
            <Plus aria-hidden="true" />
            Add to set
          </button>
        </section>
      ) : null}
      <section className="benchmark-grid" aria-label="Benchmark sets">
        {sets.map((set) => (
          <article className="benchmark-set" key={set.id}>
            <header>
              <div>
                <strong>{set.name}</strong>
                <span>v{set.version}</span>
              </div>
              <small>{set.tasks.length} tasks</small>
            </header>
            <button
              className="run-task-button"
              type="button"
              disabled={Boolean(runningSet) || hasActiveRun || !environmentReady}
              title={
                !environmentReady
                  ? 'Docker, Ollama, an installed model, and the sandbox image must be ready.'
                  : hasActiveRun
                    ? 'Stop or finish the active benchmark first.'
                    : undefined
              }
              onClick={() => void runSet(set.id)}
            >
              {runningSet === set.id ? (
                <SpinnerGap className="spinning" aria-hidden="true" />
              ) : (
                <ArrowRight aria-hidden="true" />
              )}
              Run all
            </button>
          </article>
        ))}
        {sets.length === 0 ? (
          <div className="benchmark-empty">
            <ChartBar aria-hidden="true" />
            <span>No benchmark sets imported.</span>
          </div>
        ) : null}
      </section>
      <section className="benchmark-runs">
        <h2>Reports</h2>
        {runs.map((run) => (
          <article className="benchmark-run" key={run.id}>
            <header>
              <strong>
                {run.status === 'completed'
                  ? 'Completed run'
                  : run.status === 'cancelled'
                    ? 'Stopped benchmark'
                    : run.status === 'cancelling'
                      ? 'Stopping benchmark'
                      : 'Running benchmark'}
              </strong>
              <span>
                {run.status === 'running' || run.status === 'cancelling'
                  ? `Task ${Math.min(run.completed_tasks + 1, run.total_tasks)} of ${run.total_tasks}`
                  : `${run.completed_tasks} / ${run.total_tasks} complete`}
              </span>
            </header>
            {run.report ? (
              <dl>
                <div>
                  <dt>Pass rate</dt>
                  <dd>{Math.round(run.report.pass_rate * 100)}%</dd>
                </div>
                <div>
                  <dt>Avg attempts</dt>
                  <dd>{run.report.average_attempts_to_pass.toFixed(1)}</dd>
                </div>
                <div>
                  <dt>Avg tool calls</dt>
                  <dd>{run.report.average_inner_iterations.toFixed(1)}</dd>
                </div>
                <div>
                  <dt>Total time</dt>
                  <dd>{formatDuration(run.report.total_time_ms)}</dd>
                </div>
              </dl>
            ) : (
              <div className="benchmark-progress">
                <span
                  style={{ width: `${run.total_tasks ? (run.completed_tasks / run.total_tasks) * 100 : 0}%` }}
                />
              </div>
            )}
            {run.status === 'running' ? (
              <button
                className="advanced-toggle benchmark-stop"
                type="button"
                onClick={() => void cancelRun(run.id)}
              >
                <Stop weight="fill" aria-hidden="true" />
                Stop run
              </button>
            ) : null}
            <div className="benchmark-task-links">
              {run.task_ids.map((id, index) => (
                <button type="button" onClick={() => onOpenTask(id)} key={id}>
                  Task {index + 1}
                </button>
              ))}
            </div>
          </article>
        ))}
      </section>
    </main>
  )
}
