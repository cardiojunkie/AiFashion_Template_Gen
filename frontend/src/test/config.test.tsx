import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Markdown } from '../components'
import { configDefinitions, serializeConfig } from '../pages/ConfigPage'

describe('config', () => {
  it('keeps the shared configuration surface small', () => {
    expect(Object.keys(configDefinitions)).toEqual(['headers', 'prompts', 'attribute-sets', 'value-lists', 'mapping-profiles'])
    expect(serializeConfig('value-lists', { name: 'Color', description: '', values: 'Red|Crimson\nBlue', fuzzy_matching: 'false', multiselect_delimiter: '|' })).toMatchObject({ items: [{ canonical_value: 'Red', aliases: ['Crimson'] }, { canonical_value: 'Blue', aliases: [] }] })
  })

  it('sanitizes prompt markdown', () => {
    const { container } = render(<Markdown value={'# Safe\n<script>alert(1)</script>'} />)
    expect(screen.getByRole('heading', { name: 'Safe' })).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
  })
})
