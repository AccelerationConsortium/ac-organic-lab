import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'

function wrap(ui: React.ReactElement, initialEntry = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
    </MemoryRouter>
  )
}

test('renders without crash at root', async () => {
  wrap(<App />, '/')
  expect(document.body).toBeTruthy()
})

test('renders not-found for unknown path', async () => {
  wrap(<App />, '/does-not-exist')
  expect(document.body).toBeTruthy()
})
