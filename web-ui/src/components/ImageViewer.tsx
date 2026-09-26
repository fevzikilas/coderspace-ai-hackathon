import { useEffect, useState } from 'react'
import { fetchImageObjectUrl } from '../api'
import { RISK_COLOR } from '../theme'
import type { DetectionObj, EventInfo, RunResult, Vehicle } from '../types'

interface Props {
  result: RunResult | null
  event: EventInfo | null
  vehicles: Vehicle[]
  selectedId: string | null
  onSelect: (id: string) => void
}

const PAD = 14 // küçük araç kutularını okunur kılmak için görsel dolgu (kaynak piksel)

function boxColor(d: DetectionObj, vehicles: Vehicle[], risk: string | undefined): string {
  const v = vehicles.find((x) => x.vehicle_id === d.vehicle_id)
  if (v?.risk_level) return RISK_COLOR[v.risk_level]
  if (risk && (risk === 'LOW' || risk === 'MEDIUM' || risk === 'HIGH')) return RISK_COLOR[risk]
  return '#4cc9f0'
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

export default function ImageViewer({ result, event, vehicles, selectedId, onSelect }: Props) {
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
    return <div className="empty">Henüz görüntü yok. Listeden bir olay seçip değerlendirin.</div>
  }
  const { width: w, height: h } = image
  const risk = result?.assessment?.risk_level
  const fs = Math.max(11, w / 55)

  return (
    <div className="imgview">
      <svg viewBox={`0 0 ${w} ${h}`} className="imgsvg" role="img" aria-label="Drone görüntüsü ve tespit kutuları">
        {src ? <image href={src} x={0} y={0} width={w} height={h} preserveAspectRatio="none" /> : <SyntheticScene w={w} h={h} dets={dets} />}
        {dets.map((d) => {
          const untracked = !d.vehicle_id && !!event // olay akışında izle eşleşmeyen nesne
          const color = untracked ? '#8b98a8' : boxColor(d, vehicles, risk)
          const sel = d.vehicle_id === selectedId
          const x = d.bbox.x1 - PAD
          const y = d.bbox.y1 - PAD
          const bw = d.bbox.x2 - d.bbox.x1 + 2 * PAD
          const bh = d.bbox.y2 - d.bbox.y1 + 2 * PAD
          const label = `${d.class} ${(d.conf * 100).toFixed(0)}%${d.vehicle_id ? ' · ' + d.vehicle_id : untracked ? ' · izsiz' : ''}`
          return (
            <g key={d.box_index} onClick={() => d.vehicle_id && onSelect(d.vehicle_id)} style={{ cursor: d.vehicle_id ? 'pointer' : 'default' }}>
              <rect x={x} y={y} width={bw} height={bh} fill="none" stroke={color} strokeWidth={sel ? 5 : 3} strokeDasharray={untracked ? '8 6' : undefined} />
              <rect x={x} y={y - fs * 1.5} width={label.length * fs * 0.62 + 8} height={fs * 1.5} fill={color} />
              <text x={x + 4} y={y - fs * 0.42} fontSize={fs} fontWeight={700} fill="#0b0f14" fontFamily="ui-monospace, monospace">
                {label}
              </text>
            </g>
          )
        })}
        {event && (
          <g pointerEvents="none">
            <rect x={0} y={0} width={fs * 11.5} height={fs * 2.2} fill="rgba(11,15,20,0.82)" />
            <text x={fs * 0.6} y={fs * 1.55} fontSize={fs * 1.25} fontWeight={800} fill="#4cc9f0" fontFamily="ui-monospace, monospace">
              {event.capture_time}
              <tspan fontSize={fs} fontWeight={500} fill="#c7d2de"> itibarıyla</tspan>
            </text>
          </g>
        )}
      </svg>
      <div className="img-caption">
        <span>{image.image_id}</span>
        {event && <span>çekim: <b>{event.capture_time}</b> · {event.zone.name}</span>}
        <span>
          {w}×{h} · {image.mode === 'mock' ? 'MOCK dedektör (ground-truth bbox)' : `model: ${image.backend === 'dfine' ? 'D-FINE' : image.backend === 'ultralytics' ? 'YOLO' : (image.backend ?? '?')}`} · {dets.length} tespit
          {event ? ` · ${dets.filter((d) => d.vehicle_id).length} izle eşleşti` : ''}
        </span>
        {image.fallback_reason && <span className="chip chip-warn" title={image.fallback_reason}>model yüklenemedi → mock</span>}
        {!src && <span className="chip chip-warn">{loadError ? 'görüntü alınamadı' : 'sentetik kare (görüntü yok)'}</span>}
      </div>
    </div>
  )
}
