export const API_BASE = import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE || '/api/v1'

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message)
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers })
  const isJson = response.headers.get('content-type')?.includes('application/json')
  const payload: unknown = response.status === 204 ? undefined : isJson ? await response.json() : await response.text()
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload
      ? payload.detail
      : payload && typeof payload === 'object' && 'error' in payload && payload.error && typeof payload.error === 'object' && 'message' in payload.error
        ? payload.error.message : payload
    const message = typeof detail === 'string' ? detail : `Request failed (${response.status})`
    throw new ApiError(response.status, message, detail)
  }
  return payload as T
}

export const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
})

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }

export function asPage<T>(value: Page<T> | T[]): Page<T> {
  return Array.isArray(value) ? { items: value, total: value.length, page: 1, page_size: value.length } : value
}

export function apiUrl(path: string) { return `${API_BASE}${path}` }
