import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ColumnDef, flexRender, getCoreRowModel, RowSelectionState, useReactTable } from '@tanstack/react-table'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ApiError, Page as ApiPage, api, asPage, json } from '../api'
import { Empty, Field, Notice, Page, StatusBadge } from '../components'

type Provenance = { source?: string; confidence?: number; model?: string; prompt_version?: string; images?: string[]; usage?: unknown; cost?: number }
export type ReviewRow = { id: string; sku: string; base_code: string; status: string; confidence?: number; row_version: number; fields: Record<string, string | null>; provenance?: Record<string, Provenance> }
type ReviewApiRow = Omit<ReviewRow, 'fields'> & { data?: Record<string, string | null>; fields?: Record<string, string | null> }
export type ReviewFilters = { search?: string; status?: string; source?: string; minConfidence?: string }

export function buildReviewSearch(filters: ReviewFilters, page: number, pageSize: number, sort: string, descending: boolean) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize), sort, order: descending ? 'desc' : 'asc' })
  if (filters.search) query.set('search', filters.search)
  if (filters.status) query.set('status', filters.status)
  if (filters.source) query.set('source', filters.source)
  if (filters.minConfidence) query.set('min_confidence', filters.minConfidence)
  return query.toString()
}

function EditableCell({ row, field, onSave, onInspect }: { row: ReviewRow; field: string; onSave: (row: ReviewRow, field: string, value: string) => void; onInspect: () => void }) {
  const sourceValue = row.fields[field] ?? ''
  const [value, setValue] = useState(String(sourceValue))
  useEffect(() => setValue(String(sourceValue)), [sourceValue])
  const commit = () => { if (value !== String(sourceValue)) onSave(row, field, value) }
  const source = row.provenance?.[field]?.source
  return <div className="min-w-44"><div className="flex items-center gap-1"><input className="control min-h-8 py-1" aria-label={`${field} for ${row.sku}`} value={value} onChange={event => setValue(event.target.value)} onBlur={commit} onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') { setValue(String(sourceValue)); event.currentTarget.blur() } }} /><button type="button" className="rounded px-2 py-1 text-xs text-moss hover:bg-emerald-50" aria-label={`Show ${field} provenance for ${row.sku}`} title={source || 'No provenance'} onClick={onInspect}>ⓘ</button></div>{source && <span className="mt-1 inline-block rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-600">{source}</span>}</div>
}

type Undo = { rowId: string; field: string; value: string }
type EditRequest = { rowId: string; field: string; value: string; rowVersion: number; previous?: string; clearsUndo?: boolean }

export function ReviewPage() {
  const client = useQueryClient()
  const [params, setParams] = useSearchParams()
  const runId = params.get('run') || ''
  const pageNumber = Math.max(1, Number(params.get('page')) || 1)
  const pageSize = 50
  const filters = { search: params.get('search') || '', status: params.get('status') || '', source: params.get('source') || '', minConfidence: params.get('min_confidence') || '' }
  const [sort, setSort] = useState('sku')
  const [descending, setDescending] = useState(false)
  const [selection, setSelection] = useState<RowSelectionState>({})
  const [inspected, setInspected] = useState<{ row: ReviewRow; field: string }>()
  const [undo, setUndo] = useState<Undo>()
  const [conflict, setConflict] = useState(false)
  const [bulkField, setBulkField] = useState('')
  const [bulkValue, setBulkValue] = useState('')
  const queryString = buildReviewSearch(filters, pageNumber, pageSize, sort, descending)
  const rows = useQuery({
    queryKey: ['review', runId, queryString],
    queryFn: () => api<ApiPage<ReviewApiRow> | ReviewApiRow[]>(`/runs/${encodeURIComponent(runId)}/review?${queryString}`).then(value => { const page = asPage(value); return { ...page, items: page.items.map(row => ({ ...row, fields: row.fields || row.data || {} })) as ReviewRow[] } }),
    enabled: Boolean(runId),
  })
  const edit = useMutation({
    mutationFn: ({ rowId, field, value, rowVersion }: EditRequest) => api<ReviewRow>(`/review/items/${rowId}`, json('PATCH', { changes: { [field]: value }, row_version: rowVersion })),
    onSuccess: (_, request) => { if (request.clearsUndo) setUndo(undefined); else if (request.previous !== undefined) setUndo({ rowId: request.rowId, field: request.field, value: request.previous }); setConflict(false); client.invalidateQueries({ queryKey: ['review', runId] }) },
    onError: error => setConflict(error instanceof ApiError && error.status === 409),
  })
  const bulk = useMutation({
    mutationFn: () => api('/review/bulk', json('POST', { edits: (rows.data?.items || []).filter(row => selection[row.id]).map(row => ({ item_id: row.id, row_version: row.row_version, changes: { [bulkField]: bulkValue } })) })),
    onSuccess: () => { setSelection({}); setBulkValue(''); client.invalidateQueries({ queryKey: ['review', runId] }) },
  })
  const fieldNames = useMemo(() => Array.from(new Set((rows.data?.items || []).flatMap(row => Object.keys(row.fields)))).sort(), [rows.data])
  useEffect(() => { if (!bulkField && fieldNames[0]) setBulkField(fieldNames[0]) }, [bulkField, fieldNames])
  const changeSort = (key: string) => { if (sort === key) setDescending(value => !value); else { setSort(key); setDescending(false) } }
  const header = (label: string, key: string) => <button className="font-semibold hover:text-moss" onClick={() => changeSort(key)}>{label}{sort === key ? (descending ? ' ↓' : ' ↑') : ''}</button>
  const save = (row: ReviewRow, field: string, value: string) => {
    edit.mutate({ rowId: row.id, field, value, rowVersion: row.row_version, previous: String(row.fields[field] ?? '') })
  }
  const columns = useMemo<ColumnDef<ReviewRow>[]>(() => [
    { id: 'select', header: ({ table }) => <input type="checkbox" aria-label="Select all visible rows" checked={table.getIsAllPageRowsSelected()} ref={node => { if (node) node.indeterminate = table.getIsSomePageRowsSelected() }} onChange={table.getToggleAllPageRowsSelectedHandler()} />, cell: ({ row }) => <input type="checkbox" aria-label={`Select ${row.original.sku}`} checked={row.getIsSelected()} onChange={row.getToggleSelectedHandler()} /> },
    { id: 'sku', header: () => header('SKU', 'sku'), cell: ({ row }) => <span className="font-mono text-xs">{row.original.sku}</span> },
    { id: 'base_code', header: () => header('Base code', 'base_code'), cell: ({ row }) => row.original.base_code },
    { id: 'status', header: () => header('Status', 'status'), cell: ({ row }) => <StatusBadge value={row.original.status} /> },
    { id: 'confidence', header: () => header('Confidence', 'confidence'), cell: ({ row }) => row.original.confidence == null ? '—' : `${Math.round(row.original.confidence * 100)}%` },
    ...fieldNames.map(field => ({ id: field, header: field, cell: ({ row }: { row: { original: ReviewRow } }) => <EditableCell row={row.original} field={field} onSave={save} onInspect={() => setInspected({ row: row.original, field })} /> })),
  ], [descending, fieldNames, sort])
  const table = useReactTable({ data: rows.data?.items || [], columns, getCoreRowModel: getCoreRowModel(), getRowId: row => row.id, enableRowSelection: true, onRowSelectionChange: setSelection, state: { rowSelection: selection }, manualPagination: true, pageCount: rows.data ? Math.ceil(rows.data.total / pageSize) : 0 })
  const setFilter = (key: string, value: string) => { const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); next.set('page', '1'); setParams(next, { replace: true }) }
  const goToPage = (page: number) => { const next = new URLSearchParams(params); next.set('page', String(page)); setParams(next, { replace: true }) }
  const latestRow = undo && rows.data?.items.find(row => row.id === undo.rowId)

  return <Page title="Review" description="Rows stay server-paginated; edits include the current row version to reject stale writes." actions={undo ? <button className="btn-secondary" disabled={edit.isPending || !latestRow} onClick={() => { if (latestRow) edit.mutate({ rowId: undo.rowId, field: undo.field, value: undo.value, rowVersion: latestRow.row_version, clearsUndo: true }) }}>Undo {undo.field}</button> : undefined}>
    {!runId && <Notice>Select Review from a run, or paste a run ID below.</Notice>}
    {conflict && <Notice tone="error">This row changed elsewhere. <button className="underline" onClick={() => rows.refetch()}>Reload the latest row</button> before editing again.</Notice>}
    {(edit.isError && !conflict || bulk.isError) && <Notice tone="error">{(edit.error || bulk.error)?.message}</Notice>}
    <div className="card grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <Field label="Run ID"><input className="control" value={runId} onChange={event => setFilter('run', event.target.value)} placeholder="UUID" /></Field>
      <Field label="Search"><input className="control" type="search" value={filters.search} onChange={event => setFilter('search', event.target.value)} placeholder="SKU or value" /></Field>
      <Field label="Status"><select className="control" value={filters.status} onChange={event => setFilter('status', event.target.value)}><option value="">All</option><option value="ready">Ready</option><option value="needs_review">Needs review</option><option value="edited">Edited</option><option value="failed">Failed</option></select></Field>
      <Field label="Source"><select className="control" value={filters.source} onChange={event => setFilter('source', event.target.value)}><option value="">All</option><option value="direct">Workbook</option><option value="input_data">Parsed input</option><option value="vision">Vision</option><option value="default">Default</option><option value="manual">Manual</option></select></Field>
      <Field label="Confidence at least"><input className="control" type="number" min="0" max="1" step="0.05" value={filters.minConfidence} onChange={event => setFilter('min_confidence', event.target.value)} placeholder="0.75" /></Field>
    </div>
    {Object.keys(selection).length > 0 && <div className="card flex flex-wrap items-end gap-3"><p className="self-center text-sm font-semibold">{Object.keys(selection).length} selected</p><Field label="Field"><select className="control" value={bulkField} onChange={event => setBulkField(event.target.value)}>{fieldNames.map(field => <option key={field}>{field}</option>)}</select></Field><Field label="New value"><input className="control" value={bulkValue} onChange={event => setBulkValue(event.target.value)} /></Field><button className="btn-primary" disabled={!bulkField || bulk.isPending} onClick={() => bulk.mutate()}>Apply bulk edit</button></div>}
    <div className="card overflow-hidden p-0">{rows.isPending && runId ? <p className="p-5">Loading…</p> : rows.isError ? <div className="p-5"><Notice tone="error">{rows.error.message}</Notice></div> : !rows.data?.items.length ? <div className="p-5"><Empty>{runId ? 'No rows match these filters.' : 'Choose a run to review.'}</Empty></div> : <div className="overflow-auto"><table className="w-full border-collapse text-left text-sm"><thead className="bg-slate-100">{table.getHeaderGroups().map(group => <tr key={group.id}>{group.headers.map((cell, index) => <th key={cell.id} className={`whitespace-nowrap border-b border-slate-200 px-3 py-3 ${index < 3 ? 'sticky z-10 bg-slate-100' : ''}`} style={index === 0 ? { left: 0 } : index === 1 ? { left: 42 } : index === 2 ? { left: 170 } : undefined}>{flexRender(cell.column.columnDef.header, cell.getContext())}</th>)}</tr>)}</thead><tbody>{table.getRowModel().rows.map(row => <tr key={row.id} className="border-b border-slate-100 hover:bg-slate-50">{row.getVisibleCells().map((cell, index) => <td key={cell.id} className={`whitespace-nowrap px-3 py-2 ${index < 3 ? 'sticky z-[5] bg-white' : ''}`} style={index === 0 ? { left: 0 } : index === 1 ? { left: 42 } : index === 2 ? { left: 170 } : undefined}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody></table></div>}
      {rows.data && rows.data.total > 0 && <footer className="flex items-center justify-between border-t border-slate-200 p-3 text-sm"><span>{(pageNumber - 1) * pageSize + 1}–{Math.min(pageNumber * pageSize, rows.data.total)} of {rows.data.total}</span><div className="flex gap-2"><button className="btn-secondary" disabled={pageNumber <= 1} onClick={() => goToPage(pageNumber - 1)}>Previous</button><button className="btn-secondary" disabled={pageNumber * pageSize >= rows.data.total} onClick={() => goToPage(pageNumber + 1)}>Next</button></div></footer>}
    </div>
    {inspected && <aside className="card" aria-label="Field provenance"><div className="flex justify-between gap-3"><div><h2 className="font-bold">{inspected.field} provenance</h2><p className="text-sm text-slate-500">SKU {inspected.row.sku}</p></div><button className="btn-secondary" onClick={() => setInspected(undefined)}>Close</button></div>{(() => { const detail = inspected.row.provenance?.[inspected.field]; return detail ? <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3"><div><dt className="font-semibold">Source</dt><dd>{detail.source || '—'}</dd></div><div><dt className="font-semibold">Confidence</dt><dd>{detail.confidence == null ? '—' : `${Math.round(detail.confidence * 100)}%`}</dd></div><div><dt className="font-semibold">Model</dt><dd>{detail.model || '—'}</dd></div><div><dt className="font-semibold">Prompt version</dt><dd>{detail.prompt_version || '—'}</dd></div><div><dt className="font-semibold">Images</dt><dd>{detail.images?.join(', ') || '—'}</dd></div><div><dt className="font-semibold">Provider cost</dt><dd>{detail.cost == null ? '—' : `$${detail.cost.toFixed(4)}`}</dd></div><div className="sm:col-span-3"><dt className="font-semibold">Usage</dt><dd className="mt-1 break-all font-mono text-xs">{detail.usage == null ? '—' : JSON.stringify(detail.usage)}</dd></div></dl> : <p className="mt-4 text-sm text-slate-500">No provenance was recorded for this field.</p> })()}</aside>}
  </Page>
}
