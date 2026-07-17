import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useEffect, useState } from 'react'
import { api, json } from '../api'
import { Field, Notice, Page } from '../components'

type Settings = { retention_days: number; max_image_bytes: number; max_image_pixels: number; multiselect_delimiter: string; fuzzy_matching: boolean; private_llm_hosts: string[] }
const defaults: Settings = { retention_days: 30, max_image_bytes: 20_000_000, max_image_pixels: 40_000_000, multiselect_delimiter: '|', fuzzy_matching: false, private_llm_hosts: [] }

export function SettingsPage() {
  const client = useQueryClient()
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => api<Settings>('/settings') })
  const [form, setForm] = useState(defaults)
  const [hosts, setHosts] = useState('')
  useEffect(() => { if (settings.data) { setForm(settings.data); setHosts(settings.data.private_llm_hosts.join('\n')) } }, [settings.data])
  const save = useMutation({ mutationFn: () => api<Settings>('/settings', json('PATCH', { ...form, private_llm_hosts: hosts.split('\n').map(value => value.trim()).filter(Boolean) })), onSuccess: data => { client.setQueryData(['settings'], data) } })
  const submit = (event: FormEvent) => { event.preventDefault(); save.mutate() }
  return <Page title="Settings" description="System-wide safety and mapping defaults. Changes affect new jobs only.">
    {settings.isError && <Notice tone="error">{settings.error.message}</Notice>}{save.isSuccess && <Notice tone="success">Settings saved.</Notice>}{save.isError && <Notice tone="error">{save.error.message}</Notice>}
    <form className="card space-y-4" onSubmit={submit}><div className="grid gap-4 md:grid-cols-2">
      <Field label="Soft-delete retention (days)"><input className="control" type="number" min="1" max="365" value={form.retention_days} onChange={event => setForm({ ...form, retention_days: Number(event.target.value) })} /></Field>
      <Field label="Multiselect delimiter"><input className="control" maxLength={5} value={form.multiselect_delimiter} onChange={event => setForm({ ...form, multiselect_delimiter: event.target.value })} /></Field>
      <Field label="Maximum image bytes"><input className="control" type="number" min="1000000" value={form.max_image_bytes} onChange={event => setForm({ ...form, max_image_bytes: Number(event.target.value) })} /></Field>
      <Field label="Maximum image pixels"><input className="control" type="number" min="1000000" value={form.max_image_pixels} onChange={event => setForm({ ...form, max_image_pixels: Number(event.target.value) })} /></Field>
      <Field label="Allowed private LLM hosts" hint="One exact hostname per line. Leave blank to deny private endpoints."><textarea className="control min-h-28" value={hosts} onChange={event => setHosts(event.target.value)} /></Field>
      <label className="flex items-center gap-3 self-center text-sm font-semibold"><input type="checkbox" checked={form.fuzzy_matching} onChange={event => setForm({ ...form, fuzzy_matching: event.target.checked })} /> Enable fuzzy value-list matching</label>
    </div><button className="btn-primary" disabled={settings.isPending || save.isPending}>{save.isPending ? 'Saving…' : 'Save settings'}</button></form>
  </Page>
}
