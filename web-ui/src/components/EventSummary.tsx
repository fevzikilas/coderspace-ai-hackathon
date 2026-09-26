import { RISK_COLOR, RISK_LABEL } from '../theme'
import { plainNotes, plainSummary } from '../summary'
import { inkOn } from '../vehicleColors'
import { zoneLabel } from '../zoneLabels'
import VehicleTabs from './VehicleTabs'
import type { Assessment, CatalogEvent, EventInfo, Run, Vehicle } from '../types'

export type DetailsTab = 'image' | 'reason' | 'movement' | 'reports' | 'tools'

interface Props {
  assessment: Assessment | null
  run: Run | null
  /** ekranda gösterilen (son değerlendirilen) olay */
  event: EventInfo | null
  /** soldaki şeritte seçili olay (henüz değerlendirilmemiş olabilir) */
  picked: CatalogEvent | null
  busy: boolean
  vehicles: Vehicle[]
  colors: Record<string, string>
  selected: Vehicle | null
  onSelectVehicle: (id: string) => void
  onRun: (imageId: string) => void
  onOpenDetails: (tab: DetailsTab) => void
}

/** Büyük, sade risk rozeti: "YÜKSEK RİSK". */
function BigRisk({ level }: { level: Assessment['risk_level'] | null }) {
  if (!level) return <div className="bigrisk none">—</div>
  return (
    <div className={`bigrisk ${level === 'HIGH' ? 'pulse' : ''}`} style={{ background: RISK_COLOR[level], color: inkOn(RISK_COLOR[level]) }} aria-label={`Risk seviyesi ${RISK_LABEL[level]}`}>
      {RISK_LABEL[level]} RİSK
    </div>
  )
}

/** SEÇİLİ OLAY ÖZETİ (haritanın üstünde, görüntünün solunda): büyük risk rozeti, tek cümle, araç sekmeleri. Teknik ayrıntı YOK → "Detayları Gör" modalı. */
export default function EventSummary({ assessment: a, run, event, picked, busy, vehicles, colors, selected, onSelectVehicle, onRun, onOpenDetails }: Props) {
  const shownId = event?.image_id ?? null
  const running = busy && run ? run.request.image_id : null
  const pickedPending = picked && picked.image_id !== shownId && !busy ? picked : null

  const pendingBar = pickedPending && (
    <div className="sum-pending">
      <span>
        Seçilen olay: <b>{pickedPending.capture_time}</b> · {zoneLabel(pickedPending.zone_name)}
        {pickedPending.last_run?.risk_level ? <span className="muted"> · önceki sonuç {RISK_LABEL[pickedPending.last_run.risk_level]}</span> : null}
      </span>
      <button className="btn primary" onClick={() => onRun(pickedPending.image_id)}>▶ Değerlendir</button>
    </div>
  )

  if (busy) {
    return (
      <aside className="summary">
        <div className="sum-running">
          <span className="spinner" aria-hidden />
          <span>{running ? <><b>{picked?.image_id === running ? picked.capture_time : running}</b> olayı değerlendiriliyor…</> : 'Değerlendiriliyor…'}</span>
        </div>
      </aside>
    )
  }

  if (!a || !event) {
    const failed = run?.status === 'failed'
    return (
      <aside className="summary">
        {pendingBar}
        <p className="sum-empty">
          {failed
            ? `Değerlendirme başarısız oldu: ${run?.error?.message ?? 'bilinmeyen hata'}`
            : run?.result.message ?? (pickedPending ? 'Bu olay henüz değerlendirilmedi.' : 'Soldaki listeden bir olay seçin.')}
        </p>
        {failed && <button className="btn details-btn" onClick={() => onOpenDetails('tools')}>Detayları Gör</button>}
      </aside>
    )
  }

  const notes = plainNotes(a)
  return (
    <aside className={`summary level-${a.risk_level}`} aria-label="Seçili olay özeti">
      <div className="sum-body">
      {pendingBar}
      <div className="sum-when">
        <b>{event.capture_time}</b> · {zoneLabel(event.zone.name)}
      </div>
      <div className="sum-main">
        <BigRisk level={a.risk_level} />
        <p className="sum-line">{plainSummary(a, vehicles)}</p>
      </div>
      {notes.length > 0 && (
        <ul className="sum-notes">
          {notes.map((n) => <li key={n}>⚠ {n}</li>)}
        </ul>
      )}
      <VehicleTabs vehicles={vehicles} colors={colors} selected={selected} onSelect={onSelectVehicle} />
      </div>
      <button className="btn details-btn" onClick={() => onOpenDetails('reason')}>Detayları Gör</button>
    </aside>
  )
}
