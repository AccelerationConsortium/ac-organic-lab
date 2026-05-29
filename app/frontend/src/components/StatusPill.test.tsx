import { render, screen } from '@testing-library/react'
import { StatusPill } from './StatusPill'

test('renders "Ready" for ready state', () => {
  render(<StatusPill state="ready" />)
  expect(screen.getByText('Ready')).toBeInTheDocument()
})

test('renders "Offline" for unreachable state', () => {
  render(<StatusPill state="unreachable" />)
  expect(screen.getByText('Offline')).toBeInTheDocument()
})

test('falls back to raw state string for unknown states', () => {
  render(<StatusPill state="calibrating" />)
  expect(screen.getByText('calibrating')).toBeInTheDocument()
})
