import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchImageObjectUrl } from '../api'
import { RISK_COLOR, RISK_LABEL } from '../theme'
import type { CatalogEvent, EventsResponse } from '../types'

const thumbCache = new Map<string, string>()

/** Görünür olunca yüklenen küçük resim (görüntü uç noktası API anahtarı istediği için fetch + blob URL). */
function Thumb({ imageId }: { imageId: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [src, setSrc] = useState<string | null>(thumbCache.get(imageId) ?? null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (src || failed || !ref.current) return
    const el = ref.current
    let cancelled = false
    const obs = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return
      obs.disconnect()
      fetchImageObjectUrl(imageId)
        .then((u) => {
          thumbCache.set(imageId, u)
          if (!cancelled) setSrc(u)
        })
        .catch(() => !cancelled && setFailed(true))
    })
    obs.observe(el)
    return () => {
      cancelled = true
      obs.disconnect()
    }
  }, [imageId, src, failed])

  return (
    <div ref={ref} className="thumb" aria-hidden>
      {src ? <img src={src} alt="" /> : <span>{failed ? '×' : ''}</span>}
    </div>
  )
}

interface Props {
  data: EventsResponse | null
  error: string | null
  selectedId: string | null
  runningId: string | null
  busy: boolean
  onSelect: (id: string) => void
  onRun: (opts: { imageId: string; imageB64?: string }) => void
}

const MAX_IMAGE_BYTES = 10 * 1024 * 1024

export default function EventPicker({ data, error, selectedId, runningId, busy, onSelect, onRun }: Props) {
  const [zone, setZone] = useState<string>('all')
  const [uploadMsg, setUploadMsg] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const events = data?.events ?? []
  const zones = useMemo(() => {
    const counts = new Map<string, { name: string; n: number }>()
    for (const e of events) {
      const key = e.zone_id ?? '—'
      const cur = counts.get(key) ?? { name: e.zone_name ?? 'Bölge yok', n: 0 }
      cur.n += 1
      counts.set(key, cur)
    }
    return [...counts.entries()].map(([id, v]) => ({ id, ...v }))
  }, [events])
  const visible = zone === 'all' ? events : events.filter((e) => (e.zone_id ?? '—') === zone)
  const selected = events.find((e) => e.image_id === selectedId) ?? null

  const onFile = (file: File | undefined) => {
    setUploadMsg(null)
    if (!file) return
    const stem = file.name.replace(/\.[^.]+$/, '')
    if (!events.some((e) => e.image_id === stem)) {
      setUploadMsg(`“${file.name}” hiçbir olayla eşleşmiyor: dosya adı bir image_id olmalı (örn. ${events[0]?.image_id ?? 'img_000860'}.jpg), çünkü köşe koordinatları/çekim zamanı image_meta.json'dan gelir.`)
      return
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setUploadMsg('Görüntü 10 MB’tan büyük')
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      onSelect(stem)
      onRun({ imageId: stem, imageB64: String(reader.result) })
    }
    reader.onerror = () => setUploadMsg('Dosya okunamadı')
    reader.readAsDataURL(file)
  }

  if (error && !data) return <div className="empty">Olay kataloğu alınamadı: {error}</div>
  if (!data) return <div className="empty">Olaylar yükleniyor…</div>
  if (events.length === 0) {
    return (
      <div className="empty">
        Veri setinde görüntü yok.
        {data.errors.length > 0 && <div className="note warn">{data.errors.join(' · ')}</div>}
        <div className="muted">DATA_DIR altında image_meta.json ve tracks.csv bulunmalı.</div>
      </div>
    )
  }

  return (
    <div className="evpicker">
      <div className="chips zonechips">
        <button className={`chip chip-btn ${zone === 'all' ? 'active' : ''}`} onClick={() => setZone('all')}>
          Tümü ({events.length})
        </button>
        {zones.map((z) => (
          <button key={z.id} className={`chip chip-btn ${zone === z.id ? 'active' : ''}`} onClick={() => setZone(z.id)}>
            {z.name} ({z.n})
          </button>
        ))}
      </div>

      <ul className="evlist" role="listbox" aria-label="Olaylar">
        {visible.map((e: CatalogEvent) => {
          const risk = e.last_run?.risk_level ?? null
          const active = e.image_id === selectedId
          const running = e.image_id === runningId
          return (
            <li key={e.image_id} role="option" aria-selected={active}>
              <button className={`evrow ${active ? 'active' : ''}`} onClick={() => onSelect(e.image_id)} onDoubleClick={() => !busy && onRun({ imageId: e.image_id })}>
                <Thumb imageId={e.image_id} />
                <span className="ev-time">{e.capture_time}</span>
                <span className="ev-id">{e.image_id}</span>
                <span className="ev-zone">{e.zone_name ?? '—'}</span>
                <span className="ev-res">
                  {running ? (
                    <span className="chip chip-run">çalışıyor…</span>
                  ) : risk ? (
                    <span className="chip" style={{ color: RISK_COLOR[risk], borderColor: RISK_COLOR[risk] }}>{RISK_LABEL[risk]}</span>
                  ) : e.last_run?.status === 'failed' ? (
                    <span className="chip chip-warn">hata</span>
                  ) : null}
                </span>
              </button>
            </li>
          )
        })}
      </ul>

      <div className="evactions">
        <button className="btn primary" disabled={busy || !selected} onClick={() => selected && onRun({ imageId: selected.image_id })}>
          {busy && runningId ? '⏳ Değerlendiriliyor…' : selected ? `▶ ${selected.capture_time} olayını değerlendir` : '▶ Olay seçin'}
        </button>
        <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = '' }} />
        <button className="btn ghost" disabled={busy} onClick={() => fileRef.current?.click()} title="Dosya adı bir image_id ile eşleşmeli (ör. img_000860.jpg)">
          🖼 Görüntü yükle
        </button>
      </div>
      {uploadMsg && <div className="note warn">{uploadMsg}</div>}
      <div className="muted small-note">Çift tıklama = hemen değerlendir. Yüklenen dosyanın adı bir image_id olmalı; zaman ve konum image_meta.json’dan gelir.</div>
    </div>
  )
}
