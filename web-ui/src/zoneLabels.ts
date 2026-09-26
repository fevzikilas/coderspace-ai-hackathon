// Bölge adlarının GÖRÜNTÜLEME katmanı: veri dosyaları (zones.json) ASCII'ye katlanmış adlar taşır ("Kuzeydogu Kavsagi"); arayüzde Türkçe karakterli gösterilir.
// Veri dosyasına/API'ye dokunulmaz; eşleştirme adın (ya da zone_id'nin) ASCII'ye katlanmış anahtarıyla yapılır, sözlükte olmayan ad OLDUĞU GİBİ gösterilir.
const LABELS: Record<string, string> = {
  // resmî veri (data/REAL/zones.json)
  'kuzey-yolu': 'Kuzey Yolu',
  'kuzeydogu-kavsagi': 'Kuzeydoğu Kavşağı',
  'dogu-yolu': 'Doğu Yolu',
  'guneydogu-yerlesimi': 'Güneydoğu Yerleşimi',
  'guney-kapisi-yaklasimi': 'Güney Kapısı Yaklaşımı',
  'guneybati-yolu': 'Güneybatı Yolu',
  'bati-yerlesimi': 'Batı Yerleşimi',
  'kuzeybati-yolu': 'Kuzeybatı Yolu',
  // sentetik veri (data/synthetic/zones.json) — resmî veriyle ortak adlar yukarıda
  'kuzeydogu-yolu': 'Kuzeydoğu Yolu',
  'guneydogu-yolu': 'Güneydoğu Yolu',
  'guney-yolu': 'Güney Yolu',
  'bati-yolu': 'Batı Yolu',
}

const key = (s: string): string => s.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')

/** Bölge adı ya da zone_id (büyük/küçük harf fark etmez) → Türkçe karakterli görünen ad; bilinmiyorsa girdi aynen döner. */
export function zoneLabel(nameOrId: string | null | undefined): string {
  if (!nameOrId) return '—'
  return LABELS[key(nameOrId)] ?? nameOrId
}
