import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useState } from 'react'
import { api, asPage, json } from '../api'
import { Empty, Field, Notice, Page, StatusBadge } from '../components'

type Profile = { id: string; name: string; adapter: string; model_name: string; endpoint_url?: string; has_api_key?: boolean; status?: string }
type TestJob = { id: string; status: string; message?: string }

export function LlmProfiles() {
  const client = useQueryClient()
  const [form, setForm] = useState({ name: '', adapter: 'litellm', model_name: '', endpoint_url: '', api_key: '' })
  const [testJobId, setTestJobId] = useState<string>()
  const profiles = useQuery({ queryKey: ['llm-profiles'], queryFn: () => api('/config/llm-profiles').then(value => asPage(value as Profile[])) })
  const create = useMutation({ mutationFn: () => api('/config/llm-profiles', json('POST', { ...form, endpoint_url: form.endpoint_url || null })), onSuccess: () => { setForm({ name: '', adapter: 'litellm', model_name: '', endpoint_url: '', api_key: '' }); client.invalidateQueries({ queryKey: ['llm-profiles'] }) } })
  const test = useMutation({ mutationFn: (id: string) => api<TestJob>(`/config/llm-profiles/${id}/test`, json('POST')), onSuccess: job => setTestJobId(job.id) })
  const action = useMutation({ mutationFn: ({ id, name }: { id: string; name: 'publish' | 'archive' }) => name === 'archive' ? api(`/config/llm-profiles/${id}`, { method: 'DELETE' }) : api(`/config/llm-profiles/${id}/publish`, json('POST')), onSuccess: () => client.invalidateQueries({ queryKey: ['llm-profiles'] }) })
  const testJob = useQuery({ queryKey: ['llm-test', testJobId], queryFn: () => api<TestJob>(`/jobs/${testJobId}`), enabled: Boolean(testJobId), refetchInterval: query => ['pending', 'queued', 'received', 'started', 'running'].includes(query.state.data?.status || '') ? 1500 : false })
  const submit = (event: FormEvent) => { event.preventDefault(); create.mutate() }
  return <Page title="LLM Profiles" description="Provider keys are write-only. Saved profiles expose only a masked fingerprint.">
    {(create.isError || test.isError || action.isError) && <Notice tone="error">{(create.error || test.error || action.error)?.message}</Notice>}
    {action.isSuccess && <Notice tone="success">Profile updated.</Notice>}
    {testJob.data && <Notice tone={['failed', 'failure'].includes(testJob.data.status) ? 'error' : ['succeeded', 'success'].includes(testJob.data.status) ? 'success' : 'info'}>Connection test: {testJob.data.message || testJob.data.status}</Notice>}
    <form className="card space-y-4" onSubmit={submit}>
      <h2 className="font-bold">New profile</h2><div className="grid gap-4 md:grid-cols-2">
        <Field label="Name"><input className="control" required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></Field>
        <Field label="Adapter"><select className="control" value={form.adapter} onChange={e => setForm({ ...form, adapter: e.target.value })}><option value="litellm">LiteLLM</option><option value="openai-compatible">OpenAI-compatible HTTP</option><option value="mock">Mock (local demo)</option></select></Field>
        <Field label="Model"><input className="control" required placeholder="e.g. gpt-4.1-mini" value={form.model_name} onChange={e => setForm({ ...form, model_name: e.target.value })} /></Field>
        <Field label="Base URL" hint="Required only for compatible HTTP profiles."><input className="control" type="url" placeholder="https://provider.example/v1" value={form.endpoint_url} onChange={e => setForm({ ...form, endpoint_url: e.target.value })} /></Field>
        <Field label="API key" hint="Never returned after saving."><input className="control" type="password" autoComplete="new-password" required={form.adapter !== 'mock'} value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })} /></Field>
      </div><button className="btn-primary" disabled={create.isPending}>{create.isPending ? 'Saving…' : 'Save profile'}</button>
    </form>
    <div className="card"><h2 className="mb-4 font-bold">Profiles</h2>{profiles.isPending ? <p>Loading…</p> : profiles.isError ? <Notice tone="error">{profiles.error.message}</Notice> : !profiles.data.items.length ? <Empty>No LLM profiles yet.</Empty> : <div className="divide-y divide-slate-200">{profiles.data.items.map(profile => <article key={profile.id} className="flex flex-wrap items-center justify-between gap-3 py-4"><div><div className="flex gap-2"><h3 className="font-semibold">{profile.name}</h3><StatusBadge value={profile.status || 'saved'} /></div><p className="text-sm text-slate-600">{profile.adapter} · {profile.model_name}</p><p className="font-mono text-xs text-slate-500">Key {profile.has_api_key ? '•••••••• (stored)' : 'not set'}</p></div><div className="flex flex-wrap gap-2"><button className="btn-secondary" disabled={test.isPending} onClick={() => test.mutate(profile.id)}>Test connection</button><button className="btn-secondary" disabled={action.isPending} onClick={() => action.mutate({ id: profile.id, name: 'publish' })}>Publish</button><button className="btn-danger" disabled={action.isPending} onClick={() => action.mutate({ id: profile.id, name: 'archive' })}>Archive</button></div></article>)}</div>}</div>
  </Page>
}
