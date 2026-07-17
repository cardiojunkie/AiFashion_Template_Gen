import { describe, expect, it } from 'vitest'
import { blockingIssues, Preflight } from '../pages/RunsPage'

describe('runs preflight', () => {
  it('blocks only error-severity issues', () => {
    const result: Preflight = { id: 'preflight-1', rows: 2, groups: 1, image_urls: 0, valid: false, issues: [{ blocking: false, message: 'image missing' }, { blocking: true, message: 'mixed attribute sets' }] }
    expect(blockingIssues(result)).toBe(1)
  })
})
