import type { EquipmentSnapshot } from '../types/api'
import { EquipmentCard } from './EquipmentCard'
import styles from './EquipmentGrid.module.css'

export function EquipmentGrid({ snapshots }: { snapshots: EquipmentSnapshot[] }) {
  if (snapshots.length === 0) {
    return (
      <p className={styles.empty}>No equipment configured for this platform.</p>
    )
  }
  return (
    <div className={styles.grid}>
      {snapshots.map(s => <EquipmentCard key={s.id} snap={s} />)}
    </div>
  )
}
