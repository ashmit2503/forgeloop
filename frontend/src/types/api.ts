export type TaskStatus =
  | 'queued'
  | 'intake'
  | 'awaiting_clarification'
  | 'planning'
  | 'generating'
  | 'installing'
  | 'testing'
  | 'executing'
  | 'repairing'
  | 'succeeded'
  | 'stopped'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export type AttemptStatus = 'running' | 'succeeded' | 'failed' | 'cancelled'
export type Runtime = 'python' | 'node'
export type RuntimeRequest = 'auto' | Runtime

export interface CommandResult {
  phase: 'install' | 'test' | 'execute' | 'healthcheck' | 'judge'
  argv: string[]
  exit_code: number | null
  stdout: string
  stderr: string
  timed_out: boolean
  duration_ms: number
  truncated: boolean
}

export interface FilePayload {
  path: string
  content: string
}

export interface AttemptPlan {
  summary: string
  steps: string[]
  failure_response: string | null
}

export interface Citation {
  title: string
  url: string
}

export interface ToolEvent {
  index: number
  tool: 'read_file' | 'write_file' | 'list_dir' | 'run_command' | 'run_linter' | 'web_search' | 'finalize'
  label: string
  detail: string | null
  status: 'running' | 'passed' | 'failed'
  duration_ms: number
  citations: Citation[]
}

export interface TestContract {
  deterministic: boolean
  summary: string
  expected: string
  test_files: FilePayload[]
  test_command: { argv: string[] } | null
  execution:
    | { kind: 'command'; command: { argv: string[] } }
    | { kind: 'http'; start_command: { argv: string[] }; port: number; health_path: string }
  judge_criteria: string | null
  revision_reason: string | null
}

export interface Attempt {
  id: string
  number: number
  status: AttemptStatus
  phase: string | null
  runtime: Runtime
  summary: string | null
  failure_message: string | null
  exit_code: number | null
  timed_out: boolean
  stdout: string
  stderr: string
  logs_truncated: boolean
  stdout_truncated?: boolean
  stderr_truncated?: boolean
  changed_files: string[]
  results: CommandResult[]
  started_at: string
  completed_at: string | null
  duration_ms: number | null
  plan?: AttemptPlan | null
  tool_events?: ToolEvent[]
  inner_iterations?: number
  test_contract?: TestContract | null
}

export interface Task {
  id: string
  prompt: string
  runtime: RuntimeRequest
  resolved_runtime: Runtime | null
  model_provider: string
  model: string
  sandbox_provider: string
  max_retries: number
  status: TaskStatus
  summary: string | null
  error: string | null
  active_attempt: number | null
  cancel_requested: boolean
  created_at: string
  updated_at: string
  completed_at: string | null
  title?: string | null
  clarification_question?: string | null
  clarification_answer?: string | null
  run_started_at?: string | null
  execution_timeout_seconds?: number
  memory_mb?: number
  cpu_limit?: number
  disk_mb?: number
  execution_network?: boolean
  attachment_names?: string[]
}

export interface TaskDetail extends Task {
  attempts: Attempt[]
  event_cursor: number
}

export interface FileInfo {
  path: string
  size: number
}

export interface SystemStatus {
  docker: { available: boolean; version?: string; detail: string }
  ollama: { available: boolean; version?: string; detail: string }
  models: string[]
  default_model?: string
  sandbox_images: Record<string, boolean>
  providers: Record<string, { configured: boolean; enabled: boolean; model?: string; reason?: string }>
}

export interface TaskCreate {
  prompt: string
  runtime: 'python'
  model_provider: 'ollama'
  model: string
  sandbox_provider: 'docker'
  max_retries: number
  attachments: FilePayload[]
  execution_timeout_seconds: number
  memory_mb: number
  cpu_limit: number
  disk_mb: number
  execution_network: boolean
}

export interface BenchmarkTaskDefinition {
  id: string
  prompt: string
  attachments: FilePayload[]
}

export interface BenchmarkSet {
  id: string
  name: string
  version: string
  tasks: BenchmarkTaskDefinition[]
  created_at: string
}

export interface BenchmarkReport {
  pass_rate: number
  average_attempts_to_pass: number
  average_inner_iterations: number
  total_time_ms: number
}

export interface BenchmarkRun {
  id: string
  benchmark_set_id: string
  status: 'queued' | 'running' | 'cancelling' | 'completed' | 'cancelled'
  task_ids: string[]
  completed_tasks: number
  total_tasks: number
  report: BenchmarkReport | null
  created_at: string
  completed_at: string | null
}

export interface AgentEvent {
  task_id: string
  timestamp: string
  status?: TaskStatus | AttemptStatus | ToolEvent['status']
  attempt?: number
  attempt_id?: string
  phase?: string
  exit_code?: number | null
  timed_out?: boolean
  files?: string[]
  stream?: 'stdout' | 'stderr'
  chunk?: string
  message?: string
  path?: string
  question?: string
  plan?: AttemptPlan
  tool?: ToolEvent['tool']
  index?: number
  label?: string
  detail?: string
  duration_ms?: number
  citations?: Citation[]
}

export const terminalStatuses: TaskStatus[] = ['succeeded', 'stopped', 'failed', 'cancelled', 'interrupted']
