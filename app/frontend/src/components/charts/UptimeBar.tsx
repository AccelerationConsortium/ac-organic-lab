import Plot from 'react-plotly.js'
import { plotlyLayout, plotlyConfig } from './chartDefaults'
import type { UptimeResponse } from '../../services/historyApi'

export function UptimeBar({ devices }: { devices: UptimeResponse[] }) {
  const data = [{
    type: 'bar' as const,
    x: devices.map(d => d.uptime_pct),
    y: devices.map(d => d.device_id),
    orientation: 'h' as const,
    marker: {
      color: devices.map(d =>
        d.uptime_pct >= 99 ? '#018B38' :
        d.uptime_pct >= 95 ? '#D9A421' : '#CC5B45'
      ),
    },
  }]
  return (
    <Plot
      data={data}
      layout={plotlyLayout({ title: { text: 'Device Uptime %' }, xaxis: { range: [0, 100] } })}
      config={plotlyConfig}
      style={{ width: '100%', height: 320 }}
    />
  )
}
