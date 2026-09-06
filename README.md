# ForgeLoop

A local coding workspace that builds, tests, and repairs Python projects using Ollama and Docker. Describe a task or attach text source files, choose an attempt limit, and inspect the code, output, and validation results as it runs.

## Requirements

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Node.js 22.12 or newer and npm.
- Docker with Linux containers enabled and its daemon running.
- [Ollama](https://ollama.com/) with a local model installed. Run `ollama list` to see available models, or install one with `ollama pull qwen2.5-coder:7b`.

## Setup

Run from the repository root:

```sh
uv sync --extra dev --locked
cd frontend
npm ci
npm run build
cd ..
docker build -t autocoder-python:3.12 -f sandbox/python/Dockerfile .
```

Copy `.env.example` to `.env` and set `AUTOCODER_DEFAULT_OLLAMA_MODEL` to a name shown by `ollama list`. On PowerShell, use `Copy-Item .env.example .env`; on macOS or Linux, use `cp .env.example .env`.

```sh
uv run forgeloop
```

Open [127.0.0.1:8000](http://127.0.0.1:8000). Run commands from the repository root so configuration, history, and the built dashboard resolve consistently. The dashboard checks Docker, Ollama, the installed model, and the sandbox image before enabling a run.

## Development

Start the backend and frontend in separate terminals:

```sh
uv run uvicorn autocoder.main:app --reload --host 127.0.0.1 --port 8000
```

```sh
npm --prefix frontend run dev
```

Open [127.0.0.1:5173](http://127.0.0.1:5173). Vite forwards `/api` requests to the backend. Use one backend process: the queue and cancellation controls are managed in memory, while task history and events are persisted in SQLite.

## Task workflow

1. Describe the required behavior. Attach individual UTF-8 source files to fix or extend an existing project; archives are not supported.
2. Choose an installed model, attempt limit, execution timeout, CPU, memory, disk allowance, and execution-network setting under **Options**.
3. The agent asks one clarification when necessary, plans each attempt, edits files, and runs scratch checks.
4. A separate model call creates private acceptance tests. ForgeLoop runs them with its own pytest command in a fresh container, then executes the project. Non-programmatic outcomes can use an explicit model-judge fallback.
5. Failed validation feeds the next repair attempt. Review each attempt, compare file changes, inspect stdout and stderr, or stop the run. Download the final source ZIP when the task finishes.

Each coding loop is limited to 50 tool calls and 30 minutes by default. Up to six attempts are available. Uploaded and stored source is limited to 100 files, 512 KiB per file, and 5 MiB total. The disk setting controls disposable sandbox storage and runtime monitoring; it does not increase the source export limit.

Generated tests and model judgments can be wrong. Review the code and validation evidence before using a generated project. HTTP tasks are started and health-checked for validation; they are not deployed or kept running after completion.

## Configuration and storage

Settings are read from `.env` or environment variables; environment variables take precedence.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AUTOCODER_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama API |
| `AUTOCODER_DEFAULT_OLLAMA_MODEL` | `qwen2.5-coder:7b` | Model used when omitted from a task request |
| `AUTOCODER_PYTHON_IMAGE` | `autocoder-python:3.12` | Python sandbox image |
| `AUTOCODER_DATA_DIR` | `.autocoder-data` | History and generated workspaces |
| `AUTOCODER_DATABASE_URL` | SQLite database inside the data directory | Optional explicit database location |

The `autocoder` Python package, environment-variable prefix, image names, and storage names remain stable for existing installations. `autocoder` remains an alias for the `forgeloop` command.

Task records and events live in SQLite. Source snapshots are stored under `.autocoder-data/tasks/<task-id>/attempt-<n>/source/`; disposable validation copies are removed after execution. Restarting the backend marks unfinished tasks as interrupted and unfinished benchmarks as cancelled. Runs are not resumed automatically. Stop the backend before backing up or relocating the data directory.

## Sandbox behavior

This is a single-user local application without authentication. The launch command binds to `127.0.0.1`.

Containers run as a non-root user with CPU and memory limits, a read-only root filesystem, bounded temporary storage, dropped capabilities, and no privilege escalation. Only the attempt workspace is mounted. The Docker socket and host credentials are not passed to generated programs.

Dependency installation has network access. Execution networking is off by default and can be enabled per task. Installs run inside the sandbox and put Python dependencies under `/tmp/agent-deps`. Completed exports exclude private acceptance tests and runtime caches. Orphan cleanup targets only containers labelled for this application's data directory.

## Benchmarks and API

Import a versioned JSON task set from **Benchmarks**, then select resources and run it. Only one benchmark can run at a time. Sets become immutable once used; import a new version to change them. Reports include pass rate, average attempts to pass, average tool calls, and elapsed time.

```json
{
  "name": "Python basics",
  "version": "1.0.0",
  "tasks": [
    {
      "id": "greeting",
      "prompt": "Create hello.py that prints hello followed by a newline.",
      "attachments": []
    }
  ]
}
```

Interactive API documentation is at [127.0.0.1:8000/docs](http://127.0.0.1:8000/docs); the schema is at `/openapi.json`. Routes cover task creation and history, clarification, cancellation, persisted server-sent events, attempt files, ZIP export, deletion, readiness, and benchmarks.

## Code layout

```text
backend/
  autocoder/
    api.py             Application factory and lifecycle
    routes/            Task, benchmark, and system endpoints
    services.py        Application service wiring
    orchestrator.py    Queue, coding loop, validation, and repair
    domain.py          Request, result, and event schemas
    database.py        SQLite records and persistence
    workspace.py       Snapshot, path, and export validation
    providers/         Ollama and Docker integrations
  tests/               Backend unit and Docker integration tests
frontend/
  src/
    components/        Task workspace and shared UI, with tests
    features/          Benchmark screen and tests
    hooks/             Live events, history, readiness, and layout
    lib/               API client and display utilities
    types/             API and workspace contracts
    test/              Shared test setup
sandbox/               Sandbox image definitions
scripts/               PowerShell image build helper
```

## Checks

```sh
uv run ruff check backend
uv run ruff format --check backend
uv run pytest -m "not integration"
npm --prefix frontend run lint
npm --prefix frontend run format:check
npm --prefix frontend test
npm --prefix frontend run build
```

With Docker running and the sandbox image built:

```sh
uv run pytest -m integration
```

Integration checks skip when their required images or Docker are unavailable. A real Ollama run is a separate manual acceptance check; unit tests use deterministic providers. CI runs the static checks, unit tests, frontend build, and Python sandbox integration checks.

## Troubleshooting

- **Backend is unreachable:** start `uv run forgeloop` and retry. The interface retains saved results while reconnecting.
- **No installed model:** run `ollama list`, install a model if needed, and refresh readiness. Readiness also refreshes automatically.
- **Sandbox image is missing:** rerun the Docker build command from Setup.
- **A run stops at the attempt limit:** inspect its failed test or execution result, then refine the prompt or attachments and start a new task.
- **The dashboard returns 404:** run `npm --prefix frontend run build` and restart the backend.
