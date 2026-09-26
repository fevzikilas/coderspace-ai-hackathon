import { useEffect, useState } from 'react'
import { fetchImageObjectUrl } from '../api'
import { colorOf, inkOn } from '../vehicleColors'
import { zoneLabel } from '../zoneLabels'
import type { DetectionObj, EventInfo, RunResult } from '../types'

interface Props {
  result: RunResult | null
  event: EventInfo | null
  /** araç renkleri (track_id → renk): haritadaki iz ve araç sekmesiyle AYNI */
  colors: Record<string, string>
  selectedId: string | null
  onSelect: (id: string) => void
  /** küçük önizleme (özet kart): yalnızca görüntü + kutular; etiket, saat bandı ve alt yazı yok */
  compact?: boolean
  /** panel kutusuna sığdırma oranı (0.8 = kutunun %80'i, en-boy oranı korunur); verilmezse genişliğe yayılır */
  fit?: number
}

const PAD = 14 // küçük araç kutularını okunur kılmak için görsel dolgu (kaynak piksel)

/** İzli araç: kendi sabit rengi (harita/sekmeyle aynı). İzsiz nesne: gri kesikli. Olay dışı (eski/demo akış) tespit: nötr. */
function boxColor(d: DetectionObj, colors: Record<string, string>): string {
  return d.vehicle_id ? colorOf(colors, d.vehicle_id) : '#8b98a8'
}

/** Gerçek görüntü yoksa (mock mod) kutulardan sentetik bir hava sahnesi çizer. */
function SyntheticScene({ w, h, dets }: { w: number; h: number; dets: DetectionObj[] }) {
  const cx = dets.length ? dets.reduce((s, d) => s + (d.bbox.x1 + d.bbox.x2) / 2, 0) / dets.length : w / 2
  return (
    <g>
      <defs>
        <pattern id="grass" width="40" height="40" patternUnits="userSpaceOnUse">
          <rect width="40" height="40" fill="#1d2b1b" />
          <circle cx="8" cy="9" r="1.6" fill="#243522" />
          <circle cx="27" cy="22" r="1.4" fill="#182416" />
          <circle cx="17" cy="33" r="1.2" fill="#263a24" />
        </pattern>
      </defs>
      <rect width={w} height={h} fill="url(#grass)" />
      <rect x={cx - 34} y={0} width={68} height={h} fill="#2a2d33" />
      <rect x={cx - 36} y={0} width={3} height={h} fill="#4a4d55" />
      <rect x={cx + 33} y={0} width={3} height={h} fill="#4a4d55" />
      <line x1={cx} y1={0} x2={cx} y2={h} stroke="#c9b458" strokeWidth={3} strokeDasharray="34 26" opacity={0.75} />
      {dets.map((d) => (
        <rect key={d.box_index} x={d.bbox.x1} y={d.bbox.y1} width={d.bbox.x2 - d.bbox.x1} height={d.bbox.y2 - d.bbox.y1} rx={3} fill="#d5dbe3" opacity={0.92} />
      ))}
    </g>
  )
}

export default function ImageViewer({ result, event, colors, selectedId, onSelect, compact = false, fit }: Props) {
  const image = result?.image ?? null
  const dets = result?.detections ?? []
  const [src, setSrc] = useState<string | null>(null)
  const [loadError, setLoadError] = useState(false)
  const imageId = image?.url ? image.image_id : null

  useEffect(() => {
    setLoadError(false)
    if (!imageId) {
      setSrc(null)
      return
    }
    let url: string | null = null
    let cancelled = false
    fetchImageObjectUrl(imageId)
      .then((u) => {
        url = u
        if (cancelled) URL.revokeObjectURL(u)
        else setSrc(u)
      })
      .catch(() => !cancelled && setLoadError(true))
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [imageId])

  if (!image) {
    return compact ? <div className="thumb-empty">görüntü yok</div> : <div className="empty">Henüz görüntü yok. Listeden bir olay seçip değerlendirin.</div>
  }
  const { width: w, height: h } = image
  const fs = Math.max(11, w / 55)

  return (
    <div className={`imgview ${compact ? 'compact' : ''} ${fit ? 'fit' : ''}`}>
      <div className="imgbox">
      <svg viewBox={`0 0 ${w} ${h}`} style={fit ? { width: `${fit * 100}%`, height: `${fit * 100}%` } : undefined} className="imgsvg" role="img" aria-label="Drone görüntüsü ve tespit kutuları">
        {src ? <image href={src} x={0} y={0} width={w} height={h} preserveAspectRatio="none" /> : <SyntheticScene w={w} h={h} dets={dets} />}
        {dets.map((d) => {
          const untracked = !d.vehicle_id && !!event // olay akışında izle eşleşmeyen nesne
          const color = untracked ? '#8b98a8' : boxColor(d, colors)
          const ink = inkOn(color)
          const sel = d.vehicle_id === selectedId
          const x = d.bbox.x1 - PAD
          const y = d.bbox.y1 - PAD
          const bw = d.bbox.x2 - d.bbox.x1 + 2 * PAD
          const bh = d.bbox.y2 - d.bbox.y1 + 2 * PAD
          const label = `${d.class} ${(d.conf * 100).toFixed(0)}%${d.vehicle_id ? ' · ' + d.vehicle_id : untracked ? ' · izsiz' : ''}`
          // Etiket kutunun üstüne; görüntü kenarına taşarsa kutunun İÇİNE / sola kaydırılır (kadraj dışına kesik etiket çıkmasın)
          const lw = label.length * fs * 0.62 + 8
          const ly = y - fs * 1.5 >= 0 ? y - fs * 1.5 : Math.max(0, y)
          const lx = Math.max(0, Math.min(x, w - lw))
          return (
            <g key={d.box_index} onClick={() => !compact && d.vehicle_id && onSelect(d.vehicle_id)} style={{ cursor: d.vehicle_id && !compact ? 'pointer' : undefined }}>
              <rect x={x} y={y} width={bw} height={bh} fill="none" stroke={color} strokeWidth={(sel ? 6 : 3.5) * (compact ? w / 500 : 1)} strokeDasharray={untracked ? '8 6' : undefined} />
              {sel && !compact && <rect x={x - 3} y={y - 3} width={bw + 6} height={bh + 6} fill="none" stroke="#ffffff" strokeWidth={1.5} />}
              {!compact && (<>
              <rect x={lx} y={ly} width={lw} height={fs * 1.5} fill={color} />
              <text x={lx + 4} y={ly + fs * 1.08} fontSize={fs} fontWeight={700} fill={ink} fontFamily="ui-monospace, monospace">
                {label}
              </text>
              </>)}
            </g>
          )
        })}
      </svg>
      </div>
      {!compact && !fit && <div className="img-caption">
        <span>{image.image_id}</span>
        {event && <span>çekim: <b>{event.capture_time}</b> · {zoneLabel(event.zone.name)}</span>}
        <span>
          {w}×{h} · {image.mode === 'mock' ? 'MOCK dedektör (ground-truth bbox)' : `model: ${image.backend === 'dfine' ? 'D-FINE' : image.backend === 'ultralytics' ? 'YOLO' : (image.backend ?? '?')}`} · {dets.length} tespit
          {event ? ` · ${dets.filter((d) => d.vehicle_id).length} izle eşleşti` : ''}
        </span>
        {image.fallback_reason && <span className="chip chip-warn" title={image.fallback_reason}>model yüklenemedi → mock</span>}
        {!src && <span className="chip chip-warn">{loadError ? 'görüntü alınamadı' : 'sentetik kare (görüntü yok)'}</span>}
      </div>}
    </div>
  )
}
