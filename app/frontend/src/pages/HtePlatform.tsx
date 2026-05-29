import { useEquipmentList, usePlatforms } from '../hooks/useEquipment'
import { EquipmentGrid } from '../components/EquipmentGrid'
import type { EquipmentSnapshot } from '../types/api'
import styles from './HtePlatform.module.css'

export default function HtePlatform() {
  const { data } = useEquipmentList()
  const { data: platforms } = usePlatforms()

  const hteIds: string[] = platforms?.sections.find(s => s.id === 'hte')?.equipment ?? []
  const byId = new Map<string, EquipmentSnapshot>(data?.equipment.map(s => [s.id, s]) ?? [])
  const snapshots = hteIds.map(id => byId.get(id)).filter((s): s is EquipmentSnapshot => !!s)

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>HTE Platform</h1>
      <p className={styles.sub}>{snapshots.length} devices configured</p>
      <EquipmentGrid snapshots={snapshots} />
    </div>
  )
}
