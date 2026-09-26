import type { DashboardState, EventsResponse, PipelineRequest } from './types'

// UI yalnızca gateway'e bağlanır (tek origin). Geliştirmede Vite /api'yi proxy'ler.
const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api'
const KEY_STORAGE = 'uskoruma_api_key'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export function getApiKey(): string {
  try {
    return localStorage.getItem(KEY_STORAGE) || (import.meta.env.VITE_API_KEY as string | undefined) || ''
  } catch {
    return (import.meta.env.VITE_API_KEY as string | undefined) || ''
  }
}

export function setApiKey(key: string): void {
  try {
    localStorage.setItem(KEY_STORAGE, key)
  } catch {
    /* localStorage kapalı olabilir */
  }
}

function authHeaders(): Record<string, string> {
  const key = getApiKey()
  return key ? { 'X-API-Key': key } : {}
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(init.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      /* gövde JSON değil */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

export const fetchState = (): Promise<DashboardState> => request<DashboardState>('/dashboard/state')

/** Veri setindeki tüm olaylar (görüntüler) + bölgeler + bu oturumdaki son değerlendirmeler. */
export const fetchEvents = (): Promise<EventsResponse> => request<EventsResponse>('/events')

/** wait=false: 202 döner, ilerleme dashboard/state üzerinden poll edilir. */
export const startPipeline = (body: PipelineRequest): Promise<{ run_id: string; status: string }> =>
  request('/pipeline/run?wait=false', { method: 'POST', body: JSON.stringify(body) })

/** <img> başlık gönderemediği için görüntüyü fetch ile alıp blob URL'ye çevirir. */
export async function fetchImageObjectUrl(imageId: string): Promise<string> {
  const res = await fetch(`${API_BASE}/images/${encodeURIComponent(imageId)}`, { headers: authHeaders() })
  if (!res.ok) throw new ApiError(res.status, 'Görüntü alınamadı')
  return URL.createObjectURL(await res.blob())
}
