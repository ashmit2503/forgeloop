import { CaretDown } from '@phosphor-icons/react/dist/csr/CaretDown'
import { Check } from '@phosphor-icons/react/dist/csr/Check'
import { CheckCircle } from '@phosphor-icons/react/dist/csr/CheckCircle'
import { CircleNotch } from '@phosphor-icons/react/dist/csr/CircleNotch'
import { Code } from '@phosphor-icons/react/dist/csr/Code'
import { FileCode } from '@phosphor-icons/react/dist/csr/FileCode'
import { Globe } from '@phosphor-icons/react/dist/csr/Globe'
import { ListBullets } from '@phosphor-icons/react/dist/csr/ListBullets'
import { Play } from '@phosphor-icons/react/dist/csr/Play'
import { SidebarSimple } from '@phosphor-icons/react/dist/csr/SidebarSimple'
import { TerminalWindow } from '@phosphor-icons/react/dist/csr/TerminalWindow'
import { TestTube } from '@phosphor-icons/react/dist/csr/TestTube'
import { Warning } from '@phosphor-icons/react/dist/csr/Warning'
import { Wrench } from '@phosphor-icons/react/dist/csr/Wrench'
import { X } from '@phosphor-icons/react/dist/csr/X'
import { XCircle } from '@phosphor-icons/react/dist/csr/XCircle'
import { useEffect, useState } from 'react'
import type { Attempt, CommandResult, TaskDetail, ToolEvent } from '../types/api'
import { agentNoteFor, evidenceFor } from '../lib/attemptEvidence'
import { activePhaseLabel, buildAgentLog, formatDuration, taskIsTerminal } from '../lib/runModel'

export function AgentLog({
  task,
  selectedAttempt,
  collapsed,
  mobileOpen,
  onToggle,
  onMobileClose,
  onAttempt,
  onShowOutput,
}: {
  task: TaskDetail
  selectedAttempt: number | null
  collapsed: boolean
  mobileOpen: boolean
  onToggle: () => void
  onMobileClose: () => void
  onAttempt: (attempt: number) => void
  onShowOutput: (attempt: number) => void
}) {
  const entries = buildAgentLog(task)
  const [expanded, setExpanded] = useState<number | null>(selectedAttempt)
  const currentAttempt = task.active_attempt || task.attempts.at(-1)?.number || 1

  useEffect(() => setExpanded(selectedAttempt), [selectedAttempt])

  if (collapsed && !mobileOpen) {
    return (
      <aside className="agent-log collapsed" aria-label="Collapsed Agent Log">
        <button className="icon-button" type="button" onClick={onToggle} aria-label="Expand Agent">
          <SidebarSimple aria-hidden="true" />
        </button>
        <div className="collapsed-log-dots">
          {entries.slice(-8).map((entry) => (
            <button
              className={`log-dot ${entry.state}`}
              type="button"
              aria-label={`Attempt ${entry.attempt}: ${entry.label}`}
              onClick={() => entry.attempt && onAttempt(entry.attempt)}
              key={entry.id}
            >
              <span />
            </button>
          ))}
        </div>
      </aside>
    )
  }

  const toggleAttempt = (number: number) => {
    const next = expanded === number ? null : number
    setExpanded(next)
    if (next !== null) onAttempt(number)
  }

  return (
    <aside className={`agent-log${mobileOpen ? ' mobile-open' : ''}`} aria-label="Agent Log">
      <header className="agent-log-header">
        <div>
          <h2>Agent</h2>
          <span>
            {task.attempts.length} attempt{task.attempts.length === 1 ? '' : 's'}
          </span>
        </div>
        <button
          className="icon-button desktop-log-close"
          type="button"
          onClick={onToggle}
          aria-label="Collapse Agent"
        >
          <SidebarSimple aria-hidden="true" />
        </button>
        <button
          className="icon-button mobile-log-close"
          type="button"
          onClick={onMobileClose}
          aria-label="Close Agent"
        >
          <X aria-hidden="true" />
        </button>
      </header>
      {!taskIsTerminal(task) ? (
        <section className="active-repair" aria-label="Current agent activity">
          <div className="active-repair-heading">
            <CircleNotch className="spinning" aria-hidden="true" />
            <strong>{activePhaseLabel(task.status)}</strong>
            <span>
              {currentAttempt} / {task.max_retries + 1}
            </span>
          </div>
          <p>{activeDescription(task)}</p>
        </section>
      ) : null}
      <div className="attempt-groups">
        {task.attempts.map((attempt) => (
          <AttemptGroup
            attempt={attempt}
            task={task}
            expanded={expanded === attempt.number}
            onToggle={() => toggleAttempt(attempt.number)}
            onShowOutput={() => onShowOutput(attempt.number)}
            key={attempt.id}
          />
        ))}
        {task.attempts.length === 0 ? (
          <div className="attempts-empty">
            <CircleNotch className="spinning" aria-hidden="true" />
            <span>Waiting for the first workspace snapshot…</span>
          </div>
        ) : null}
      </div>
    </aside>
  )
}

function AttemptGroup({
  attempt,
  task,
  expanded,
  onToggle,
  onShowOutput,
}: {
  attempt: Attempt
  task: TaskDetail
  expanded: boolean
  onToggle: () => void
  onShowOutput: () => void
}) {
  const failed = attempt.status === 'failed'
  const running = attempt.status === 'running'
  const status = running ? attempt.phase || 'Running' : attempt.status
  const StatusIcon = running
    ? CircleNotch
    : failed
      ? XCircle
      : attempt.status === 'succeeded'
        ? CheckCircle
        : Warning

  return (
    <section className={`attempt-group ${attempt.status}${expanded ? ' expanded' : ''}`}>
      <button
        className="attempt-group-summary"
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={`attempt-${attempt.id}-details`}
      >
        <span className="attempt-status-icon">
          <StatusIcon
            className={running ? 'spinning' : ''}
            weight={failed ? 'fill' : 'regular'}
            aria-hidden="true"
          />
        </span>
        <span className="attempt-heading">
          <strong>Attempt {attempt.number}</strong>
          <small>{capitalize(status)}</small>
        </span>
        <time>{formatDuration(attempt.duration_ms)}</time>
        <CaretDown className="attempt-caret" aria-hidden="true" />
      </button>
      {expanded ? (
        <div className="attempt-details" id={`attempt-${attempt.id}-details`}>
          <section className="agent-note">
            <span>
              <Wrench aria-hidden="true" />
              Agent note
            </span>
            <p>{agentNoteFor(task, attempt)}</p>
          </section>
          {attempt.plan ? (
            <details className="attempt-plan" open={running}>
              <summary>
                {attempt.number > 1 ? 'Repair plan' : 'Attempt plan'}
                <CaretDown aria-hidden="true" />
              </summary>
              <p>{attempt.plan.summary}</p>
              <ol>
                {attempt.plan.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
              {attempt.plan.failure_response ? (
                <p className="failure-response">
                  <strong>Response to failure:</strong> {attempt.plan.failure_response}
                </p>
              ) : null}
            </details>
          ) : attempt.summary ? (
            <section className="attempt-plan">
              <span>{attempt.number > 1 ? 'Repair approach' : 'Workspace plan'}</span>
              <p>{attempt.summary}</p>
            </section>
          ) : null}
          {attempt.tool_events?.length ? <ToolTimeline events={attempt.tool_events} /> : null}
          {attempt.test_contract ? (
            <section className="official-test">
              <header>
                <TestTube aria-hidden="true" />
                <strong>Official test</strong>
                <span>{attempt.test_contract.deterministic ? 'Deterministic' : 'LLM judge'}</span>
              </header>
              <p>{attempt.test_contract.summary}</p>
              <dl>
                <div>
                  <dt>Expected</dt>
                  <dd>{attempt.test_contract.expected}</dd>
                </div>
                {attempt.test_contract.revision_reason ? (
                  <div className="test-revision">
                    <dt>Test revised</dt>
                    <dd>{attempt.test_contract.revision_reason}</dd>
                  </div>
                ) : null}
              </dl>
            </section>
          ) : null}
          {attempt.results.length ? (
            <div className="gate-list">
              {attempt.results.map((result, index) => (
                <GateResult result={result} attempt={attempt} key={`${result.phase}-${index}`} />
              ))}
            </div>
          ) : (
            <div className="gate-pending">
              <CircleNotch className={running ? 'spinning' : ''} aria-hidden="true" />
              <span>{running ? 'Waiting for test evidence…' : 'No gate evidence was recorded.'}</span>
            </div>
          )}
          {attempt.stdout || attempt.stderr ? (
            <button className="attempt-output-link" type="button" onClick={onShowOutput}>
              <TerminalWindow aria-hidden="true" />
              Open complete output
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}

function ToolTimeline({ events }: { events: ToolEvent[] }) {
  return (
    <section className="tool-timeline">
      <span>Agent actions · {events.length}</span>
      <ol>
        {events.map((event) => (
          <ToolRow event={event} key={event.index} />
        ))}
      </ol>
    </section>
  )
}

function ToolRow({ event }: { event: ToolEvent }) {
  const Icon =
    event.tool === 'web_search'
      ? Globe
      : event.tool === 'write_file' || event.tool === 'read_file'
        ? FileCode
        : event.tool === 'list_dir'
          ? ListBullets
          : event.tool === 'run_command' || event.tool === 'run_linter'
            ? TerminalWindow
            : Play
  return (
    <li className={`tool-row ${event.status}`}>
      <Icon aria-hidden="true" />
      <div>
        <strong>{event.label}</strong>
        <small>
          {event.tool.replace('_', ' ')} · {formatDuration(event.duration_ms)}
        </small>
        {event.detail ? (
          <details>
            <summary>Result</summary>
            <pre>{event.detail}</pre>
          </details>
        ) : null}
        {event.citations.length ? (
          <div className="tool-citations">
            {event.citations.map((citation) => (
              <a href={citation.url} target="_blank" rel="noreferrer" key={citation.url}>
                {citation.title}
              </a>
            ))}
          </div>
        ) : null}
      </div>
    </li>
  )
}

function GateResult({ result, attempt }: { result: CommandResult; attempt: Attempt }) {
  const evidence = evidenceFor(result)
  const expected =
    (result.phase === 'test' || result.phase === 'judge') && attempt.test_contract?.expected
      ? attempt.test_contract.expected
      : evidence.expected
  const Icon =
    result.phase === 'test' || result.phase === 'judge'
      ? TestTube
      : result.phase === 'install'
        ? TerminalWindow
        : result.phase === 'healthcheck'
          ? Check
          : Code
  return (
    <section className={`gate-result ${evidence.passed ? 'passed' : 'failed'}`}>
      <header>
        <span>
          <Icon aria-hidden="true" />
          <strong>{phaseTitle(result.phase)}</strong>
        </span>
        <span>
          {evidence.passed ? (
            <CheckCircle aria-hidden="true" />
          ) : (
            <XCircle weight="fill" aria-hidden="true" />
          )}
          {evidence.passed ? 'Passed' : result.timed_out ? 'Timed out' : `Exit ${result.exit_code ?? '—'}`}
        </span>
      </header>
      <code className="gate-command">{evidence.command}</code>
      <dl className="gate-comparison">
        <div>
          <dt>Expected</dt>
          <dd>{expected}</dd>
        </div>
        <div>
          <dt>Observed</dt>
          <dd>{evidence.actual}</dd>
        </div>
        <div className="difference">
          <dt>Difference</dt>
          <dd>{evidence.difference}</dd>
        </div>
      </dl>
    </section>
  )
}

function activeDescription(task: TaskDetail) {
  const activeAttempt = task.attempts.find((attempt) => attempt.number === task.active_attempt)
  if ((task.status === 'repairing' || task.status === 'generating') && activeAttempt?.summary)
    return activeAttempt.summary
  if (task.status === 'repairing')
    return 'Reading the failed gate and preparing the next validated workspace patch.'
  if (task.status === 'planning') return 'Creating a fresh, inspectable plan for this attempt.'
  if (task.status === 'intake')
    return 'Checking whether one clarification is essential before the run begins.'
  if (task.status === 'awaiting_clarification')
    return 'Waiting for your answer before starting the run timer.'
  if (task.status === 'generating')
    return `Creating a complete ${task.runtime === 'auto' ? 'project' : task.runtime} workspace with automated tests.`
  if (task.status === 'installing')
    return 'Installing dependencies while controlled network access is available.'
  if (task.status === 'testing')
    return task.execution_network
      ? 'Running the generated test suite with task-enabled network access.'
      : 'Running the generated test suite with network access disabled.'
  if (task.status === 'executing')
    return 'Running the final command or checking the configured health endpoint.'
  return 'Waiting for the current agent run.'
}

function phaseTitle(phase: CommandResult['phase']) {
  if (phase === 'install') return 'Install'
  if (phase === 'test') return 'Automated tests'
  if (phase === 'healthcheck') return 'Health check'
  if (phase === 'judge') return 'LLM judge'
  return 'Execution'
}

function capitalize(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1)
}
