import { Fragment, useEffect, useMemo } from 'react'
import L from 'leaflet'
import { Circle, CircleMarker, MapContainer, Polygon, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { TILE_URL } from '../theme'

// Simülasyon haritası: üs + sınırı, bölge merkezleri, olayın görüntü alanı, araçlar ve son 10 dk izleri.
// `colored` false iken (analiz sürüyor) araçlar nötr gri; sonuç açıklanınca yaklaşan/yaklaşmayan/bilinmeyen renklerine geçer.

export interface SimBase { name: string; lat: number; lon: number; radius_m: number }
export interface SimZone { zone_id: string; name: string; lat: number; lon: number }
export interface SimMapVehicle { lat: number; lon: number; state: 'approaching' | 'other' | 'unknown'; label: string; eta_min: number | null; trail: [number, number][] }

const COLOR = { approaching: '#ff453a', other: '#e6edf3', unknown: '#f5a524', neutral: '#8b98a8' }

function FitView({ bounds, id }: { bounds: L.LatLngBounds; id: string }) {
  const map = useMap()
  useEffect(() => {
    map.flyToBounds(bounds, { padding: [28, 28], duration: 0.9, maxZoom: 16 })
    // bounds her olayda yeniden kurulur; yalnızca olay değişince uç
  }, [map, id])
  return null
}

export default function SimMap(props: {
  id: string
  base: SimBase
  zones: SimZone[]
  zoneId: string | null
  footprint: [number, number][]
  vehicles: SimMapVehicle[]
  colored: boolean
}) {
  const { id, base, zones, zoneId, footprint, vehicles, colored } = props
  const bounds = useMemo(() => {
    // izler çerçeveye katılmaz (km'lerce uzanabilir): üs + görüntü alanı + araçlar
    const pts: [number, number][] = [[base.lat, base.lon], ...footprint, ...vehicles.map((v): [number, number] => [v.lat, v.lon])]
    return L.latLngBounds(pts).pad(0.15)
  }, [base, footprint, vehicles])
  const lead = useMemo(
    () => vehicles.filter((v) => v.state === 'approaching').sort((a, b) => (a.eta_min ?? 1e9) - (b.eta_min ?? 1e9))[0],
    [vehicles],
  )
  const touch = typeof window !== 'undefined' && 'ontouchstart' in window

  return (
    <MapContainer
      className="sim-map"
      bounds={bounds}
      boundsOptions={{ padding: [28, 28] }}
      scrollWheelZoom={false}
      dragging={!touch}
      attributionControl
      zoomControl
    >
      {/* OSM karo kuralı Referer ister; sayfa geneli 'no-referrer' olduğundan karolara yalnızca köken gönderilir (MapPanel ile aynı) */}
      <TileLayer url={TILE_URL} attribution="&copy; OpenStreetMap" maxZoom={19} className="dark-tiles" referrerPolicy="strict-origin-when-cross-origin" />
      <FitView bounds={bounds} id={id} />

      {zones.map((z) => (
        <CircleMarker key={z.zone_id} center={[z.lat, z.lon]} radius={z.zone_id === zoneId ? 6 : 4}
          pathOptions={{ color: z.zone_id === zoneId ? '#4cc9f0' : '#5b6675', weight: 2, fillOpacity: 0.6 }}>
          <Tooltip direction="top" permanent={z.zone_id === zoneId} className="sim-map-tip">{z.name}</Tooltip>
        </CircleMarker>
      ))}

      <Circle center={[base.lat, base.lon]} radius={base.radius_m} pathOptions={{ color: '#ff453a', weight: 2, fillColor: '#ff453a', fillOpacity: 0.1 }} />
      <CircleMarker center={[base.lat, base.lon]} radius={5} pathOptions={{ color: '#ff453a', weight: 2, fillColor: '#ff453a', fillOpacity: 1 }}>
        <Tooltip direction="bottom" permanent className="sim-map-tip">{base.name}</Tooltip>
      </CircleMarker>

      {footprint.length >= 3 && (
        <Polygon positions={footprint} pathOptions={{ color: '#ffffff', weight: 1.5, dashArray: '4 4', fillOpacity: 0.06 }} />
      )}

      {colored && lead && (
        <Polyline positions={[[lead.lat, lead.lon], [base.lat, base.lon]]} pathOptions={{ color: COLOR.approaching, weight: 2, dashArray: '6 6', opacity: 0.9 }}>
          {lead.eta_min != null && (
            <Tooltip permanent direction="center" className="sim-map-tip eta">~{Math.max(1, Math.round(lead.eta_min))} dk</Tooltip>
          )}
        </Polyline>
      )}

      {vehicles.map((v, i) => {
        const c = colored ? COLOR[v.state] : COLOR.neutral
        return (
          <Fragment key={`${id}-${i}`}>
            {v.trail.length > 1 && <Polyline positions={v.trail} pathOptions={{ color: c, weight: 2, opacity: 0.7 }} />}
            <CircleMarker center={[v.lat, v.lon]} radius={colored && v.state === 'approaching' ? 6 : 4.5}
              pathOptions={{ color: c, weight: 2, fillColor: c, fillOpacity: 0.9, dashArray: v.state === 'unknown' && colored ? '2 3' : undefined }}>
              <Tooltip>{v.label}</Tooltip>
            </CircleMarker>
          </Fragment>
        )
      })}
    </MapContainer>
  )
}
