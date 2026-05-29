import { Component, type ReactNode } from 'react'
import { AlertCircle, RefreshCw } from 'lucide-react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          minHeight: '100vh',
          gap: '16px',
          padding: '32px',
          fontFamily: "'Source Serif 4', 'Charter', 'Georgia', serif",
          color: '#2d2d2d',
          background: '#f7f6f4',
        }}>
          <AlertCircle size={48} color="#CC5B45" />
          <h1 style={{ fontSize: '20px', fontWeight: 600 }}>Something went wrong</h1>
          <p style={{ fontSize: '14px', color: '#848484', maxWidth: '400px', textAlign: 'center' }}>
            {this.state.error?.message || 'An unexpected error occurred'}
          </p>
          <button
            onClick={() => window.location.reload()}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '10px 20px',
              background: '#4E5689',
              color: '#ffffff',
              border: 'none',
              borderRadius: '8px',
              fontSize: '14px',
              fontFamily: "'Source Serif 4', 'Charter', 'Georgia', serif",
              cursor: 'pointer',
            }}
          >
            <RefreshCw size={16} />
            Reload Page
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
