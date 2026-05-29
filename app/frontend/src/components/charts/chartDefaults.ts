import type { Layout, Config } from 'plotly.js'
import { downloadSvgButton } from './chartExport'

export const MEASUREMENT_COLORS = [
  '#458A74', // sage green
  '#4E5689', // slate blue
  '#D9A421', // gold
  '#CC5B45', // terracotta
  '#6A8EC9', // periwinkle
  '#B46DA9', // mauve
  '#018B38', // forest green
]

export const CHART_COLORS = {
  primary: '#4E5689',
  steel:   '#458A74',
  pink:    '#B46DA9',
  success: '#018B38',
  warning: '#D9A421',
  error:   '#CC5B45',
}

const FONT_FAMILY = "'JetBrains Mono', 'Source Code Pro', 'Menlo', monospace"
const GRID_COLOR  = 'rgba(33, 60, 81, 0.1)'

export function plotlyLayout(overrides?: Partial<Layout>): Partial<Layout> {
  return {
    margin: { l: 60, r: 20, t: 16, b: 50 },
    paper_bgcolor: 'transparent',
    plot_bgcolor:  'transparent',
    font: { family: FONT_FAMILY, size: 11, color: CHART_COLORS.primary },
    xaxis: {
      gridcolor: GRID_COLOR,
      showspikes: true,
      spikemode: 'across',
      spikethickness: 1,
      spikecolor: 'rgba(45,45,45,0.3)',
      spikedash: 'dot',
      ...(overrides?.xaxis as object),
    },
    yaxis: {
      gridcolor: GRID_COLOR,
      showspikes: true,
      spikemode: 'across',
      spikethickness: 1,
      spikecolor: 'rgba(45,45,45,0.3)',
      spikedash: 'dot',
      ...(overrides?.yaxis as object),
    },
    hovermode: 'closest',
    legend: { x: 1.01, y: 1 },
    ...overrides,
  }
}

export const plotlyConfig: Partial<Config> = {
  responsive: true,
  displayModeBar: 'hover',
  displaylogo: false,
  modeBarButtonsToRemove: ['sendDataToCloud', 'lasso2d', 'select2d'],
  modeBarButtonsToAdd: ['toggleSpikelines', downloadSvgButton],
}

/** Full toolbar config for "Origin-like" interactive analysis charts */
export const plotlyConfigInteractive: Partial<Config> = {
  responsive: true,
  displayModeBar: true,
  displaylogo: false,
  modeBarButtonsToRemove: ['sendDataToCloud'],
  modeBarButtonsToAdd: ['toggleSpikelines', downloadSvgButton],
  scrollZoom: true,
}
