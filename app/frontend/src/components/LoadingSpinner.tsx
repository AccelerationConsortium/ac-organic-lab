import { Loader2 } from 'lucide-react'

export default function LoadingSpinner() {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: '400px',
      gap: '12px',
      color: 'var(--color-steel)',
    }}>
      <Loader2 size={32} style={{ animation: 'spin 1s linear infinite' }} />
      <span style={{ fontSize: '14px' }}>Loading...</span>
    </div>
  )
}
