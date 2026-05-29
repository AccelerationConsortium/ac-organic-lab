import type { EquipmentSnapshot } from '../types/api'
import { StatusPill } from './StatusPill'
import styles from './EquipmentCard.module.css'

export function EquipmentCard({ snap }: { snap: EquipmentSnapshot }) {
  const state = snap.fetch_error ? 'unreachable' : (snap.status?.equipment_status ?? 'unknown')
  return (
    <div className={styles.card} data-state={state}>
      <div className={styles.header}>
        <span className={styles.name}>{snap.name}</span>
        <StatusPill state={state} />
      </div>
      <div className={styles.id}>{snap.id}</div>
      {snap.fetch_error && (
        <div className={styles.error}>{String(snap.fetch_error).slice(0, 80)}</div>
      )}
    </div>
  )
}
