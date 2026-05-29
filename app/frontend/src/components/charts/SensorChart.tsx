import Plot from 'react-plotly.js'
import { plotlyLayout, plotlyConfig, MEASUREMENT_COLORS } from './chartDefaults'
import type { SensorPoint } from '../../services/historyApi'

interface Props { sensorId: string; metric: string; readings: SensorPoint[] }

export function SensorChart({ sensorId, metric, readings }: Props) {
  return (
    <Plot
      data={[{
        type: 'scatter',
        mode: 'lines',
        x: readings.map(r => r.ts),
        y: readings.map(r => r.value),
        name: `${sensorId} · ${metric}`,
        line: { color: MEASUREMENT_COLORS[0], width: 1.5 },
      }]}
      layout={plotlyLayout({
        title: { text: `${sensorId} — ${metric}` },
        yaxis: { title: { text: readings[0]?.unit ?? '' } },
      })}
      config={plotlyConfig}
      style={{ width: '100%', height: 240 }}
    />
  )
}
