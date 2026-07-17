import { useMutation, useQuery } from '@tanstack/react-query'
import { FormEvent, useState } from 'react'
import { api, apiUrl } from '../api'
import { Field, Notice, Page, Progress, StatusBadge } from '../components'

const workbookExtensions = ['.xlsx', '.xlsm']
export function validateWorkbook(file?: Pick<File, 'name' | 'size'>) {
  if (!file) return 'Choose a workbook.'
  if (!workbookExtensions.some(ext => file.name.toLowerCase().endsWith(ext))) return 'Use an XLSX or XLSM file.'
  if (file.size > 25 * 1024 * 1024) return 'Workbook must be 25 MB or smaller.'
  return ''
}

type ImageJob = { id: string; status: string; progress?: number; total_rows?: number; completed_rows?: number; failed_rows?: number; message?: string }

export function ImageDownloader() {
  const [file, setFile] = useState<File>()
  const [jobId, setJobId] = useState<string>()
  const upload = useMutation({ mutationFn: async () => { const error = validateWorkbook(file); if (error) throw new Error(error); const body = new FormData(); body.append('file', file!); return api<ImageJob>('/image-downloads', { method: 'POST', body }) }, onSuccess: job => setJobId(job.id) })
  const job = useQuery({ queryKey: ['image-job', jobId], queryFn: () => api<ImageJob>(`/image-downloads/${jobId}`), enabled: Boolean(jobId), refetchInterval: query => ['queued', 'running'].includes(query.state.data?.status || '') ? 2000 : false })
  const submit = (event: FormEvent) => { event.preventDefault(); upload.mutate() }
  const data = job.data
  return <Page title="Image Downloader" description="Upload a workbook once to fetch, normalize, and package all image columns.">
    {upload.isError && <Notice tone="error">{upload.error.message}</Notice>}
    <form className="card space-y-4" onSubmit={submit}><Field label="Catalog workbook" hint="XLSX or XLSM · up to 25 MB"><input className="control" type="file" accept=".xlsx,.xlsm" required onChange={e => setFile(e.target.files?.[0])} /></Field><button className="btn-primary" disabled={upload.isPending}>{upload.isPending ? 'Uploading…' : 'Normalize images'}</button></form>
    {jobId && <div className="card space-y-4" aria-live="polite"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-bold">Image job</h2><StatusBadge value={data?.status || 'queued'} /></div><Progress value={data?.progress || 0} label={`${data?.completed_rows || 0} of ${data?.total_rows || 0} rows`} />{data?.message && <p className="text-sm text-slate-600">{data.message}</p>}{data?.status === 'completed' && <div className="flex flex-wrap gap-2"><a className="btn-primary" href={apiUrl(`/image-downloads/${jobId}/images.zip`)}>Image ZIP</a><a className="btn-secondary" href={apiUrl(`/image-downloads/${jobId}/report.csv`)}>CSV report</a><a className="btn-secondary" href={apiUrl(`/image-downloads/${jobId}/report.xlsx`)}>XLSX report</a>{Boolean(data.failed_rows) && <span className="self-center text-sm text-amber-800">{data.failed_rows} rows failed</span>}</div>}</div>}
  </Page>
}
