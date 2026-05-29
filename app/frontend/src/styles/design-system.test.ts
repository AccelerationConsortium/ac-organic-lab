import { readFileSync } from 'node:fs'
import { join } from 'node:path'

describe('design system tokens', () => {
  it('index.css exports expected custom properties', () => {
    const css = readFileSync(join(__dirname, 'index.css'), 'utf-8')
    expect(css).toContain('--color-navy')
    expect(css).toContain('--color-steel')
    expect(css).toContain('--font-mono')
  })
})
