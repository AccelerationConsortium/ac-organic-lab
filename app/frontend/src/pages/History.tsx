import { useState } from 'react'
import { useAllUptime, useLatestSensors, useRuns, useSensorHistory } from '../hooks/useHistory'
import { UptimeBar } from '../components/charts/UptimeBar'
import { SensorChart } from '../components/charts/SensorChart'
import styles from './History.module.css'

const SENSOR_IDS = ['env_sample_prep', 'env_storage', 'env_lab499_west', 'env_lab499_east']
const METRICS    = ['temperature_c', 'humidity_pct', 'co2_ppm']

export default function History() {
  const [days, setDays]               = useState(30)
  const [activeSensor, setActiveSensor] = useState(SENSOR_IDS[0])
  const [activeMetric, setActiveMetric] = useState(METRICS[0])

  const { data: uptimeData } = useAllUptime(days)
  const { data: sensorData } = useSensorHistory(activeSensor, activeMetric, days * 24)
  const { data: runsData }   = useRuns(50)

  // suppress unused warning — available for future "latest readings" panel
  void useLatestSensors()

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Observability</h1>

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>Device Uptime</h2>
          <select className={styles.select} value={days} onChange={e => setDays(+e.target.value)}>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
          </select>
        </div>
        {uptimeData && <UptimeBar devices={uptimeData.devices} />}
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>Environmental Sensors</h2>
          <div className={styles.controls}>
            <select className={styles.select} value={activeSensor} onChange={e => setActiveSensor(e.target.value)}>
              {SENSOR_IDS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <select className={styles.select} value={activeMetric} onChange={e => setActiveMetric(e.target.value)}>
              {METRICS.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        </div>
        {sensorData && <SensorChart sensorId={activeSensor} metric={activeMetric} readings={sensorData.readings} />}
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Recent Runs</h2>
        <table className={styles.table}>
          <thead><tr><th>ID</th><th>Device</th><th>Started</th><th>Status</th><th>Wells</th></tr></thead>
          <tbody>
            {runsData?.runs.map(run => (
              <tr key={run.id}>
                <td className={styles.mono}>{run.id.slice(0, 8)}</td>
                <td>{run.device_id}</td>
                <td className={styles.mono}>{new Date(run.started_at).toLocaleString()}</td>
                <td><span className={styles.badge} data-status={run.status}>{run.status}</span></td>
                <td className={styles.mono}>{run.n_converged}/{run.n_wells}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
