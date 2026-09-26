const R = 6371008.8
const rad = (d: number) => (d * Math.PI) / 180
const deg = (r: number) => (r * 180) / Math.PI

export function haversine(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const dphi = rad(lat2 - lat1)
  const dlmb = rad(lon2 - lon1)
  const a = Math.sin(dphi / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dlmb / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)))
}

/** (lat, lon) noktasından `bearing` (derece, kuzeyden saat yönünde) yönünde `dist` metre ötedeki nokta. */
export function destination(lat: number, lon: number, bearing: number, dist: number): [number, number] {
  const b = rad(bearing)
  const d = dist / R
  const p1 = rad(lat)
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b))
  const l2 = rad(lon) + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2))
  return [deg(p2), deg(l2)]
}

/** Drone görüş konisi (harita gösterimi için sektör poligonu). */
export function fovSector(lat: number, lon: number, heading: number, fov: number, rangeM: number): [number, number][] {
  const pts: [number, number][] = [[lat, lon]]
  const steps = 8
  for (let i = 0; i <= steps; i++) {
    pts.push(destination(lat, lon, heading - fov / 2 + (fov * i) / steps, rangeM))
  }
  return pts
}

export const fmtDist = (m: number | null | undefined): string =>
  m == null ? '—' : m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`

/** Yerel saat (sunucu olayları: log satırları vb.). */
export const fmtTime = (iso: string | null | undefined): string => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('tr-TR', { hour12: false })
}

/** Veri seti zamanları "HH:MM" tarihsizdir ve UTC olarak kodlanır; saat dilimine göre KAYMAMASI için UTC gösterilir. */
export const fmtClock = (iso: string | null | undefined): string => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('tr-TR', { hour12: false, hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })
}

/** `ts`'in `refIso` (olay anı) öncesindeki yaşı; referans yoksa şimdiye göre (yalnızca eski/demo akışı). */
export const ageMin = (iso: string, refIso?: string | null, precomputed?: number | null): string => {
  const m = precomputed ?? Math.round(((refIso ? new Date(refIso).getTime() : Date.now()) - new Date(iso).getTime()) / 60000)
  if (m < 1) return refIso ? 'olay anında' : 'şimdi'
  const suffix = refIso ? 'önce (olay anına göre)' : 'önce'
  if (m < 90) return `${Math.round(m)} dk ${suffix}`
  return `${(m / 60).toFixed(1)} sa ${suffix}`
}

/** İki açı (derece) arasındaki 0..360 saat yönlü fark. */
const cw = (from: number, to: number): number => (((to - from) % 360) + 360) % 360

export interface ZoneSector {
  zoneId: string
  name: string
  bearing: number
  start: number
  end: number
}

/** Bölgelerin üsse göre yönlerinden pusula gülü dilimleri: her dilim kendi bölgesinin yönü etrafında, sınırlar komşu yönlerin ORTASINDA
 *  (8 eşit aralıklı bölgede tam 45°'lik dilimler: K = 337.5°–22.5°). Tek bölgede tam daire. */
export function zoneSectors(zones: { zone_id: string; name: string; bearing_from_base_deg?: number }[]): ZoneSector[] {
  const zs = zones.filter((z) => typeof z.bearing_from_base_deg === 'number').sort((a, b) => (a.bearing_from_base_deg as number) - (b.bearing_from_base_deg as number))
  return zs.map((z, i) => {
    const b = z.bearing_from_base_deg as number
    if (zs.length === 1) return { zoneId: z.zone_id, name: z.name, bearing: b, start: b - 180, end: b + 180 }
    const prev = zs[(i - 1 + zs.length) % zs.length].bearing_from_base_deg as number
    const next = zs[(i + 1) % zs.length].bearing_from_base_deg as number
    return { zoneId: z.zone_id, name: z.name, bearing: b, start: b - cw(prev, b) / 2, end: b + cw(b, next) / 2 }
  })
}

/** Üsten `radius` metreye uzanan dilim poligonu (merkez + yay). */
export function sectorPolygon(lat: number, lon: number, start: number, end: number, radius: number, stepDeg = 5): [number, number][] {
  const pts: [number, number][] = [[lat, lon]]
  const n = Math.max(2, Math.ceil((end - start) / stepDeg))
  for (let i = 0; i <= n; i++) pts.push(destination(lat, lon, start + ((end - start) * i) / n, radius))
  return pts
}

/** "Guney Kapisi Yaklasimi" → ["Guney Kapisi", "Yaklasimi"]: harita etiketi için iki dengeli satır. */
export function balanceName(name: string): string[] {
  const words = name.trim().split(/\s+/)
  if (words.length < 2) return [name]
  let best = 1
  let bestDiff = Infinity
  for (let i = 1; i < words.length; i++) {
    const diff = Math.abs(words.slice(0, i).join(' ').length - words.slice(i).join(' ').length)
    if (diff < bestDiff) {
      bestDiff = diff
      best = i
    }
  }
  return [words.slice(0, best).join(' '), words.slice(best).join(' ')]
}
