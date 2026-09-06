import { ArrowRight } from '@phosphor-icons/react/dist/csr/ArrowRight'
import { Question } from '@phosphor-icons/react/dist/csr/Question'
import { useState } from 'react'
import type { FormEvent } from 'react'
import type { TaskDetail } from '../types/api'

export function ClarificationPrompt({
  task,
  onAnswer,
}: {
  task: TaskDetail
  onAnswer: (answer: string) => Promise<void>
}) {
  const [answer, setAnswer] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!answer.trim() || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await onAnswer(answer.trim())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not send the clarification.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="new-task-view clarification-view">
      <section className="new-task-panel clarification-panel">
        <header className="composer-heading">
          <h1>One detail needed.</h1>
          <p>The timer and attempt counter stay paused until you answer.</p>
        </header>
        <form onSubmit={(event) => void submit(event)}>
          <div className="clarification-thread">
            <div className="clarification-message user-message">
              <span>You</span>
              <p>{task.prompt}</p>
            </div>
            <div className="clarification-message agent-message">
              <span>
                <Question aria-hidden="true" />
                Agent
              </span>
              <p>{task.clarification_question}</p>
            </div>
          </div>
          <div className="composer-box clarification-composer">
            <label className="prompt-field">
              <span className="sr-only">Your clarification</span>
              <textarea
                value={answer}
                onChange={(event) => setAnswer(event.target.value)}
                rows={4}
                autoFocus
                maxLength={20_000}
                placeholder="Reply with the missing detail…"
              />
            </label>
            <div className="composer-controls">
              <span className="clarification-kicker">
                <Question aria-hidden="true" />
                Attempt 1 has not started
              </span>
              <button className="run-task-button" type="submit" disabled={!answer.trim() || submitting}>
                <ArrowRight aria-hidden="true" />
                {submitting ? 'Sending…' : 'Continue run'}
              </button>
            </div>
          </div>
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
        </form>
      </section>
    </main>
  )
}
