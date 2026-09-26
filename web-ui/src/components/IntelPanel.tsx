import { ageMin } from '../geo'
import type { IntelItem, ReportItem, ReportVerdict, ReportVerification } from '../types'

interface Props {
  zoneName: string
  /** olay anı (ISO): yaşlar buna göre hesaplanır; yoksa (eski/demo akışı) şimdiye göre */
  refIso: string | null
  captureTime: string | null
  intel: IntelItem[]
  reports: ReportItem[]
  /** değerlendirme sonrası: raporların kendi verimizle nicel karşılaştırması */
  verification: ReportVerification | null
}

const SOURCE_LABEL: Record<string, string> = { official: 'RESMÎ', third_party: 'ÜÇÜNCÜ TARAF' }
const VERDICT_LABEL: Record<ReportVerdict, string> = { compatible: 'UYUMLU', incompatible: 'UYUMSUZ', unverifiable: 'DOĞRULANAMADI', irrelevant: 'İLGİSİZ' }
const VERDICT_ORDER: Record<string, number> = { incompatible: 0, compatible: 1, unverifiable: 2, none: 3, irrelevant: 4 }

/** İstihbarat + saha raporları: bilinçli olarak SOLUK/ÇİZGİLİ çizilir (düşük güven, kendi verimizden ayrışsın). */
export default function IntelPanel({ zoneName, refIso, captureTime, intel, reports, verification }: Props) {
  const verdicts = new Map((verification?.items ?? []).map((i) => [i.id, i]))
  // İlgisizler `items`ta yoktur: kimliği eşleşmeyen rapor 'ilgisiz' ya da henüz karşılaştırılmamış demektir
  const verdictOf = (r: ReportItem): ReportVerdict | 'none' => (verification && r.id ? (verdicts.get(r.id)?.verdict ?? 'irrelevant') : 'none')
  const sorted = [...reports].sort((a, b) => VERDICT_ORDER[verdictOf(a)] - VERDICT_ORDER[verdictOf(b)] || (a.time ?? '').localeCompare(b.time ?? ''))
  const shown = sorted.filter((r) => verdictOf(r) !== 'irrelevant')
  const irrelevant = sorted.filter((r) => verdictOf(r) === 'irrelevant')
  const counts = verification?.by_verdict ?? {}
  const renderReport = (r: ReportItem, idx: number) => {
    const src = r.source ?? r.reporter ?? 'diğer'
    const v = verdictOf(r)
    const detail = r.id ? verdicts.get(r.id)?.summary : undefined
    return (
      <article key={`r${idx}`} className={`lowconf-item src-${src}`}>
        <p>{r.text}</p>
        <footer>
          {v !== 'none' ? (
            <span className={`chip verdict v-${v}`} title={detail ?? (v === 'irrelevant' ? 'doğrulanabilir araç iddiası yok' : '')}>{VERDICT_LABEL[v]}</span>
          ) : (
            <span className="chip">doğrulanmamış</span>
          )}
          <span className="chip chip-low">{SOURCE_LABEL[src] ?? src.toUpperCase()}</span>
          <span>{r.time ?? ''} · {ageMin(r.ts, refIso, r.age_min)}</span>
          {r.distance_m != null && <span>~{r.distance_m >= 1000 ? `${(r.distance_m / 1000).toFixed(1)} km` : `${r.distance_m} m`} olay konumundan</span>}
          {!r.location && <span>genel / konumsuz</span>}
        </footer>
        {detail && v !== 'irrelevant' && <p className="verdict-detail">{detail}</p>}
      </article>
    )
  }
  return (
    <div className="intel">
      <div className="lowconf-banner" role="note">
        <b>⚠ DÜŞÜK GÜVEN</b> — doğrulanmamış dış veriler. Karar önceliği kendi tespit ve hareket verimizdedir; bu maddeler yalnızca
        destekleyici bağlamdır. <span className="muted">Bölge: {zoneName}{captureTime ? ` · ${captureTime} itibarıyla` : ''}</span>
      </div>

      <div className="sub-title">İstihbarat ({intel.length})</div>
      {intel.length === 0 && <div className="empty small">Bu bölge için istihbarat kaynağı yok (veri setinde intel dosyası yok).</div>}
      {intel.map((i, idx) => (
        <article key={`i${idx}`} className="lowconf-item">
          <p>{i.text}</p>
          <footer>
            <span className="chip chip-low">güven: {i.confidence.toUpperCase()}</span>
            <span>{i.source}</span>
            <span>{ageMin(i.ts, refIso)}</span>
          </footer>
        </article>
      ))}

      <div className="sub-title">Saha raporları ({reports.length}){captureTime ? ` — yalnızca ${captureTime} ve öncesi` : ''}</div>
      {verification && (
        <div className="verdict-summary" role="note">
          Kendi tespit+iz verimizle karşılaştırma:{' '}
          <span className="chip verdict v-compatible">{counts.compatible ?? 0} uyumlu</span>{' '}
          <span className="chip verdict v-incompatible">{counts.incompatible ?? 0} uyumsuz</span>{' '}
          <span className="chip verdict v-unverifiable">{counts.unverifiable ?? 0} doğrulanamadı</span>{' '}
          <span className="chip verdict v-irrelevant">{counts.irrelevant ?? 0} ilgisiz</span>
          {verification.identity_claims > 0 && <div className="muted small">{verification.identity_claims} rapor kimlik/dostluk iddiası içeriyor — kendi verimizle doğrulanamaz, riski düşürmez.</div>}
        </div>
      )}
      {reports.length === 0 && <div className="empty small">Rapor yok</div>}
      {shown.map(renderReport)}
      {irrelevant.length > 0 && (
        <details className="irrelevant">
          <summary>İlgisiz raporlar ({irrelevant.length}) — hava, haberleşme, plan, geçmiş ihbar…</summary>
          {irrelevant.map(renderReport)}
        </details>
      )}
    </div>
  )
}
