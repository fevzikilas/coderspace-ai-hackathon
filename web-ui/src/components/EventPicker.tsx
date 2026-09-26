import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchImageObjectUrl } from '../api'
import { RISK_COLOR, RISK_LABEL } from '../theme'
import { inkOn } from '../vehicleColors'
import { zoneLabel } from '../zoneLabels'
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
const byTime = (a: CatalogEvent, b: CatalogEvent): number => a.capture_iso.localeCompare(b.capture_iso) || a.image_id.localeCompare(b.image_id)

/** SOL ŞERİT: üstte bölge süzgeci (Tümü + bölgeler, pusula sırasıyla), altında olaylar capture_time'a göre KRONOLOJİK (en erken üstte). */
export default function EventPicker({ data, error, selectedId, runningId, busy, onSelect, onRun }: Props) {
  const [zone, setZone] = useState<string>('all')
  const [uploadMsg, setUploadMsg] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const events = useMemo(() => [...(data?.events ?? [])].sort(byTime), [data?.events])
  // Süzgeç düğmeleri: yalnızca olayı olan bölgeler; sıra = üsten pusula yönü (K → KD → D …), yönü bilinmeyen sonda
  const zones = useMemo(() => {
    const bearing = new Map((data?.zones ?? []).map((z) => [z.zone_id, z.bearing_from_base_deg ?? 999]))
    const counts = new Map<string, { name: string; n: number }>()
    for (const e of events) {
      const key = e.zone_id ?? '—'
      const cur = counts.get(key) ?? { name: e.zone_name ?? 'Bölge yok', n: 0 }
      cur.n += 1
      counts.set(key, cur)
    }
    return [...counts.entries()].map(([id, v]) => ({ id, ...v })).sort((a, b) => (bearing.get(a.id) ?? 999) - (bearing.get(b.id) ?? 999))
  }, [events, data?.zones])
  const visible = zone === 'all' ? events : events.filter((e) => (e.zone_id ?? '—') === zone)

  useEffect(() => {
    const box = listRef.current
    const el = box?.querySelector<HTMLElement>(`li[data-id="${selectedId}"]`)
    if (!el || !box) return
    if (el.offsetTop < box.scrollTop || el.offsetTop + el.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTo({ top: Math.max(0, el.offsetTop - box.clientHeight / 3), behavior: 'smooth' })
    }
  }, [selectedId, zone])

  const onFile = (file: File | undefined) => {
    setUploadMsg(null)
    if (!file) return
    const stem = file.name.replace(/\.[^.]+$/, '')
    if (!events.some((e) => e.image_id === stem)) {
      setUploadMsg(`“${file.name}” hiçbir olayla eşleşmiyor: dosya adı bir image_id olmalı (örn. ${events[0]?.image_id ?? 'img_000860'}.jpg).`)
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

  const header = (
    <div className="rail-head">
      <b>Olaylar</b>
      <span className="muted">{data ? `${visible.length}/${events.length}` : ''}</span>
      <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = '' }} />
      <button className="rail-upload" disabled={busy || !data} onClick={() => fileRef.current?.click()} title="Görüntü yükle — dosya adı bir image_id ile eşleşmeli (ör. img_000860.jpg); zaman ve konum image_meta.json’dan gelir">
        ⤒ Yükle
      </button>
    </div>
  )

  if (error && !data) return <div className="rail">{header}<div className="empty small">Olay kataloğu alınamadı: {error}</div></div>
  if (!data) return <div className="rail">{header}<div className="empty small">Olaylar yükleniyor…</div></div>
  if (events.length === 0) {
    return (
      <div className="rail">
        {header}
        <div className="empty small">
          Veri setinde görüntü yok.
          {data.errors.length > 0 && <div className="note warn">{data.errors.join(' · ')}</div>}
        </div>
      </div>
    )
  }

  return (
    <div className="rail">
      {header}
      <div className="zonefilter" role="radiogroup" aria-label="Bölge süzgeci">
        <button role="radio" aria-checked={zone === 'all'} className={`zf zf-all ${zone === 'all' ? 'active' : ''}`} onClick={() => setZone('all')}>
          Tümü <small>{events.length}</small>
        </button>
        {zones.map((z) => (
          <button key={z.id} role="radio" aria-checked={zone === z.id} className={`zf ${zone === z.id ? 'active' : ''}`} onClick={() => setZone(z.id)} title={zoneLabel(z.name)}>
            <span>{zoneLabel(z.name)}</span> <small>{z.n}</small>
          </button>
        ))}
      </div>
      {uploadMsg && <div className="note warn small">{uploadMsg}</div>}

      <ul className="evlist" role="listbox" aria-label="Olaylar (saat sırasıyla)" ref={listRef}>
        {visible.map((e) => {
          const risk = e.last_run?.risk_level ?? null
          const active = e.image_id === selectedId
          const running = e.image_id === runningId
          return (
            <li key={e.image_id} role="option" aria-selected={active} data-id={e.image_id}>
              <button className={`evrow ${active ? 'active' : ''}`} onClick={() => onSelect(e.image_id)} onDoubleClick={() => !busy && onRun({ imageId: e.image_id })} title={`${e.image_id} · ${zoneLabel(e.zone_name)} — çift tıkla: değerlendir`}>
                <Thumb imageId={e.image_id} />
                <span className="ev-meta">
                  <span className="ev-time">{e.capture_time}</span>
                  {running ? (
                    <span className="rpill run">çalışıyor…</span>
                  ) : risk ? (
                    <span className="rpill" style={{ background: RISK_COLOR[risk], color: inkOn(RISK_COLOR[risk]) }}>{RISK_LABEL[risk]}</span>
                  ) : e.last_run?.status === 'failed' ? (
                    <span className="rpill fail">hata</span>
                  ) : (
                    <span className="rpill none">—</span>
                  )}
                  <span className="ev-zone">{zoneLabel(e.zone_name)}</span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
