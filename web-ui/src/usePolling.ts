import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from './api'

const DEFAULT_MS = Number(import.meta.env.VITE_POLL_MS) || 2000

export interface PollResult<T> {
  data: T | null
  error: string | null
  unauthorized: boolean
  lastOk: number | null
  refresh: () => void
}

/**
 * Zincirleme setTimeout ile polling: istekler asla üst üste binmez, sekme gizliyken duraklar.
 * Hata olursa son geçerli veri korunur (UI boşalmaz), error alanı bağlantı sorununu bildirir.
 * Her effect çalışması kendi `cancelled` bayrağına sahiptir; StrictMode'un çift mount'unda eski zincir kendiliğinden sonlanır.
 */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number = DEFAULT_MS): PollResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [unauthorized, setUnauthorized] = useState(false)
  const [lastOk, setLastOk] = useState<number | null>(null)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const trigger = useRef<() => void>(() => undefined)

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    const loop = async () => {
      if (cancelled) return
      if (!document.hidden) {
        try {
          const next = await fetcherRef.current()
          if (cancelled) return
          setData(next)
          setError(null)
          setUnauthorized(false)
          setLastOk(Date.now())
        } catch (e) {
          if (cancelled) return
          if (e instanceof ApiError && e.status === 401) setUnauthorized(true)
          setError(e instanceof Error ? e.message : String(e))
        }
      }
      if (!cancelled) timer = window.setTimeout(loop, intervalMs)
    }

    trigger.current = () => {
      window.clearTimeout(timer)
      void loop()
    }
    void loop()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [intervalMs])

  const refresh = useCallback(() => trigger.current(), [])
  return { data, error, unauthorized, lastOk, refresh }
}
