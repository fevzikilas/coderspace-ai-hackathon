import { useEffect, useState } from 'react'
import { fetchImageObjectUrl } from '../api'
import type { CropRef } from '../types'

export default function CropThumbnail({ crop, label }: { crop: CropRef | null; label: string }) {
  const [src, setSrc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let objectUrl: string | null = null
    let cancelled = false
    setSrc(null)
    setFailed(false)
    if (!crop) return
    fetchImageObjectUrl(crop.image_id)
      .then((url) => {
        objectUrl = url
        if (cancelled) URL.revokeObjectURL(url)
        else setSrc(url)
      })
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [crop])

  if (!crop) return <div className="reid-crop empty small">Crop reference yok</div>
  const { x1, y1, x2, y2 } = crop.bbox
  return (
    <figure className="reid-crop">
      <div className="reid-crop-frame">
        {src ? (
          <svg viewBox={`${x1} ${y1} ${Math.max(1, x2 - x1)} ${Math.max(1, y2 - y1)}`} role="img" aria-label={`${label} araç crop'u`}>
            <image href={src} x={0} y={0} width={crop.image_width} height={crop.image_height} preserveAspectRatio="none" />
          </svg>
        ) : (
          <span>{failed ? 'Crop alınamadı' : 'Crop yükleniyor…'}</span>
        )}
      </div>
      <figcaption>{label} · {crop.image_id}</figcaption>
    </figure>
  )
}
