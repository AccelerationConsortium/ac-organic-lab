import Plot from 'react-plotly.js'
import { plotlyLayout, plotlyConfig } from './chartDefaults'
import type { EquipmentEvent } from '../../services/historyApi'

export function FailureTimeline({ events }: { events: EquipmentEvent[] }) {
  const failures = events.filter(e =>
    e.to_state === 'error' || e.to_state === 'unreachable' || e.to_state === 'e_stop'
  )

  const data = [{
    type: 'scatter' as const,
    mode: 'markers' as const,
    x: failures.map(e => e.ts),
    y: failures.map(e => e.device_id),
    text: failures.map(e => `${e.to_state}: ${e.message ?? ''}`),
    marker: {
      color: failures.map(e => e.to_state === 'error' ? '#CC5B45' : '#D9A421'),
      size: 10,
      symbol: 'x' as const,
    },
    hovertemplate: '<b>%{y}</b><br>%{x}<br>%{text}<extra></extra>',
  }]

  return (
    <Plot
      data={data}
      layout={plotlyLayout({ title: { text: 'Failure Events' } })}
      config={plotlyConfig}
      style={{ width: '100%', height: 280 }}
    />
  )
}
