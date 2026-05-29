import { useEquipmentList, usePlatforms } from '../hooks/useEquipment'
import { PlatformCard } from '../components/PlatformCard'
import type { EquipmentSnapshot } from '../types/api'
import styles from './Overview.module.css'

export default function Overview() {
  const { data: equipment } = useEquipmentList()
  const { data: platforms } = usePlatforms()

  if (!equipment || !platforms) {
    return <div className={styles.loading}>Loading…</div>
  }

  const byId = new Map<string, EquipmentSnapshot>(
    equipment.equipment.map(s => [s.id, s])
  )

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Lab Overview</h1>
      <div className={styles.grid}>
        {platforms.sections
          .filter(s => s.kind === 'platform')
          .map(section => {
            const snaps = section.equipment
              .map(id => byId.get(id))
              .filter((s): s is EquipmentSnapshot => s !== undefined)
            return <PlatformCard key={section.id} section={section} snapshots={snaps} />
          })}
      </div>
    </div>
  )
}
