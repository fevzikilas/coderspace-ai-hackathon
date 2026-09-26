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
