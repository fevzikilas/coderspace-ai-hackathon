import { useState } from 'react'
import { zoneLabel } from '../zoneLabels'
import ImageViewer from './ImageViewer'
import type { EventInfo, RunResult } from '../types'

interface Props {
  result: RunResult | null
  event: EventInfo | null
  colors: Record<string, string>
  selectedId: string | null
  onSelect: (id: string) => void
  onEnlarge: () => void
}

// Sığdırma modları: %80 (varsayılan; kutunun %80'i, kenarda nefes payı) · %100 (kutuya tam sığ). En-boy oranı her iki modda korunur.
const FITS = [
  { value: 0.8, label: '%80' },
  { value: 1, label: '%100' },
] as const

/** GÖRÜNTÜ PANELİ (haritanın üstünde, sağ): yalnızca görüntü + tespit kutuları. Saat/bölge başlıkta; görüntünün üstüne başka öğe bindirilmez. */
export default function ImagePanel({ result, event, colors, selectedId, onSelect, onEnlarge }: Props) {
  const [fit, setFit] = useState<number>(0.8)
  const image = result?.image ?? null
  return (
    <section className="imgpanel" aria-label="Görüntü ve tespit kutuları">
      <header className="imgpanel-head">
        <b>Görüntü</b>
        {event && <span className="muted">{event.capture_time} · {zoneLabel(event.zone.name)}</span>}
        <div className="seg" role="radiogroup" aria-label="Sığdırma">
          {FITS.map((f) => (
            <button key={f.value} role="radio" aria-checked={fit === f.value} className={fit === f.value ? 'active' : ''} onClick={() => setFit(f.value)}>
              {f.label}
            </button>
          ))}
        </div>
        <button className="seg-btn" onClick={onEnlarge} disabled={!image} title="Görüntüyü etiketleriyle büyük aç">
          ⤢ Büyüt
        </button>
      </header>
      <div className="imgpanel-body">
        <ImageViewer result={result} event={event} colors={colors} selectedId={selectedId} onSelect={onSelect} fit={fit} />
      </div>
    </section>
  )
}
