import '@testing-library/jest-dom'

// vi.mock is hoisted above imports — use async factory to avoid "React is not defined"
vi.mock('react-plotly.js', async () => {
  const { createElement } = await import('react')
  return {
    default: (props: Record<string, unknown>) =>
      createElement('div', { 'data-testid': 'plotly-chart', style: props.style as object }),
  }
})

Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
  value: () => null,
})

if (!window.URL.createObjectURL) {
  window.URL.createObjectURL = () => ''
}
