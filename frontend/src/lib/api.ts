import type {
  BenchmarkRun,
  BenchmarkSet,
  FileInfo,
  SystemStatus,
  Task,
  TaskCreate,
  TaskDetail,
} from '../types/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    signal: AbortSignal.timeout(30_000),
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new Error(apiErrorMessage(body, response))
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

function apiErrorMessage(body: unknown, response: Response): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const messages = detail.flatMap((item) => {
        if (!item || typeof item !== 'object') return []
        const message = (item as { msg?: unknown }).msg
        const location = (item as { loc?: unknown }).loc
        const prefix = Array.isArray(location) ? location.slice(1).join('.') : ''
        return typeof message === 'string' ? [`${prefix ? `${prefix}: ` : ''}${message}`] : []
      })
      if (messages.length) return messages.join(' · ')
    }
  }
  return response.statusText || `Request failed with ${response.status}`
}

export const api = {
  status: () => request<SystemStatus>('/api/system/status'),
  tasks: (limit = 100, offset = 0) => request<Task[]>(`/api/tasks?limit=${limit}&offset=${offset}`),
  task: (id: string) => request<TaskDetail>(`/api/tasks/${id}`),
  createTask: (body: TaskCreate) =>
    request<Task>('/api/tasks', { method: 'POST', body: JSON.stringify(body) }),
  cancelTask: (id: string) => request(`/api/tasks/${id}/cancel`, { method: 'POST' }),
  answerClarification: (id: string, answer: string) =>
    request<TaskDetail>(`/api/tasks/${id}/clarification`, {
      method: 'POST',
      body: JSON.stringify({ answer }),
    }),
  deleteTask: (id: string) => request(`/api/tasks/${id}`, { method: 'DELETE' }),
  files: (id: string, attempt: number) => request<FileInfo[]>(`/api/tasks/${id}/attempts/${attempt}/files`),
  file: (id: string, attempt: number, path: string) =>
    request<{ path: string; content: string }>(
      `/api/tasks/${id}/attempts/${attempt}/files/${path.split('/').map(encodeURIComponent).join('/')}`,
    ),
  benchmarkSets: () => request<BenchmarkSet[]>('/api/benchmarks/sets'),
  importBenchmarkSet: (body: Omit<BenchmarkSet, 'id' | 'created_at'>) =>
    request<BenchmarkSet>('/api/benchmarks/sets', { method: 'POST', body: JSON.stringify(body) }),
  addBenchmarkTask: (setId: string, body: BenchmarkSet['tasks'][number]) =>
    request<BenchmarkSet>(`/api/benchmarks/sets/${setId}/tasks`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  benchmarkRuns: () => request<BenchmarkRun[]>('/api/benchmarks/runs'),
  runBenchmark: (
    setId: string,
    body: {
      model: string
      max_retries: number
      execution_timeout_seconds: number
      memory_mb: number
      cpu_limit: number
      disk_mb: number
      execution_network: boolean
    },
  ) =>
    request<BenchmarkRun>(`/api/benchmarks/sets/${setId}/runs`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  cancelBenchmark: (runId: string) =>
    request<BenchmarkRun>(`/api/benchmarks/runs/${runId}/cancel`, {
      method: 'POST',
    }),
}
