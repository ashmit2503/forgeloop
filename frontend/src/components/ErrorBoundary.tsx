import { Warning } from '@phosphor-icons/react/dist/csr/Warning'
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface ErrorBoundaryState {
  failed: boolean
}

export class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ForgeLoop interface failure', error, info.componentStack)
  }

  render() {
    if (!this.state.failed) return this.props.children

    return (
      <main className="fatal-error" role="alert">
        <Warning weight="fill" aria-hidden="true" />
        <h1>The interface stopped unexpectedly.</h1>
        <p>Your saved tasks are safe. Reload ForgeLoop to reconnect to the local service.</p>
        <button type="button" onClick={() => window.location.reload()}>
          Reload app
        </button>
      </main>
    )
  }
}
