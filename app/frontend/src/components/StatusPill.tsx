import styles from '../styles/components.module.css'

const STATE_LABELS: Record<string, string> = {
  ready: 'Ready',
  busy: 'Busy',
  requires_init: 'Init Required',
  error: 'Error',
  unreachable: 'Offline',
  degraded: 'Degraded',
  dry_run: 'Dry Run',
  e_stop: 'E-Stop',
  maintenance: 'Maintenance',
  unknown: 'Unknown',
}

export function StatusPill({ state }: { state: string }) {
  return (
    <span className={styles.statusPill} data-state={state}>
      {STATE_LABELS[state] ?? state}
    </span>
  )
}
