import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useState } from 'react'
import { api, apiUrl, asPage, json } from '../api'
import { Empty, Field, Notice, Page, Progress, StatusBadge } from '../components'

type ExportJob = { id: string; run_id: string; format: string; status: string; progress?: number; filename?: string; download_url?: string; created_at?: string }

export function ExportsPage() {
  const client = useQueryClient()
  const [form, setForm] = useState({ run_id: '', format: 'xlsx', include_images: false, override_blocking: false })
  const jobs = useQuery({ queryKey: ['exports'], queryFn: () => api('/exports?page_size=50').then(value => asPage(value as ExportJob[])), refetchInterval: 4000 })
  const create = useMutation({ mutationFn: () => api(`/runs/${encodeURIComponent(form.run_id)}/exports`, json('POST', { format: form.format, include_images: form.include_images, override_blocking: form.override_blocking, actor: form.override_blocking ? 'internal' : null })), onSuccess: () => { setForm({ ...form, override_blocking: false }); client.invalidateQueries({ queryKey: ['exports'] }) } })
  const submit = (event: FormEvent) => { event.preventDefault(); create.mutate() }
  return <Page title="Exports" description="Build spreadsheet-safe catalog files and optional image bundles in the background.">
    {create.isError && <Notice tone="error">{create.error.message}</Notice>}
    <form className="card space-y-4" onSubmit={submit}><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"><Field label="Run ID"><input className="control" required value={form.run_id} onChange={event => setForm({ ...form, run_id: event.target.value })} /></Field><Field label="Format"><select className="control" value={form.format} onChange={event => setForm({ ...form, format: event.target.value })}><option value="xlsx">XLSX</option><option value="csv">CSV</option><option value="image_zip">Images only (ZIP)</option></select></Field><div className="space-y-3 pt-6">{form.format !== 'image_zip' && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.include_images} onChange={event => setForm({ ...form, include_images: event.target.checked })} /> Include normalized images</label>}<label className="flex items-start gap-2 text-sm text-amber-900"><input className="mt-1" type="checkbox" checked={form.override_blocking} onChange={event => setForm({ ...form, override_blocking: event.target.checked })} /> Confirm export even if blocking validation errors remain</label></div></div><button className="btn-primary" disabled={create.isPending}>{create.isPending ? 'Queuing…' : 'Create export'}</button></form>
    <div className="card"><h2 className="mb-4 font-bold">Export jobs</h2>{jobs.isPending ? <p>Loading…</p> : jobs.isError ? <Notice tone="error">{jobs.error.message}</Notice> : !jobs.data.items.length ? <Empty>No exports yet.</Empty> : <div className="divide-y divide-slate-200">{jobs.data.items.map(job => <article className="grid gap-3 py-4 sm:grid-cols-[1fr_12rem_auto] sm:items-center" key={job.id}><div><div className="flex gap-2"><h3 className="font-semibold">{job.filename || `${job.format.toUpperCase()} export`}</h3><StatusBadge value={job.status} /></div><p className="text-xs text-slate-500">Run {job.run_id}{job.created_at ? ` · ${new Date(job.created_at).toLocaleString()}` : ''}</p></div><Progress value={job.progress || 0} />{job.status === 'completed' ? <a className="btn-primary" href={job.download_url || apiUrl(`/exports/${job.id}/download`)}>Download</a> : <span />}</article>)}</div>}</div>
  </Page>
}
