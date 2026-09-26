// Risk kartındaki KISA gerekçe: cümle sınırında kesilir (cümle ortasında "…" YOK). Gerekçenin tamamı "gerekçenin tamamını oku" ile açılır.
// Ajanın gerekçenin SONUNA eklediği "(Veri boşluğu: …)" / "(Politika düzeltmesi: …)" dipnotları kısa görünümden çıkarılır: veri boşluğu zaten
// kart üstündeki rozette görünür; tam metinde ve teknik detaylarda durur.
const NOTE_PREFIX = /^(Veri boşluğu|Politika düzeltmesi):/
const UPPER_OR_OPEN = /[A-ZÇĞİÖŞÜ0-9("'‘“]/

export interface ShortRationale {
  short: string
  /** kısa görünümde gösterilmeyen cümle ya da dipnot var mı */
  hasMore: boolean
}

/** Sondaki "(Veri boşluğu: …)" gibi dipnotları (iç içe parantezleri sayarak) ayırır. */
export function splitTrailingNotes(text: string): { body: string; notes: string[] } {
  let body = text.trimEnd()
  const notes: string[] = []
  while (body.endsWith(')')) {
    let depth = 0
    let i = body.length - 1
    for (; i >= 0; i--) {
      if (body[i] === ')') depth++
      else if (body[i] === '(' && --depth === 0) break
    }
    if (i < 0) break
    const inner = body.slice(i + 1, -1)
    if (!NOTE_PREFIX.test(inner)) break
    notes.unshift(inner)
    body = body.slice(0, i).trimEnd()
  }
  return { body, notes }
}

/** Cümlelere böler: ". " / "! " / "? " sonrası büyük harf, rakam ya da parantez geliyorsa cümle sonudur ("3.5 km", "ETA 11.2 dk" bölünmez). */
export function splitSentences(text: string): string[] {
  const out: string[] = []
  let start = 0
  const re = /[.!?]+\s+/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    const next = text[m.index + m[0].length]
    if (next && UPPER_OR_OPEN.test(next)) {
      out.push(text.slice(start, m.index + m[0].trimEnd().length))
      start = m.index + m[0].length
    }
  }
  if (start < text.length) out.push(text.slice(start))
  return out.map((s) => s.trim()).filter(Boolean)
}

/** İlk cümle her zaman; sonrakiler toplam `maxChars`'ı aşmadıkça ve en çok `maxSentences` cümleye kadar eklenir. */
export function shortRationale(text: string, maxChars = 340, maxSentences = 3): ShortRationale {
  const { body, notes } = splitTrailingNotes(text)
  const sentences = splitSentences(body)
  if (sentences.length === 0) return { short: text, hasMore: false }
  let n = 1
  let len = sentences[0].length
  while (n < sentences.length && n < maxSentences && len + 1 + sentences[n].length <= maxChars) {
    len += 1 + sentences[n].length
    n++
  }
  return { short: sentences.slice(0, n).join(' '), hasMore: n < sentences.length || notes.length > 0 }
}

/** "nvidia/nemotron-3-ultra-550b-a55b:free" → "nemotron-3-ultra" (rozette sığsın; tam ad ipucunda). Sağlayıcı öneki, ":free" ve parametre boyutu eki atılır. */
export const shortModelName = (model: string | null | undefined): string =>
  (model ?? '').split('/').pop()?.replace(/:free$/, '').replace(/-\d+(\.\d+)?b(-a\d+(\.\d+)?b)?$/i, '') ?? ''
