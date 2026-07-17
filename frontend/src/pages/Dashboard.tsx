import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Notice, Page, StatusBadge } from '../components'

type Health = { status?: string; checks?: { database?: string; redis?: string; storage?: string } }

export function Dashboard() {
  const health = useQuery({ queryKey: ['health'], queryFn: () => api<Health>('/health'), refetchInterval: 30_000 })
  return <Page title="Dashboard" description="Configure, enrich, review, and export a catalog from one workspace.">
    {health.isError && <Notice tone="error">The API is unavailable. Check that the backend is running.</Notice>}
    <div className="grid gap-4 md:grid-cols-3">
      <div className="card"><p className="text-sm font-semibold text-slate-500">System</p><div className="mt-3"><StatusBadge value={health.data?.status || (health.isPending ? 'checking' : 'offline')} /></div></div>
      {(['database', 'redis', 'storage'] as const).map(key => <div className="card" key={key}><p className="capitalize text-sm font-semibold text-slate-500">{key}</p><p className="mt-3 text-lg font-bold">{health.data?.checks?.[key] || '—'}</p></div>)}
    </div>
    <div className="card"><h2 className="text-lg font-bold">Start a workflow</h2><div className="mt-4 flex flex-wrap gap-3"><Link className="btn-primary" to="/runs">Upload catalog</Link><Link className="btn-secondary" to="/image-downloader">Normalize images</Link><Link className="btn-secondary" to="/review">Review results</Link></div></div>
  </Page>
}
