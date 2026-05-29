import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockFetch = vi.fn()
global.fetch = mockFetch

describe('apiClient.get', () => {
  beforeEach(() => mockFetch.mockReset())

  it('returns parsed JSON on 200', async () => {
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({ equipment: [] }) })
    const { apiClient } = await import('./api')
    const result = await apiClient.get('/api/equipment')
    expect(result).toEqual({ equipment: [] })
  })

  it('throws on non-200', async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 503, statusText: 'Service Unavailable' })
    const { apiClient } = await import('./api')
    await expect(apiClient.get('/api/equipment')).rejects.toThrow('503')
  })
})
