// Araç renkleri: her track_id için deterministik bir renk; AYNI renk haritadaki iz+nokta, görüntüdeki kutu kenarı ve araç sekmesinde kullanılır.
//
// Palet KURUMSAL/renk-körü dostu kaynaklardan, DOYGUN tonlarla alınır (pastel/neon yok): Okabe-Ito (gökyüzü, sarı, mavi, mavimsi yeşil) + Tableau 10
// (deniz yeşili, mor) + Tableau 10 klasik (yeşil, pembe-mor, menekşe, zeytin); kırmızı/turuncu/kahve/gri tonları hariç. CIEDE2000 ile "en uzak nokta"
// sırasına dizildi: herhangi bir ÖN EK (ilk k renk) birbirinden olabildiğince ayrışır (4 araçta en yakın çift ΔE00 = 32, 5'te 22, 6'da 19, 7'de 17,
// 8'de 14, 10'da 10). Ayrılmış renklerden uzaktır: risk/üs halkası kırmızısı (#ff453a) ve uyarı halkası amberi (#f5a524) için ΔE00 ≥ 22, "izsiz"
// grisi (#8b98a8) ve seçim beyazı için ≥ 16; koyu harita zemininde (#1b222c) kontrast ≥ 3:1.
export const VEHICLE_PALETTE: readonly string[] = [
  '#2ca02c', // Tableau 10 (klasik) yeşil
  '#e377c2', // Tableau 10 (klasik) pembe-mor
  '#56b4e9', // Okabe-Ito gökyüzü mavisi
  '#f0e442', // Okabe-Ito sarı
  '#0072b2', // Okabe-Ito mavi
  '#76b7b2', // Tableau 10 deniz yeşili
  '#9467bd', // Tableau 10 (klasik) menekşe
  '#009e73', // Okabe-Ito mavimsi yeşil
  '#b07aa1', // Tableau 10 mor
  '#bcbd22', // Tableau 10 (klasik) zeytin
]

/** FNV-1a (32 bit): kararlı, kütüphanesiz özet (yalnızca kümede olmayan kimlikler için yedek renk). */
export function fnv1a(s: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h >>> 0
}

/** Kimlikler SIRALANIR (T0043 < T0091 < …) ve sırayla paletin k. rengini alır: aynı araç kümesi her zaman aynı atamayı verir, ≤ 10 araçta hiçbir iki araç aynı rengi almaz
 *  ve palet sırası gereği en ayrışan renkler önce kullanılır. (Gerçek veride her iz tek bir olayın görüntüsüne aittir, yani renk pratikte iz başına sabittir;
 *  aynı iz farklı bir araç kümesiyle görünürse sırası, dolayısıyla rengi değişebilir.) 10'dan fazla araçta renkler yeniden kullanılır. */
export function assignVehicleColors(ids: readonly string[]): Record<string, string> {
  const uniq = [...new Set(ids)].sort()
  const out: Record<string, string> = {}
  uniq.forEach((id, i) => {
    out[id] = VEHICLE_PALETTE[i % VEHICLE_PALETTE.length]
  })
  return out
}

/** Kümede olmayan bir kimlik için (yalnızca özet, çakışmasız garanti yok). */
export const colorOf = (colors: Record<string, string>, id: string | null | undefined): string =>
  (id && colors[id]) || (id ? VEHICLE_PALETTE[fnv1a(id) % VEHICLE_PALETTE.length] : '#8b98a8')

/** Renk üzerinde okunur yazı rengi (WCAG göreli parlaklık). */
export function inkOn(hex: string): string {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
  const lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
  return lum > 0.32 ? '#0b0f14' : '#ffffff'
}
