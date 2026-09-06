import { ArrowClockwise } from '@phosphor-icons/react/dist/csr/ArrowClockwise'
import { ArrowRight } from '@phosphor-icons/react/dist/csr/ArrowRight'
import { CaretDown } from '@phosphor-icons/react/dist/csr/CaretDown'
import { CheckCircle } from '@phosphor-icons/react/dist/csr/CheckCircle'
import { CircleNotch } from '@phosphor-icons/react/dist/csr/CircleNotch'
import { Cpu } from '@phosphor-icons/react/dist/csr/Cpu'
import { Paperclip } from '@phosphor-icons/react/dist/csr/Paperclip'
import { SlidersHorizontal } from '@phosphor-icons/react/dist/csr/SlidersHorizontal'
import { Warning } from '@phosphor-icons/react/dist/csr/Warning'
import { X } from '@phosphor-icons/react/dist/csr/X'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DragEvent, FormEvent } from 'react'
import type { SystemStatus, TaskCreate } from '../types/api'

const archiveExtensions = ['.7z', '.bz2', '.gz', '.rar', '.tar', '.tgz', '.xz', '.zip']

function isArchivePath(path: string) {
  const normalized = path.toLowerCase()
  return archiveExtensions.some((extension) => normalized.endsWith(extension))
}

export function TaskComposer({
  status,
  statusLoading,
  statusError,
  onRefreshStatus,
  onSubmit,
}: {
  status: SystemStatus | null
  statusLoading: boolean
  statusError?: string | null
  onRefreshStatus: () => void
  onSubmit: (task: TaskCreate) => Promise<void>
}) {
  const ollamaModels = useMemo(() => (status?.models.length ? status.models : ['qwen2.5-coder:7b']), [status])
  const preferredModel =
    status?.default_model && ollamaModels.includes(status.default_model)
      ? status.default_model
      : ollamaModels[0]
  const [form, setForm] = useState<TaskCreate>({
    prompt: '',
    runtime: 'python',
    model_provider: 'ollama',
    model: preferredModel,
    sandbox_provider: 'docker',
    max_retries: 3,
    attachments: [],
    execution_timeout_seconds: 60,
    memory_mb: 1024,
    cpu_limit: 1,
    disk_mb: 512,
    execution_network: false,
  })
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const attachmentsRef = useRef(form.attachments)
  attachmentsRef.current = form.attachments
  const imageValues = status ? Object.values(status.sandbox_images) : []
  const environmentReady = Boolean(
    status?.docker.available &&
    status.ollama.available &&
    status.models.includes(form.model) &&
    imageValues.length &&
    imageValues.every(Boolean),
  )
  const canSubmit = Boolean(form.prompt.trim().length >= 3 && !submitting && environmentReady)

  useEffect(() => {
    if (form.model_provider === 'ollama' && !ollamaModels.includes(form.model)) {
      setForm((current) => ({ ...current, model: preferredModel }))
    }
  }, [form.model, form.model_provider, ollamaModels, preferredModel])

  const submitTask = useCallback(async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit({ ...form, prompt: form.prompt.trim() })
      attachmentsRef.current = []
      setForm((current) => ({ ...current, prompt: '', attachments: [] }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not start the task.')
    } finally {
      setSubmitting(false)
    }
  }, [canSubmit, form, onSubmit])

  useEffect(() => {
    const runFromKeyboard = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key !== 'Enter') return
      if (!canSubmit) return
      event.preventDefault()
      void submitTask()
    }
    window.addEventListener('keydown', runFromKeyboard)
    return () => window.removeEventListener('keydown', runFromKeyboard)
  }, [canSubmit, submitTask])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void submitTask()
  }

  const addFiles = useCallback(async (files: File[]) => {
    setError(null)
    try {
      if (files.length > 100) throw new Error('You can attach at most 100 files.')
      if (files.some((file) => isArchivePath(file.name))) {
        throw new Error('Attach individual text files, not an archive.')
      }
      if (files.some((file) => file.size > 512 * 1024)) {
        throw new Error('Each attached file must be 512 KiB or smaller.')
      }
      const payloads = await Promise.all(
        files.map(async (file) => ({
          path: file.webkitRelativePath || file.name,
          content: await file.text(),
        })),
      )
      if (
        payloads.some((file) => {
          const normalized = file.path.toLowerCase()
          return normalized === '.autocoder_tests' || normalized.startsWith('.autocoder_tests/')
        })
      ) {
        throw new Error('.autocoder_tests is reserved for private acceptance tests.')
      }
      if (payloads.some((file) => file.content.includes('\0'))) {
        throw new Error('Attachments must be text files.')
      }
      const byPath = new Map(attachmentsRef.current.map((file) => [file.path, file]))
      payloads.forEach((file) => byPath.set(file.path, file))
      const merged = [...byPath.values()]
      if (merged.length > 100) throw new Error('You can attach at most 100 files.')
      const encoder = new TextEncoder()
      const totalBytes = merged.reduce((total, file) => total + encoder.encode(file.content).length, 0)
      if (totalBytes > 5 * 1024 * 1024) throw new Error('Attachments must total 5 MiB or less.')
      attachmentsRef.current = merged
      setForm((current) => ({ ...current, attachments: merged }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not attach those files.')
    }
  }, [])

  const dropFiles = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    void addFiles([...event.dataTransfer.files])
  }

  return (
    <main className="new-task-view">
      <section className="new-task-panel">
        <header className="composer-heading">
          <h1>Build something.</h1>
          <p>Describe the result. ForgeLoop will write, run, and repair it.</p>
        </header>
        <form onSubmit={submit}>
          <div
            className="composer-box"
            onDragOver={(event) => event.preventDefault()}
            onDrop={dropFiles}
            onPaste={(event) => {
              const files = [...event.clipboardData.files]
              if (files.length) {
                event.preventDefault()
                void addFiles(files)
              }
            }}
          >
            <label className="prompt-field">
              <span className="sr-only">Task prompt</span>
              <textarea
                value={form.prompt}
                onChange={(event) => setForm((current) => ({ ...current, prompt: event.target.value }))}
                placeholder="What do you want to build?"
                rows={7}
                maxLength={20_000}
                autoFocus
                required
              />
            </label>
            {form.attachments.length ? (
              <div className="attachment-list" aria-label="Attached files">
                {form.attachments.map((file) => (
                  <span className="attachment-chip" key={file.path}>
                    <Paperclip aria-hidden="true" />
                    {file.path}
                    <button
                      type="button"
                      onClick={() =>
                        setForm((current) => {
                          const attachments = current.attachments.filter((item) => item.path !== file.path)
                          attachmentsRef.current = attachments
                          return { ...current, attachments }
                        })
                      }
                      aria-label={`Remove ${file.path}`}
                    >
                      <X aria-hidden="true" />
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
            {advancedOpen ? (
              <section className="advanced-panel" aria-label="Advanced task settings">
                <label>
                  <span>Ollama model</span>
                  <select
                    value={form.model}
                    onChange={(event) => setForm((current) => ({ ...current, model: event.target.value }))}
                  >
                    {ollamaModels.map((model) => (
                      <option value={model} key={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Max attempts</span>
                  <select
                    value={form.max_retries + 1}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, max_retries: Number(event.target.value) - 1 }))
                    }
                  >
                    {[1, 2, 3, 4, 5, 6].map((value) => (
                      <option value={value} key={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>Execution timeout</span>
                  <select
                    value={form.execution_timeout_seconds}
                    onChange={(event) =>
                      setForm((current) => ({
                        ...current,
                        execution_timeout_seconds: Number(event.target.value),
                      }))
                    }
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
                    value={form.memory_mb}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, memory_mb: Number(event.target.value) }))
                    }
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
                    value={form.cpu_limit}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, cpu_limit: Number(event.target.value) }))
                    }
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
                    value={form.disk_mb}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, disk_mb: Number(event.target.value) }))
                    }
                  >
                    {[128, 256, 512, 1024, 2048].map((value) => (
                      <option value={value} key={value}>
                        {value >= 1024 ? `${value / 1024} GiB` : `${value} MiB`}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="network-setting">
                  <span>Execution network</span>
                  <input
                    type="checkbox"
                    checked={form.execution_network}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, execution_network: event.target.checked }))
                    }
                  />
                  <small>Dependency installation always has network access.</small>
                </label>
              </section>
            ) : null}
            <div className="composer-controls">
              <div className="composer-primary-options">
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  hidden
                  onChange={(event) => {
                    const selected = [...(event.target.files || [])]
                    event.currentTarget.value = ''
                    void addFiles(selected)
                  }}
                />
                <button className="advanced-toggle" type="button" onClick={() => fileInput.current?.click()}>
                  <Paperclip aria-hidden="true" />
                  Attach files
                </button>
                <button
                  className={`advanced-toggle${advancedOpen ? ' active' : ''}`}
                  type="button"
                  onClick={() => setAdvancedOpen((open) => !open)}
                  aria-expanded={advancedOpen}
                >
                  <SlidersHorizontal aria-hidden="true" />
                  Options
                </button>
              </div>
              <button
                className="run-task-button"
                type="submit"
                disabled={!canSubmit}
                title={
                  !environmentReady
                    ? 'Docker, Ollama, the selected model, and sandbox image must be ready.'
                    : undefined
                }
              >
                {submitting ? (
                  <CircleNotch className="spinning" aria-hidden="true" />
                ) : (
                  <ArrowRight aria-hidden="true" />
                )}
                <span>{submitting ? 'Starting…' : 'Run'}</span>
                <kbd>Ctrl ↵</kbd>
              </button>
            </div>
          </div>
          {error ? (
            <p className="form-error" role="alert">
              <Warning aria-hidden="true" />
              {error}
            </p>
          ) : null}
        </form>
        <EnvironmentStatus
          status={status}
          loading={statusLoading}
          error={statusError}
          onRefresh={onRefreshStatus}
        />
      </section>
    </main>
  )
}

function EnvironmentStatus({
  status,
  loading,
  error,
  onRefresh,
}: {
  status: SystemStatus | null
  loading: boolean
  error?: string | null
  onRefresh: () => void
}) {
  const imageValues = status ? Object.values(status.sandbox_images) : []
  const checks = [
    {
      label: 'Docker',
      ready: Boolean(status?.docker.available),
      detail: status?.docker.detail || error || 'Checking daemon',
    },
    {
      label: 'Ollama',
      ready: Boolean(status?.ollama.available),
      detail: status?.ollama.detail || error || 'Checking service',
    },
    {
      label: 'Models',
      ready: Boolean(status?.models.length),
      detail: status?.models.length ? `${status.models.length} installed` : 'No installed models',
    },
    {
      label: 'Images',
      ready: Boolean(imageValues.length && imageValues.every(Boolean)),
      detail: status
        ? `${imageValues.filter(Boolean).length}/${imageValues.length} ready`
        : 'Checking images',
    },
  ]
  const ready = checks.every((check) => check.ready)

  return (
    <details className="environment-status">
      <summary>
        <span className={`environment-state ${ready ? 'ready' : 'not-ready'}`}>
          <span className="mini-status-dot" />
          {ready ? 'Environment ready' : error ? 'Local service unavailable' : 'Setup required'}
        </span>
        <span>Local · Docker</span>
        <CaretDown aria-hidden="true" />
      </summary>
      <div className="environment-details">
        <header>
          <span>
            <Cpu aria-hidden="true" />
            System status
          </span>
          <button type="button" onClick={onRefresh} disabled={loading}>
            <ArrowClockwise className={loading ? 'spinning' : ''} aria-hidden="true" />
            Refresh
          </button>
        </header>
        <div>
          {checks.map((check) => (
            <span className={check.ready ? 'ready' : 'not-ready'} title={check.detail} key={check.label}>
              {check.ready ? (
                <CheckCircle weight="fill" aria-hidden="true" />
              ) : (
                <Warning weight="fill" aria-hidden="true" />
              )}
              <span>
                <strong>{check.label}</strong>
                <small>{check.detail}</small>
              </span>
            </span>
          ))}
        </div>
      </div>
    </details>
  )
}
