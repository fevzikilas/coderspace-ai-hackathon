import { useEffect, useState } from 'react'
import L from 'leaflet'
import { Circle, CircleMarker, MapContainer, Marker, Pane, Polygon, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { fmtClock, fovSector } from '../geo'
import { DRONE_STATUS_COLOR, PATTERN_COLOR, PATTERN_LABEL, RISK_COLOR, RISK_LABEL, TILE_URL } from '../theme'
import type { Base, DetectionObj, Drone, EventInfo, Vehicle } from '../types'

const iconCache = new Map<string, L.DivIcon>()

function droneIcon(heading: number, status: string): L.DivIcon {
  const key = `${status}-${Math.round(heading / 5) * 5}`
  let icon = iconCache.get(key)
  if (!icon) {
    const color = DRONE_STATUS_COLOR[status] ?? '#94a3b8'
    icon = L.divIcon({
      className: 'drone-icon',
      iconSize: [28, 28],
      iconAnchor: [14, 14],
      html: `<svg width="28" height="28" viewBox="0 0 28 28" style="transform:rotate(${Math.round(heading / 5) * 5}deg)">
        <path d="M14 3 L22 23 L14 18 L6 23 Z" fill="${color}" fill-opacity="0.9" stroke="#0b0f14" stroke-width="1.5"/></svg>`,
    })
    iconCache.set(key, icon)
  }
  return icon
}

const baseIcon = L.divIcon({
  className: 'base-icon',
  iconSize: [26, 26],
  iconAnchor: [13, 13],
  html: '<div class="base-star">★</div>',
})

function vehicleColor(v: Vehicle): string {
  if (v.in_latest_detection && v.risk_level) return RISK_COLOR[v.risk_level]
  if (v.pattern) return PATTERN_COLOR[v.pattern]
  return PATTERN_COLOR.RANDOM
}

function FitOnDemand({ base, trigger }: { base: Base; trigger: number }) {
  const map = useMap()
  useEffect(() => {
    map.fitBounds(L.latLng(base.lat, base.lon).toBounds(base.alert_radius_m * 2.2), { animate: trigger > 1 })
    // yalnızca tetikleyici değişince (ilk yükleme, kullanıcı butonu veya üs değişimi)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trigger, map, base.lat, base.lon])
  return null
}

/** Son tespite (olayın izi + ayak izi + üs, veya demo araçları) yakınlaşır. `tick` değişince tetiklenir. */
function FocusOnDemand({ points, tick, pad }: { points: [number, number][]; tick: string; pad: number }) {
  const map = useMap()
  useEffect(() => {
    if (!tick || points.length === 0) return
    map.fitBounds(L.latLngBounds(points.map(([a, b]) => L.latLng(a, b))).pad(pad), { maxZoom: 16, animate: true })
    // yalnızca tetikleyici değişince
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, map])
  return null
}

interface Props {
  base: Base
  drones: Drone[]
  vehicles: Vehicle[]
  /** olay akışı: görüntüden georeferanslanan tespitler (araç konumları) */
  detections: DetectionObj[]
  event: EventInfo | null
  selectedId: string | null
  onSelect: (id: string) => void
  /** Yeni başarılı koşu kimliği: değişince harita tespite odaklanır */
  focusKey: string | null
  focusDroneId: string | null
}

const CORNER_ORDER = ['top_left', 'top_right', 'bottom_right', 'bottom_left'] as const

export default function MapPanel({ base, drones, vehicles, detections, event, selectedId, onSelect, focusKey, focusDroneId }: Props) {
  const [fitTick, setFitTick] = useState(1)
  const [manualFocus, setManualFocus] = useState(0)
  const center: [number, number] = [base.lat, base.lon]

  const footprint: [number, number][] | null = event ? CORNER_ORDER.map((k) => event.corner_coordinates[k]) : null
  // olay akışında araç konumu = görüntüden hesaplanan konum (tespit); yoksa izin son noktası
  const detPos = new Map(detections.filter((d) => d.vehicle_id).map((d) => [d.vehicle_id as string, [d.lat, d.lon] as [number, number]]))
  const untracked = event ? detections.filter((d) => !d.vehicle_id) : []

  const focusPoints: [number, number][] = event
    ? [center, ...(footprint ?? []), ...vehicles.flatMap((v) => v.trace.map((p) => [p.lat, p.lon] as [number, number]))]
    : [
        ...vehicles.filter((v) => v.in_latest_detection).map((v) => [v.last_position.lat, v.last_position.lon] as [number, number]),
        ...drones.filter((d) => d.id === focusDroneId).map((d) => [d.lat, d.lon] as [number, number]),
      ]

  return (
    <div className="map-wrap">
      <MapContainer center={center} zoom={12} className="map" scrollWheelZoom>
        <TileLayer url={TILE_URL} attribution='&copy; OpenStreetMap' maxZoom={19} className="dark-tiles" />
        <FitOnDemand base={base} trigger={fitTick} />
        <FocusOnDemand points={focusPoints} tick={manualFocus ? `m${manualFocus}` : focusKey ? `r${focusKey}` : ''} pad={event ? 0.2 : 0.6} />

        {/* Üs sınırı + uyarı halkası */}
        <Circle center={center} radius={base.alert_radius_m} pathOptions={{ color: '#f5a524', weight: 1, dashArray: '6 8', fillOpacity: 0.02 }} />
        <Circle center={center} radius={base.radius_m} pathOptions={{ color: '#ff453a', weight: 2, fillColor: '#ff453a', fillOpacity: 0.1 }}>
          <Tooltip sticky>Üs sınırı — {base.radius_m} m</Tooltip>
        </Circle>
        <Marker position={center} icon={baseIcon} zIndexOffset={500}>
          <Tooltip direction="top" offset={[0, -10]}>{base.name}</Tooltip>
        </Marker>

        {/* Olay: görüntünün yer ayak izi (köşe koordinatları) */}
        {footprint && (
          <Polygon positions={footprint} pathOptions={{ color: '#4cc9f0', weight: 2, dashArray: '4 4', fillOpacity: 0.1 }}>
            <Tooltip sticky>
              Görüntü ayak izi — {event?.image_id} · {event?.capture_time}
              {event?.footprint_m ? ` · ${Math.round(event.footprint_m.width)}×${Math.round(event.footprint_m.height)} m` : ''}
            </Tooltip>
          </Polygon>
        )}

        {/* Drone'lar (yalnızca eski/demo akışı): konum + yön + görüş alanı */}
        {drones.map((d) => {
          const active = d.status === 'ACTIVE'
          const nadir = Math.abs(d.gimbal_pitch ?? -45) >= 80
          const color = DRONE_STATUS_COLOR[d.status] ?? '#94a3b8'
          return (
            <span key={d.id}>
              {active && !nadir && (
                <Polygon positions={fovSector(d.lat, d.lon, d.heading, d.fov, 600)} pathOptions={{ color, weight: 1, fillOpacity: 0.08 }} />
              )}
              {active && nadir && d.alt ? (
                <Circle center={[d.lat, d.lon]} radius={d.alt * Math.tan((d.fov / 2) * (Math.PI / 180))} pathOptions={{ color, weight: 1, fillOpacity: 0.12 }} />
              ) : null}
              <Marker position={[d.lat, d.lon]} icon={droneIcon(d.heading, d.status)}>
                <Tooltip direction="right" offset={[10, 0]}>
                  <b>{d.id}</b> {d.callsign ?? ''}
                  <br />
                  {d.status} · {Math.round(d.heading)}° · FOV {d.fov}°{d.alt ? ` · ${Math.round(d.alt)} m` : ''}
                </Tooltip>
              </Marker>
            </span>
          )
        })}

        {/* Araçlar: drone ikonlarının ÜSTÜNDE ayrı pane (çakışınca da tıklanabilir kalsın) */}
        <Pane name="vehicles" style={{ zIndex: 640 }}>
          {vehicles.map((v) => {
            const color = vehicleColor(v)
            const pos: [number, number] = detPos.get(v.vehicle_id) ?? [v.last_position.lat, v.last_position.lon]
            const selected = v.vehicle_id === selectedId
            return (
              <span key={v.vehicle_id}>
                {v.trace.length > 1 && (
                  <Polyline
                    positions={v.trace.map((p) => [p.lat, p.lon] as [number, number])}
                    pathOptions={{ color, weight: v.in_latest_detection ? 3 : 2, opacity: v.in_latest_detection ? 0.9 : 0.5 }}
                  />
                )}
                {/* olay akışı: geçmiş iz noktaları (5 dk) saat etiketiyle */}
                {event &&
                  v.trace.map((p) => (
                    <CircleMarker key={p.ts} center={[p.lat, p.lon]} radius={3} pathOptions={{ color, weight: 1, fillColor: '#0b0f14', fillOpacity: 1 }}>
                      <Tooltip direction="top" offset={[0, -4]}>{v.vehicle_id} · {fmtClock(p.ts)}</Tooltip>
                    </CircleMarker>
                  ))}
                {v.analysis.approaching && (
                  <Polyline positions={[pos, center]} pathOptions={{ color: '#ff453a', weight: 1.5, dashArray: '4 6', opacity: 0.7 }} />
                )}
                {selected && <CircleMarker center={pos} radius={15} pathOptions={{ color: '#ffffff', weight: 2, fill: false }} />}
                <CircleMarker
                  center={pos}
                  radius={v.in_latest_detection ? 8 : 6}
                  pathOptions={{ color: '#0b0f14', weight: 2, fillColor: color, fillOpacity: 1 }}
                  eventHandlers={{ click: () => onSelect(v.vehicle_id) }}
                >
                  <Tooltip direction="top" offset={[0, -8]}>
                    <b>{v.vehicle_id}</b> · {v.class}
                    {event ? (
                      <>
                        <br />
                        görüntüden hesaplanan konum ({event.capture_time})
                      </>
                    ) : null}
                    <br />
                    {v.analysis.speed_mps.toFixed(1)} m/s{v.analysis.approaching ? ' · YAKLAŞIYOR' : ''}
                    {v.pattern ? ` · ${PATTERN_LABEL[v.pattern]}` : ''}
                    {v.in_latest_detection && v.risk_level ? ` · RİSK ${RISK_LABEL[v.risk_level]}` : ''}
                  </Tooltip>
                </CircleMarker>
              </span>
            )
          })}
          {/* iziyle eşleşmeyen nesneler (park halindeki sivil araç vb.) */}
          {untracked.map((d) => (
            <CircleMarker key={`u${d.box_index}`} center={[d.lat, d.lon]} radius={5} pathOptions={{ color: '#8b98a8', weight: 2, dashArray: '2 3', fillColor: '#8b98a8', fillOpacity: 0.35 }}>
              <Tooltip direction="top" offset={[0, -6]}>{d.class} — izle (tracks.csv) eşleşmedi</Tooltip>
            </CircleMarker>
          ))}
        </Pane>
      </MapContainer>

      <div className="map-btns">
        <button className="map-btn" onClick={() => setManualFocus((n) => n + 1)} disabled={focusPoints.length === 0} title="Son tespite yakınlaş">
          ⌖ Tespite odaklan
        </button>
        <button className="map-btn" onClick={() => setFitTick((n) => n + 1)} title="Üssü ortala">
          ◎ Üssü ortala
        </button>
      </div>
      <div className="map-legend">
        <div className="legend-title">Harita{event ? ` — ${event.capture_time} itibarıyla` : ''}</div>
        {event ? (
          <>
            <div><i className="legend-dot" style={{ background: RISK_COLOR.HIGH }} /> Araç: görüntüden hesaplanan konum</div>
            <div><i className="legend-dot legend-small" /> İz noktası (5 dk, saat etiketli)</div>
            <div><i className="legend-dot lg-fp" /> Görüntü yer ayak izi</div>
            <div><i className="legend-dot" style={{ background: '#8b98a8', opacity: 0.6 }} /> İzsiz nesne</div>
          </>
        ) : (
          <>
            <div><i className="legend-dot lg-drone" /> Drone (yön + görüş alanı)</div>
            <div><i className="legend-dot" style={{ background: RISK_COLOR.HIGH }} /> Araç — risk (son tespit)</div>
            <div><i className="legend-dot" style={{ background: PATTERN_COLOR.LOITERING }} /> Bekleme (LOITERING)</div>
            <div><i className="legend-dot" style={{ background: PATTERN_COLOR.RANDOM }} /> Rastgele</div>
          </>
        )}
        <div><i className="legend-dot lg-line" /> Yaklaşma vektörü</div>
        <div><i className="legend-dot lg-ring" /> Üs sınırı / uyarı halkası</div>
      </div>
    </div>
  )
}
