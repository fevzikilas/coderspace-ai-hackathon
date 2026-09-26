import { ageMin } from '../geo'
import type { IntelItem, ReportItem } from '../types'

interface Props {
  zoneName: string
  /** olay anı (ISO): yaşlar buna göre hesaplanır; yoksa (eski/demo akışı) şimdiye göre */
  refIso: string | null
  captureTime: string | null
  intel: IntelItem[]
  reports: ReportItem[]
}

const SOURCE_LABEL: Record<string, string> = { official: 'RESMÎ', third_party: 'ÜÇÜNCÜ TARAF' }

/** İstihbarat + saha raporları: bilinçli olarak SOLUK/ÇİZGİLİ çizilir (düşük güven, kendi verimizden ayrışsın). */
export default function IntelPanel({ zoneName, refIso, captureTime, intel, reports }: Props) {
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
      {reports.length === 0 && <div className="empty small">Rapor yok</div>}
      {reports.map((r, idx) => {
        const src = r.source ?? r.reporter ?? 'diğer'
        return (
          <article key={`r${idx}`} className={`lowconf-item src-${src}`}>
            <p>{r.text}</p>
            <footer>
              <span className="chip chip-low">{SOURCE_LABEL[src] ?? src.toUpperCase()}</span>
              <span className="chip">doğrulanmamış</span>
              <span>{r.time ?? ''} · {ageMin(r.ts, refIso, r.age_min)}</span>
              {r.distance_m != null && <span>~{r.distance_m >= 1000 ? `${(r.distance_m / 1000).toFixed(1)} km` : `${r.distance_m} m`} olay konumundan</span>}
              {!r.location && <span>genel / konumsuz</span>}
            </footer>
          </article>
        )
      })}
    </div>
  )
}
