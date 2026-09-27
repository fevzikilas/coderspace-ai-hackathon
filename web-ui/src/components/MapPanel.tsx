import { useEffect, useState } from 'react'
import L from 'leaflet'
import { Circle, CircleMarker, MapContainer, Marker, Pane, Polygon, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { destination, fmtClock, fovSector } from '../geo'
import { colorOf } from '../vehicleColors'
import { findGraphNode, linkLabel } from '../vehicleGraph'
import ZoneRose from './ZoneRose'
import { DRONE_STATUS_COLOR, PATTERN_COLOR, PATTERN_LABEL, RISK_COLOR, RISK_LABEL, TILE_URL } from '../theme'
import type { Base, DetectionObj, Drone, EventInfo, Vehicle, VehicleGraph, ZoneInfo } from '../types'

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

function FitOnDemand({ base, trigger, insetRight }: { base: Base; trigger: number; insetRight: number }) {
  const map = useMap()
  useEffect(() => {
    map.fitBounds(L.latLng(base.lat, base.lon).toBounds(base.alert_radius_m * 2.2), { animate: trigger > 1, paddingBottomRight: [insetRight, 0] })
    // yalnızca tetikleyici değişince (ilk yükleme, kullanıcı butonu veya üs değişimi)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trigger, map, base.lat, base.lon])
  return null
}

/** Harita kabı boyut değiştirince (pencere, dar ekran düzeni) Leaflet'e haber verir; yoksa karolar eksik çizilir. */
function AutoResize() {
  const map = useMap()
  useEffect(() => {
    const ro = new ResizeObserver(() => map.invalidateSize({ animate: false }))
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [map])
  return null
}

/** Son tespite (olayın izi + ayak izi + üs, veya demo araçları) yakınlaşır. `tick` değişince tetiklenir. `insetRight`: sağ üstteki özet kartın kapladığı genişlik (px). */
function FocusOnDemand({ points, tick, pad, maxZoom = 16, insetRight = 0 }: { points: [number, number][]; tick: string; pad: number; maxZoom?: number; insetRight?: number }) {
  const map = useMap()
  useEffect(() => {
    if (!tick || points.length === 0) return
    map.fitBounds(L.latLngBounds(points.map(([a, b]) => L.latLng(a, b))).pad(pad), { maxZoom, animate: true, paddingTopLeft: [10, 10], paddingBottomRight: [insetRight + 10, 10] })
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
  /** araç renkleri (track_id → renk): görüntüdeki kutu ve araç sekmesiyle AYNI */
  colors: Record<string, string>
  /** zones.json bölgeleri: pusula gülü dilimleri */
  zones: ZoneInfo[]
  activeZoneId: string | null
  vehicleGraph: VehicleGraph
  selectedVehicleLinkId: string | null
  onSelectVehicleLink: (linkId: string) => void
  /** haritanın sağ üstüne bindirilen özet kartın genişliği (px, dar ekranda 0): odaklanırken bu alan boş bırakılır */
  insetRight?: number
}

// Dar ekranda harita küçük: lejand kapalı (tek satır) başlar, kullanıcı açabilir
const legendOpenInitially = typeof window === 'undefined' || window.matchMedia('(min-width: 1100px)').matches

const CORNER_ORDER = ['top_left', 'top_right', 'bottom_right', 'bottom_left'] as const

export default function MapPanel({ base, drones, vehicles, detections, event, selectedId, onSelect, focusKey, focusDroneId, colors, zones, activeZoneId, vehicleGraph, selectedVehicleLinkId, onSelectVehicleLink, insetRight = 0 }: Props) {
  const [fitTick, setFitTick] = useState(1)
  const [manualFocus, setManualFocus] = useState(0)
  const center: [number, number] = [base.lat, base.lon]

  const footprint: [number, number][] | null = event ? CORNER_ORDER.map((k) => event.corner_coordinates[k]) : null
  // olay akışında araç konumu = görüntüden hesaplanan konum (tespit); yoksa izin son noktası
  const detPos = new Map(detections.filter((d) => d.vehicle_id).map((d) => [d.vehicle_id as string, [d.lat, d.lon] as [number, number]]))
  const untracked = event ? detections.filter((d) => !d.vehicle_id) : []

  // Olay modunda otomatik odak = tüm pusula gülü (uyarı halkası); "Tespite odaklan" düğmesi ayak izi + izlere yakınlaşır
  const rosePoints: [number, number][] = [0, 90, 180, 270].map((b) => destination(base.lat, base.lon, b, base.alert_radius_m))
  const focusPoints: [number, number][] = event
    ? [center, ...(footprint ?? []), ...vehicles.flatMap((v) => v.trace.map((p) => [p.lat, p.lon] as [number, number]))]
    : [
        ...vehicles.filter((v) => v.in_latest_detection).map((v) => [v.last_position.lat, v.last_position.lon] as [number, number]),
        ...drones.filter((d) => d.id === focusDroneId).map((d) => [d.lat, d.lon] as [number, number]),
      ]

  return (
    <div className="map-wrap">
      <MapContainer center={center} zoom={12} zoomSnap={0.25} zoomDelta={0.5} className="map" scrollWheelZoom>
        {/* OSM karo kullanım kuralı Referer ister; sayfa geneli 'no-referrer' (nginx) olduğu için karolara YALNIZCA köken (yol/sorgu değil) gönderilir,
            yoksa yakın zoom'da (önbellekte olmayan karolar) "Access blocked" karosu döner. */}
        <TileLayer url={TILE_URL} attribution='&copy; OpenStreetMap' maxZoom={19} className="dark-tiles" referrerPolicy="strict-origin-when-cross-origin" />
        <AutoResize />
        <FitOnDemand base={base} trigger={fitTick} insetRight={insetRight} />
        {event ? (
          <>
            <FocusOnDemand points={rosePoints} tick={focusKey ? `r${focusKey}` : ''} pad={0.04} maxZoom={14} insetRight={insetRight} />
            <FocusOnDemand points={focusPoints} tick={manualFocus ? `m${manualFocus}` : ''} pad={0.2} insetRight={insetRight} />
          </>
        ) : (
          <FocusOnDemand points={focusPoints} tick={manualFocus ? `m${manualFocus}` : focusKey ? `r${focusKey}` : ''} pad={0.6} />
        )}

        {/* Bölge pusula gülü (olay akışı): üssü merkez alan 8 dilim, olayın bölgesi vurgulu */}
        {event && <ZoneRose base={base} zones={zones} activeZoneId={activeZoneId} />}

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
          <Polygon positions={footprint} pathOptions={{ color: '#e2e8f0', weight: 2, dashArray: '4 4', fillColor: '#e2e8f0', fillOpacity: 0.07 }}>
            <Tooltip sticky>
              Görüntü ayak izi — {event?.image_id} · {event?.capture_time}
              {event?.footprint_m ? ` · ${Math.round(event.footprint_m.width)}×${Math.round(event.footprint_m.height)} m` : ''}
            </Tooltip>
          </Polygon>
        )}

        {/* Cross-event görsel adaylar: bağımsız kanıt edge'leri, kimlik kümesi değildir. */}
        <Pane name="vehicle-evidence" style={{ zIndex: 620 }}>
          {vehicleGraph.edges.map((link) => {
            const source = findGraphNode(vehicleGraph, link.source_event_id, link.source_track_id)
            const target = findGraphNode(vehicleGraph, link.target_event_id, link.target_track_id)
            if (!source?.position || !target?.position) return null
            const selectedLink = link.link_id === selectedVehicleLinkId
            return (
              <Polyline
                key={link.link_id}
                positions={[[source.position.lat, source.position.lon], [target.position.lat, target.position.lon]]}
                pathOptions={{ color: selectedLink ? '#ffffff' : '#f5a524', weight: selectedLink ? 5 : 3, opacity: 0.9, dashArray: '8 7' }}
                eventHandlers={{ click: () => onSelectVehicleLink(link.link_id) }}
              >
                <Tooltip sticky>{linkLabel(link)}<br />Cross-event evidence, confirmed identity değil</Tooltip>
              </Polyline>
            )
          })}
          {vehicleGraph.nodes.filter((node) => node.position).map((node) => {
            const endpointSelected = vehicleGraph.edges.some((link) => link.link_id === selectedVehicleLinkId && (
              (link.source_event_id === node.event_id && link.source_track_id === node.track_id) ||
              (link.target_event_id === node.event_id && link.target_track_id === node.track_id)
            ))
            return (
              <CircleMarker key={node.node_id} center={[node.position!.lat, node.position!.lon]} radius={endpointSelected ? 8 : 5}
                pathOptions={{ color: endpointSelected ? '#ffffff' : '#f5a524', weight: 2, fillColor: '#121821', fillOpacity: 0.95 }}>
                <Tooltip>{node.event_id} · {node.track_id}<br />{fmtClock(node.timestamp)}</Tooltip>
              </CircleMarker>
            )
          })}
        </Pane>

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
            const color = event ? colorOf(colors, v.vehicle_id) : vehicleColor(v)
            const pos: [number, number] = detPos.get(v.vehicle_id) ?? [v.last_position.lat, v.last_position.lon]
            const selected = v.vehicle_id === selectedId
            return (
              <span key={v.vehicle_id}>
                {v.trace.length > 1 && (
                  <Polyline
                    positions={v.trace.map((p) => [p.lat, p.lon] as [number, number])}
                    pathOptions={{ color, weight: v.in_latest_detection ? 3 : 2, opacity: v.in_latest_detection ? 0.95 : 0.5 }}
                  />
                )}
                {/* olay akışı: geçmiş iz noktaları (5 dk) saat etiketiyle */}
                {event &&
                  v.trace.map((p) => (
                    <CircleMarker key={p.ts} center={[p.lat, p.lon]} radius={3.5} pathOptions={{ color, weight: 1.5, fillColor: color, fillOpacity: 0.85 }}>
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
          Tespite odaklan
        </button>
        <button className="map-btn" onClick={() => setFitTick((n) => n + 1)} title="Üssü ortala">
          Üssü ortala
        </button>
      </div>
      {/* LEJAND: ayrı panel değil, haritanın sol altına bindirilmiş yarı saydam kutu (başlığa tıklayınca küçülür) */}
      <details className="map-legend" open={legendOpenInitially}>
        <summary>Lejand{event ? ` · ${event.capture_time}` : ''}</summary>
        <div className="lg-grid">
          {event ? (
            <>
              <i className="lg-sym legend-multi" /><span>Araç (renk = görüntüdeki kutu ve sekmeyle aynı)</span>
              <i className="lg-sym legend-small" /><span>Aracın geçmiş konumları (5 dk arayla)</span>
              <i className="lg-sym lg-line" /><span>Üsse yaklaşma yönü</span>
              <i className="lg-sym lg-untracked" /><span>Hareketi bilinmeyen araç</span>
            </>
          ) : (
            <>
              <i className="lg-sym lg-drone" /><span>Drone (yön + görüş alanı)</span>
              <i className="lg-sym" style={{ background: RISK_COLOR.HIGH }} /><span>Araç — risk (son tespit)</span>
              <i className="lg-sym" style={{ background: PATTERN_COLOR.LOITERING }} /><span>Bekleyen araç</span>
              <i className="lg-sym lg-line" /><span>Üsse yaklaşma yönü</span>
            </>
          )}
          <i className="lg-sym lg-base">★</i><span>Üs</span>
          <i className="lg-sym lg-ring" /><span>Üs sınırı</span>
          <i className="lg-sym lg-alert" /><span>Uyarı halkası</span>
          {event && (
            <>
              <i className="lg-sym lg-slice" /><span>Olayın bölgesi (vurgulu dilim)</span>
              <i className="lg-sym lg-fp" /><span>Görüntünün kapladığı alan</span>
              <i className="lg-sym lg-candidate" /><span>Possible vehicle match (cross-event evidence)</span>
            </>
          )}
        </div>
      </details>
    </div>
  )
}
