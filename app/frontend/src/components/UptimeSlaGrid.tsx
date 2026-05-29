import type { UptimeResponse } from '../services/historyApi'
import styles from './UptimeSlaGrid.module.css'

function slaColor(pct: number) {
  if (pct >= 99) return 'var(--color-success)'
  if (pct >= 95) return 'var(--color-warning)'
  return 'var(--color-error)'
}

export function UptimeSlaGrid({ devices }: { devices: UptimeResponse[] }) {
  return (
    <div className={styles.grid}>
      {devices.map(d => (
        <div key={d.device_id} className={styles.card}>
          <div className={styles.deviceId}>{d.device_id}</div>
          <div className={styles.pct} style={{ color: slaColor(d.uptime_pct) }}>
            {d.uptime_pct.toFixed(1)}%
          </div>
          <div className={styles.label}>uptime ({d.days}d)</div>
        </div>
      ))}
    </div>
  )
}
