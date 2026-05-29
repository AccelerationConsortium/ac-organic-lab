import { useState } from 'react'
import { useDeviceEvents } from '../hooks/useHistory'
import { FailureTimeline } from '../components/charts/FailureTimeline'
import styles from './Alerts.module.css'

const DEVICES = [
  'xarm_translocation', 'ot2', 'dose_every_well', 'filter_every_well',
  'plateloc', 'fume_hood_actuator', 'torry_pines_shaker',
]

function DeviceEventRow({ deviceId }: { deviceId: string }) {
  const { data } = useDeviceEvents(deviceId)
  const failures = data?.events.filter(e =>
    ['error', 'unreachable', 'e_stop'].includes(e.to_state ?? '')
  ) ?? []
  return (
    <tr>
      <td className={styles.mono}>{deviceId}</td>
      <td className={styles.mono}>{failures.length}</td>
      <td className={styles.mono}>{failures[0]?.ts ? new Date(failures[0].ts).toLocaleDateString() : '—'}</td>
      <td>{failures[0]?.message ?? '—'}</td>
    </tr>
  )
}

export default function Alerts() {
  const [selectedDevice, setSelectedDevice] = useState(DEVICES[0])
  const { data: events } = useDeviceEvents(selectedDevice)

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Alerts &amp; Failures</h1>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Fleet Failure Summary</h2>
        <table className={styles.table}>
          <thead><tr><th>Device</th><th>Failures</th><th>Last Failure</th><th>Last Message</th></tr></thead>
          <tbody>{DEVICES.map(d => <DeviceEventRow key={d} deviceId={d} />)}</tbody>
        </table>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h2 className={styles.sectionTitle}>Failure Timeline</h2>
          <select
            className={styles.select}
            value={selectedDevice}
            onChange={e => setSelectedDevice(e.target.value)}
          >
            {DEVICES.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>
        {events && <FailureTimeline events={events.events} />}
      </section>
    </div>
  )
}
