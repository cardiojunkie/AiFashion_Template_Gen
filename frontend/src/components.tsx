import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'

export function Page({ title, description, actions, children }: { title: string; description?: string; actions?: ReactNode; children: ReactNode }) {
  return <section className="mx-auto max-w-[1600px] space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><h1 className="text-2xl font-bold tracking-tight">{title}</h1>{description && <p className="mt-1 max-w-3xl text-sm text-slate-600">{description}</p>}</div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
    {children}
  </section>
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="block"><span className="label">{label}</span>{children}{hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}</label>
}

export function Notice({ children, tone = 'info' }: { children: ReactNode; tone?: 'info' | 'error' | 'success' }) {
  const style = tone === 'error' ? 'border-red-200 bg-red-50 text-red-800' : tone === 'success' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-blue-200 bg-blue-50 text-blue-800'
  return <div className={`rounded-lg border p-3 text-sm ${style}`} role={tone === 'error' ? 'alert' : 'status'}>{children}</div>
}

export function StatusBadge({ value }: { value?: string }) {
  const label = value || 'unknown'
  const color = ['complete', 'completed', 'published', 'ready', 'healthy', 'succeeded'].includes(label.toLowerCase())
    ? 'bg-emerald-100 text-emerald-800'
    : ['failed', 'error', 'blocked', 'cancelled'].includes(label.toLowerCase())
      ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-900'
  return <span className={`inline-flex rounded-full px-2 py-1 text-xs font-semibold ${color}`}>{label}</span>
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">{children}</div>
}

export function Progress({ value, label }: { value: number; label?: string }) {
  const safe = Math.max(0, Math.min(100, value))
  return <div className="min-w-40"><div className="mb-1 flex justify-between text-xs text-slate-600"><span>{label || 'Progress'}</span><span>{Math.round(safe)}%</span></div><progress className="h-2 w-full accent-moss" max="100" value={safe}>{safe}%</progress></div>
}

export function Markdown({ value }: { value: string }) {
  return <div className="prose prose-sm max-w-none rounded-lg border border-slate-200 bg-slate-50 p-4"><ReactMarkdown rehypePlugins={[rehypeSanitize]}>{value}</ReactMarkdown></div>
}
