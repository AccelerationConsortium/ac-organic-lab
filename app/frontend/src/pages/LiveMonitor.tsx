import { useEquipmentList } from '../hooks/useEquipment'
import { useAllUptime } from '../hooks/useHistory'
import { UptimeSlaGrid } from '../components/UptimeSlaGrid'
import { StatusPill } from '../components/StatusPill'
import styles from './LiveMonitor.module.css'

export default function LiveMonitor() {
  const { data: equipment } = useEquipmentList()
  const { data: uptimeData } = useAllUptime(30)

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Live Monitor</h1>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Uptime SLA — 30 days</h2>
        {uptimeData && <UptimeSlaGrid devices={uptimeData.devices} />}
        {!uptimeData && <p className={styles.empty}>Loading uptime data…</p>}
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Current Status</h2>
        <div className={styles.statusGrid}>
          {equipment?.equipment.map(e => (
            <div key={e.id} className={styles.statusRow}>
              <span className={styles.name}>{e.name}</span>
              <StatusPill state={e.fetch_error ? 'unreachable' : (e.status?.equipment_status ?? 'unknown')} />
            </div>
          ))}
          {!equipment && <p className={styles.empty}>Loading equipment…</p>}
        </div>
      </section>
    </div>
  )
}
