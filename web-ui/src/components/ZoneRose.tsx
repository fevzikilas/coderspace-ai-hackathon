import { useMemo } from 'react'
import L from 'leaflet'
import { Marker, Pane, Polygon } from 'react-leaflet'
import { balanceName, destination, sectorPolygon, zoneSectors } from '../geo'
import { zoneLabel } from '../zoneLabels'
import type { Base, ZoneInfo } from '../types'

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string)

// Dilimler bilerek NÖTR/gri: araç renkleriyle (kategorik palet) karışmasın; yalnızca AKTİF dilim vurgu (beyaz) alır.
const INACTIVE = { color: '#8b98a8', weight: 1, opacity: 0.32, fillColor: '#8b98a8', fillOpacity: 0.035 }
const ACTIVE = { color: '#f1f5f9', weight: 2.5, opacity: 0.95, fillColor: '#f1f5f9', fillOpacity: 0.16 }

function labelIcon(name: string, active: boolean): L.DivIcon {
  const [a, b] = balanceName(name)
  return L.divIcon({
    className: `rose-label ${active ? 'active' : ''}`,
    iconSize: [0, 0],
    html: `<span>${esc(a)}${b ? `<br>${esc(b)}` : ''}</span>`,
  })
}

interface Props {
  base: Base
  zones: ZoneInfo[]
  /** olayın ait olduğu bölge (yoksa hiçbir dilim vurgulanmaz) */
  activeZoneId: string | null
}

/** Üssü merkez alan, uyarı yarıçapına kadar uzanan "pusula gülü": bölge başına bir dilim (8 bölgede 8 x 45°), bölge adıyla etiketli. */
export default function ZoneRose({ base, zones, activeZoneId }: Props) {
  const sectors = useMemo(() => zoneSectors(zones), [zones])
  if (sectors.length === 0) return null
  const R = base.alert_radius_m
  return (
    <>
      <Pane name="rose" style={{ zIndex: 350 }}>
        {sectors.map((s) => {
          const active = s.zoneId === activeZoneId
          return (
            <Polygon key={s.zoneId} positions={sectorPolygon(base.lat, base.lon, s.start, s.end, R)} pathOptions={active ? ACTIVE : INACTIVE} interactive={false} />
          )
        })}
      </Pane>
      <Pane name="roseLabels" style={{ zIndex: 360, pointerEvents: 'none' }}>
        {sectors.map((s) => {
          const active = s.zoneId === activeZoneId
          return (
            <Marker
              key={`${s.zoneId}-${active}`}
              position={destination(base.lat, base.lon, s.bearing, R * 0.74)}
              icon={labelIcon(zoneLabel(s.name), active)}
              interactive={false}
              keyboard={false}
            />
          )
        })}
      </Pane>
    </>
  )
}
