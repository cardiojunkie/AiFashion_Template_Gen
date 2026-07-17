import { describe, expect, it } from 'vitest'
import { validateWorkbook } from '../pages/ImageDownloader'

describe('image workbook validation', () => {
  it('accepts supported workbooks and rejects unsafe input early', () => {
    expect(validateWorkbook({ name: 'catalog.XLSX', size: 10 })).toBe('')
    expect(validateWorkbook({ name: 'catalog.exe', size: 10 })).toMatch(/XLSX/)
    expect(validateWorkbook({ name: 'catalog.xlsx', size: 26 * 1024 * 1024 })).toMatch(/25 MB/)
  })
})
