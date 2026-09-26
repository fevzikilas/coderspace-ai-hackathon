import { fmtDist } from './geo'
import type { Assessment, Vehicle } from './types'

// Özet kartın TEK CÜMLELİK, teknik terimsiz açıklaması. Metin ayrıştırması yok: yapılandırılmış alanlardan (araç analizi, patern, veri boşluğu) kurulur.

const byUrgency = (a: Vehicle, b: Vehicle): number =>
  (a.analysis.eta_min ?? 1e9) - (b.analysis.eta_min ?? 1e9) || (a.analysis.distance_to_base_m ?? 1e12) - (b.analysis.distance_to_base_m ?? 1e12)

/** Görüntüdeki (izli) araçlar içinde üsse yaklaşanlar, en acil olan başta. */
export function approachingVehicles(vehicles: Vehicle[]): Vehicle[] {
  return vehicles.filter((v) => v.in_latest_detection && v.analysis.approaching).sort(byUrgency)
}

/** "3 araç üsse yaklaşıyor, en yakını ~0.6 dk içinde üsse varabilir." gibi tek cümle. */
export function plainSummary(a: Assessment | null, vehicles: Vehicle[]): string {
  const inImage = vehicles.filter((v) => v.in_latest_detection)
  const approaching = approachingVehicles(vehicles)
  const matched = a?.pattern?.matched ?? []
  if (approaching.length > 0) {
    const lead = approaching[0].analysis
    const how = matched.includes('CONVOY') && approaching.length > 1 ? 'birlikte (kafile halinde) ' : ''
    const when = lead.eta_min != null ? `~${lead.eta_min.toFixed(1)} dk içinde üsse varabilir` : `üsse ${fmtDist(lead.distance_to_base_m)} uzakta`
    return approaching.length === 1
      ? `1 araç ${how}üsse yaklaşıyor, ${when}.`
      : `${approaching.length} araç ${how}üsse yaklaşıyor, en yakını ${when}.`
  }
  if (inImage.length > 0) {
    const waiting = matched.includes('LOITERING') ? '; bazıları bölgede bekliyor' : ''
    return inImage.length === 1 ? `Görüntüdeki araç üsse yaklaşmıyor${waiting}.` : `Görüntüdeki ${inImage.length} aracın hiçbiri üsse yaklaşmıyor${waiting}.`
  }
  return 'Görüntüde hareketi izlenebilen araç yok.'
}

/** Kısa, sade uyarı notları (hareketi bilinmeyen araçlar, kendi verimizle çelişen raporlar). */
export function plainNotes(a: Assessment | null): string[] {
  if (!a) return []
  const notes: string[] = []
  const untracked = a.data_gaps?.untracked_detections ?? 0
  if (untracked > 0) notes.push(`${untracked} aracın hareketi bilinmiyor (risk düşük sayılmadı)`)
  const incompatible = a.report_verification?.by_verdict.incompatible ?? 0
  if (incompatible > 0) notes.push(`${incompatible} saha raporu gördüklerimizle çelişiyor`)
  return notes
}
