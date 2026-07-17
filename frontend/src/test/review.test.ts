import { describe, expect, it } from 'vitest'
import { buildReviewSearch } from '../pages/ReviewPage'

describe('review filters', () => {
  it('sends pagination, filters, and sorting to the server', () => {
    const query = new URLSearchParams(buildReviewSearch({ search: '00123', status: 'blocked', source: 'vision', minConfidence: '0.75' }, 3, 50, 'sku', true))
    expect(Object.fromEntries(query)).toEqual({ page: '3', page_size: '50', sort: 'sku', order: 'desc', search: '00123', status: 'blocked', source: 'vision', min_confidence: '0.75' })
  })
})
