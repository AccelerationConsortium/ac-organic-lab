import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <div style={{ padding: 'var(--space-8)', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
      <div style={{ fontSize: 48, color: 'var(--text-muted)', marginBottom: 'var(--space-4)' }}>404</div>
      <p style={{ margin: '0 0 var(--space-4)', color: 'var(--text-tertiary)' }}>Page not found.</p>
      <Link to="/" style={{ color: 'var(--color-steel)', textDecoration: 'none', fontSize: 13 }}>
        ← Back to Overview
      </Link>
    </div>
  )
}
