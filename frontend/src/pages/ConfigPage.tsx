import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FormEvent, useState } from 'react'
import { api, apiUrl, asPage, json } from '../api'
import { Empty, Field, Markdown, Notice, Page, StatusBadge } from '../components'

type Kind = 'headers' | 'prompts' | 'attribute-sets' | 'value-lists' | 'mapping-profiles'
type Input = { key: string; label: string; type?: 'text' | 'textarea' | 'markdown' | 'json' | 'checkbox'; hint?: string; required?: boolean; defaultValue?: string }
type Definition = { title: string; description: string; fields: Input[]; importable?: boolean }
type Item = { id: string; name?: string; label?: string; key?: string; status?: string; version?: number; published_at?: string; [key: string]: unknown }

export const configDefinitions: Record<Kind, Definition> = {
  headers: { title: 'Templates', description: 'Define canonical workbook headers, aliases, and generated columns.', fields: [
    { key: 'key', label: 'Canonical key', required: true }, { key: 'label', label: 'Display label', required: true },
    { key: 'aliases', label: 'Aliases', type: 'textarea', hint: 'One accepted alias per line.' }, { key: 'required', label: 'Required input', type: 'checkbox' }, { key: 'generated', label: 'Generated output', type: 'checkbox' },
  ] },
  prompts: { title: 'Prompts', description: 'Version the enrichment instructions and expected response schema.', fields: [
    { key: 'name', label: 'Name', required: true }, { key: 'text', label: 'Prompt', type: 'markdown', hint: 'Markdown is sanitized in preview.', required: true },
    { key: 'response_schema', label: 'Response schema', type: 'json', defaultValue: '{}', hint: 'JSON object expected from the provider.' },
  ] },
  'attribute-sets': { title: 'Attributes', description: 'Assign required attributes to deterministic product groups.', fields: [
    { key: 'name', label: 'Name', required: true }, { key: 'assignment_rules', label: 'Assignment rules', type: 'json', defaultValue: '[]', hint: 'JSON array of {attribute_set, when} rules.' },
    { key: 'attributes', label: 'Attributes', type: 'json', defaultValue: '[]', hint: 'JSON array of attribute definitions.' },
  ] },
  'value-lists': { title: 'Value Lists', description: 'Manage canonical values and aliases used by validation and mapping.', importable: true, fields: [
    { key: 'name', label: 'Name', required: true }, { key: 'description', label: 'Description' }, { key: 'values', label: 'Values and aliases', type: 'textarea', hint: 'One canonical value per line; aliases follow a pipe.' },
  ] },
  'mapping-profiles': { title: 'Mapping Profiles', description: 'Control deterministic source priority and output mapping.', fields: [
    { key: 'name', label: 'Name', required: true }, { key: 'mapping', label: 'Mapping rules', type: 'json', defaultValue: '{}', hint: 'JSON object keyed by output field.' },
    { key: 'multiselect_delimiter', label: 'Multiselect delimiter', defaultValue: '|' }, { key: 'fuzzy_matching', label: 'Enable fuzzy matching', type: 'checkbox' },
  ] },
}

const blank = (definition: Definition) => Object.fromEntries(definition.fields.map(field => [field.key, field.defaultValue || (field.type === 'checkbox' ? 'false' : '')]))
const lines = (value: string) => value.split('\n').map(item => item.trim()).filter(Boolean)
const parseJson = (value: string, fallback: unknown) => value.trim() ? JSON.parse(value) : fallback

export function serializeConfig(kind: Kind, form: Record<string, string>, editing = false): Record<string, unknown> {
  if (kind === 'headers') return { ...(!editing && { key: form.key }), label: form.label, aliases: lines(form.aliases), required: form.required === 'true', generated: form.generated === 'true' }
  if (kind === 'prompts') return { name: form.name, text: form.text, response_schema: parseJson(form.response_schema, {}) }
  if (kind === 'attribute-sets') return { name: form.name, attributes: parseJson(form.attributes, []), assignment_rules: parseJson(form.assignment_rules, []) }
  if (kind === 'value-lists') return { name: form.name, description: form.description || null, ...(!editing && { items: lines(form.values).map(value => { const [canonical_value, ...aliases] = value.split('|').map(item => item.trim()); return { canonical_value, aliases: aliases.filter(Boolean) } }) }) }
  return { name: form.name, mapping: parseJson(form.mapping, {}), fuzzy_matching: form.fuzzy_matching === 'true', multiselect_delimiter: form.multiselect_delimiter || '|' }
}

function formValue(kind: Kind, field: Input, item: Item) {
  const value = kind === 'value-lists' && field.key === 'values' && Array.isArray(item.items)
    ? item.items.map(entry => { const value = entry as { canonical_value: string; aliases?: string[] }; return [value.canonical_value, ...(value.aliases || [])].join('|') }).join('\n')
    : item[field.key]
  if (field.type === 'checkbox') return String(Boolean(value))
  if (field.type === 'json') return JSON.stringify(value ?? (field.defaultValue === '[]' ? [] : {}), null, 2)
  if (Array.isArray(value)) return value.join('\n')
  return String(value ?? field.defaultValue ?? '')
}

export function ConfigPage({ kind }: { kind: Kind }) {
  const definition = configDefinitions[kind]
  const queryClient = useQueryClient()
  const [form, setForm] = useState<Record<string, string>>(() => blank(definition))
  const [editing, setEditing] = useState<string>()
  const [importFile, setImportFile] = useState<File>()
  const query = useQuery({ queryKey: ['config', kind], queryFn: () => api(`/config/${kind}`).then(value => asPage(value as Item[])) })
  const save = useMutation({
    mutationFn: () => editing ? api<Item>(`/config/${kind}/${editing}`, json('PATCH', serializeConfig(kind, form, true))) : api<Item>(`/config/${kind}`, json('POST', serializeConfig(kind, form))),
    onSuccess: () => { setForm(blank(definition)); setEditing(undefined); queryClient.invalidateQueries({ queryKey: ['config', kind] }) },
  })
  const action = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'publish' | 'archive' }) => action === 'archive' ? api(`/config/${kind}/${id}`, { method: 'DELETE' }) : api(`/config/${kind}/${id}/publish`, json('POST')),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['config', kind] }),
  })
  const importValues = useMutation({
    mutationFn: () => { const body = new FormData(); body.append('file', importFile!); return api(`/config/${kind}/import`, { method: 'POST', body }) },
    onSuccess: () => { setImportFile(undefined); queryClient.invalidateQueries({ queryKey: ['config', kind] }) },
  })
  const submit = (event: FormEvent) => { event.preventDefault(); save.mutate() }
  const beginEdit = (item: Item) => {
    setEditing(item.id)
    setForm(Object.fromEntries(definition.fields.map(field => [field.key, formValue(kind, field, item)])))
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return <Page title={definition.title} description={definition.description}>
    {(save.error || action.error || importValues.error) && <Notice tone="error">{String((save.error || action.error || importValues.error)?.message)}</Notice>}
    <form className="card space-y-4" onSubmit={submit}>
      <h2 className="font-bold">{editing ? 'Edit draft' : `New ${definition.title.replace(/s$/, '')}`}</h2>
      <div className="grid gap-4 md:grid-cols-2">{definition.fields.map(field => <div key={field.key} className={field.type === 'markdown' ? 'md:col-span-2' : ''}><Field label={field.label} hint={field.hint}>
        {field.type === 'checkbox' ? <input type="checkbox" checked={form[field.key] === 'true'} onChange={event => setForm({ ...form, [field.key]: String(event.target.checked) })} /> : field.type === 'textarea' || field.type === 'markdown' || field.type === 'json' ? <textarea className="control min-h-28 font-mono" disabled={Boolean(editing && kind === 'value-lists' && field.key === 'values')} required={field.required} value={form[field.key]} onChange={event => setForm({ ...form, [field.key]: event.target.value })} /> : <input className="control" disabled={Boolean(editing && kind === 'headers' && field.key === 'key')} required={field.required} value={form[field.key]} onChange={event => setForm({ ...form, [field.key]: event.target.value })} />}
      </Field>{field.type === 'markdown' && form[field.key] && <div className="mt-3"><Markdown value={form[field.key]} /></div>}</div>)}</div>
      <div className="flex gap-2"><button className="btn-primary" disabled={save.isPending}>{save.isPending ? 'Saving…' : editing ? 'Save draft' : 'Create draft'}</button>{editing && <button className="btn-secondary" type="button" onClick={() => { setEditing(undefined); setForm(blank(definition)) }}>Cancel</button>}</div>
    </form>
    {definition.importable && <form className="card flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); importValues.mutate() }}><Field label="Import values"><input className="control" type="file" accept=".csv,text/csv" required onChange={event => setImportFile(event.target.files?.[0])} /></Field><button className="btn-secondary" disabled={!importFile || importValues.isPending}>Import CSV</button></form>}
    <div className="card"><h2 className="mb-4 font-bold">Saved configurations</h2>{query.isPending ? <p>Loading…</p> : query.isError ? <Notice tone="error">{query.error.message}</Notice> : !query.data.items.length ? <Empty>No configurations yet.</Empty> : <div className="divide-y divide-slate-200">{query.data.items.map(item => <article key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-4 first:pt-0 last:pb-0"><div><div className="flex items-center gap-2"><h3 className="font-semibold">{item.name || item.label || item.key}</h3><StatusBadge value={item.status || 'draft'} /></div><p className="mt-1 text-xs text-slate-500">Version {item.version || 1}{item.published_at ? ` · Published ${new Date(item.published_at).toLocaleString()}` : ''}</p></div><div className="flex flex-wrap gap-2">{kind === 'value-lists' && <a className="btn-secondary" href={apiUrl(`/config/value-lists/${item.id}/export`)}>Export CSV</a>}<button className="btn-secondary" onClick={() => beginEdit(item)}>Edit</button><button className="btn-secondary" disabled={action.isPending || item.status === 'published'} onClick={() => action.mutate({ id: item.id, action: 'publish' })}>Publish</button><button className="btn-danger" disabled={action.isPending} onClick={() => action.mutate({ id: item.id, action: 'archive' })}>Archive</button></div></article>)}</div>}</div>
  </Page>
}
