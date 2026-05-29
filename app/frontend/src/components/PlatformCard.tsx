import { Link } from 'react-router-dom'
import { StatusPill } from './StatusPill'
import type { EquipmentSnapshot, PlatformSection } from '../types/api'
import styles from './PlatformCard.module.css'

interface Props {
  section: PlatformSection
  snapshots: EquipmentSnapshot[]
}

export function PlatformCard({ section, snapshots }: Props) {
  const reachable = snapshots.filter(s => !s.fetch_error).length
  return (
    <div className={styles.card}>
      <div className={styles.header}>
        <h2 className={styles.title}>{section.title}</h2>
        {section.href && <Link to={section.href} className={styles.link}>View →</Link>}
      </div>
      <p className={styles.sub}>{reachable}/{snapshots.length} devices reachable</p>
      <div className={styles.pills}>
        {snapshots.map(s => (
          <div key={s.id} className={styles.pillRow}>
            <span className={styles.deviceName}>{s.name}</span>
            <StatusPill state={s.fetch_error ? 'unreachable' : (s.status?.equipment_status ?? 'unknown')} />
          </div>
        ))}
      </div>
    </div>
  )
}
