import { NavLink, Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { ConfigPage } from './pages/ConfigPage'
import { Dashboard } from './pages/Dashboard'
import { ExportsPage } from './pages/ExportsPage'
import { ImageDownloader } from './pages/ImageDownloader'
import { LlmProfiles } from './pages/LlmProfiles'
import { ReviewPage } from './pages/ReviewPage'
import { RunsPage } from './pages/RunsPage'
import { SettingsPage } from './pages/SettingsPage'

const nav = [
  ['/', 'Dashboard'], ['/templates', 'Templates'], ['/prompts', 'Prompts'], ['/attributes', 'Attributes'], ['/value-lists', 'Value Lists'],
  ['/mapping-profiles', 'Mapping Profiles'], ['/llm-profiles', 'LLM Profiles'], ['/image-downloader', 'Image Downloader'],
  ['/runs', 'Runs'], ['/review', 'Review'], ['/exports', 'Exports'], ['/settings', 'Settings'],
] as const

function Shell() {
  return <div className="min-h-screen lg:grid lg:grid-cols-[250px_1fr]">
    <a href="#main" className="sr-only z-50 rounded bg-white p-3 focus:not-sr-only focus:fixed focus:left-3 focus:top-3">Skip to content</a>
    <aside className="border-b border-slate-800 bg-ink px-4 py-5 text-white lg:min-h-screen lg:border-b-0 lg:border-r">
      <div className="mb-5 px-3"><p className="text-xs font-semibold uppercase tracking-[.2em] text-emerald-300">Catalog</p><p className="mt-1 text-lg font-bold">Enrichment Studio</p></div>
      <nav aria-label="Product areas" className="flex gap-1 overflow-x-auto lg:block lg:space-y-1">
        {nav.map(([to, label]) => <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `block shrink-0 rounded-lg px-3 py-2 text-sm font-medium ${isActive ? 'bg-white text-ink' : 'text-slate-200 hover:bg-white/10 hover:text-white'}`}>{label}</NavLink>)}
      </nav>
    </aside>
    <main id="main" className="min-w-0 p-4 sm:p-6 lg:p-8"><Outlet /></main>
  </div>
}

export default function App() {
  return <Routes><Route element={<Shell />}>
    <Route index element={<Dashboard />} />
    <Route path="templates" element={<ConfigPage kind="headers" />} />
    <Route path="prompts" element={<ConfigPage kind="prompts" />} />
    <Route path="attributes" element={<ConfigPage kind="attribute-sets" />} />
    <Route path="value-lists" element={<ConfigPage kind="value-lists" />} />
    <Route path="mapping-profiles" element={<ConfigPage kind="mapping-profiles" />} />
    <Route path="llm-profiles" element={<LlmProfiles />} />
    <Route path="image-downloader" element={<ImageDownloader />} />
    <Route path="runs" element={<RunsPage />} />
    <Route path="review" element={<ReviewPage />} />
    <Route path="exports" element={<ExportsPage />} />
    <Route path="settings" element={<SettingsPage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Route></Routes>
}
