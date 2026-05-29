import { render } from '@testing-library/react'
import { FailureTimeline } from './FailureTimeline'

const mockEvents = [
  { id: 1, ts: '2026-05-01T10:00:00', device_id: 'ot2',      event_type: 'state_transition', to_state: 'error',       message: 'COM timeout' },
  { id: 2, ts: '2026-05-02T12:00:00', device_id: 'plateloc', event_type: 'state_transition', to_state: 'unreachable', message: '' },
]

test('renders without crashing with failure events', () => {
  const { container } = render(<FailureTimeline events={mockEvents} />)
  expect(container.firstChild).toBeTruthy()
})

test('renders without crashing with empty events', () => {
  const { container } = render(<FailureTimeline events={[]} />)
  expect(container.firstChild).toBeTruthy()
})
