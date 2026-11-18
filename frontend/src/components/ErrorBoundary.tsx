import React from 'react'

interface Props {
  children: React.ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[frontend:error-boundary]', {
      message: error.message,
      componentStack: info.componentStack,
    })
  }

  render() {
    if (this.state.error) {
      return (
        <main className="fatal-error" role="alert">
          <strong>页面发生异常</strong>
          <p>页面未能正常显示。可以重试当前页面。</p>
          <button className="button-primary" type="button" onClick={() => this.setState({ error: null })}>
            重试
          </button>
        </main>
      )
    }
    return this.props.children
  }
}
