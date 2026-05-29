import Plot from 'react-plotly.js'
import { plotlyLayout, plotlyConfig, MEASUREMENT_COLORS } from './chartDefaults'
import type { SensorPoint } from '../../services/historyApi'

interface Series { label: string; readings: SensorPoint[] }

export function EnergyChart({ series }: { series: Series[] }) {
  const data = series.map((s, i) => ({
    type: 'scatter' as const,
    mode: 'lines' as const,
    x: s.readings.map(r => r.ts),
    y: s.readings.map(r => r.value),
    name: s.label,
    line: { color: MEASUREMENT_COLORS[i % MEASUREMENT_COLORS.length], width: 1.5 },
  }))
  return (
    <Plot
      data={data}
      layout={plotlyLayout({ title: { text: 'Cumulative Energy (kWh)' }, yaxis: { title: { text: 'kWh' } } })}
      config={plotlyConfig}
      style={{ width: '100%', height: 300 }}
    />
  )
}
