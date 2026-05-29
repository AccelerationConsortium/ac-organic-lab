import { useSensorHistory } from '../hooks/useHistory'
import { EnergyChart } from '../components/charts/EnergyChart'
import styles from './Energy.module.css'

const PLUG_OUTLETS = [
  { sensor: 'plug_hte_strip_right', metric: 'energy_kwh_cumul_outlet_1', label: 'HTE Strip R · Outlet 1' },
  { sensor: 'plug_hte_strip_right', metric: 'energy_kwh_cumul_outlet_2', label: 'HTE Strip R · Outlet 2' },
  { sensor: 'plug_hte_strip_left',  metric: 'energy_kwh_cumul_outlet_1', label: 'HTE Strip L · Outlet 1' },
]

export default function Energy() {
  const outlet0 = useSensorHistory(PLUG_OUTLETS[0].sensor, PLUG_OUTLETS[0].metric, 168)
  const outlet1 = useSensorHistory(PLUG_OUTLETS[1].sensor, PLUG_OUTLETS[1].metric, 168)
  const outlet2 = useSensorHistory(PLUG_OUTLETS[2].sensor, PLUG_OUTLETS[2].metric, 168)

  const series = [
    outlet0.data ? { label: PLUG_OUTLETS[0].label, readings: outlet0.data.readings } : null,
    outlet1.data ? { label: PLUG_OUTLETS[1].label, readings: outlet1.data.readings } : null,
    outlet2.data ? { label: PLUG_OUTLETS[2].label, readings: outlet2.data.readings } : null,
  ].filter(Boolean) as { label: string; readings: { ts: string; value: number; unit: string }[] }[]

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Energy</h1>
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Cumulative Energy — 7 days</h2>
        {series.length > 0
          ? <EnergyChart series={series} />
          : <p className={styles.empty}>No energy data yet. Ensure kasa-tapo-services is running and plug_hte_strip_* devices are reachable.</p>
        }
      </section>
    </div>
  )
}
