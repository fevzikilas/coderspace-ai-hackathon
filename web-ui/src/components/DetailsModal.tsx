import { useEffect, useRef } from 'react'
import { zoneLabel } from '../zoneLabels'
import ImageViewer from './ImageViewer'
import IntelPanel from './IntelPanel'
import LogPanel from './LogPanel'
import MovementPanel from './MovementPanel'
import { RiskEvidence, RiskReasoning, RiskToolCalls } from './RiskCard'
import VehicleTabs from './VehicleTabs'
import type { DetailsTab } from './EventSummary'
import type { DashboardState, Vehicle } from '../types'

const TABS: { id: DetailsTab; label: string }[] = [
  { id: 'image', label: 'Görüntü' },
  { id: 'reason', label: 'Gerekçe ve kanıt' },
  { id: 'movement', label: 'Hareket' },
  { id: 'reports', label: 'Saha raporları' },
  { id: 'tools', label: 'Araç çağrıları' },
]

interface Props {
  tab: DetailsTab
  onTab: (t: DetailsTab) => void
  onClose: () => void
  data: DashboardState
  colors: Record<string, string>
  selected: Vehicle | null
  onSelectVehicle: (id: string) => void
}

/** TEKNİK DETAYLAR modalı: ana ekranı kirletmeden tam gerekçe, kanıt dağılımı, araç çağrıları, hareket sayıları, saha raporları. X / Esc / dışına tıklama kapatır. */
export default function DetailsModal({ tab, onTab, onClose, data, colors, selected, onSelectVehicle }: Props) {
  const boxRef = useRef<HTMLDivElement>(null)
  const a = data.assessment
  const run = data.latest_run
  const event = data.event

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    const prev = document.activeElement as HTMLElement | null
    boxRef.current?.focus()
    return () => {
      window.removeEventListener('keydown', onKey)
      prev?.focus?.()
    }
  }, [onClose])

  const nReports = data.reports.length
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabIndex={-1} ref={boxRef}>
        <header className="modal-head">
          <h2 id="modal-title">
            Teknik detaylar
            {event && <span className="muted"> — {event.capture_time} · {zoneLabel(event.zone.name)}{event.image_id ? ` · ${event.image_id}` : ''}</span>}
          </h2>
          <button className="modal-x" onClick={onClose} aria-label="Kapat" title="Kapat (Esc)">✕</button>
        </header>
        <nav className="modal-tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? 'active' : ''} onClick={() => onTab(t.id)}>
              {t.label}
              {t.id === 'reports' && nReports > 0 && <small> {nReports}</small>}
              {t.id === 'tools' && a && <small> {a.tool_calls_log.length}</small>}
            </button>
          ))}
        </nav>
        <div className="modal-body" role="tabpanel">
          {tab === 'image' && (
            <div className="modal-col">
              <ImageViewer result={run?.result ?? null} event={event} colors={colors} selectedId={selected?.vehicle_id ?? null} onSelect={onSelectVehicle} />
              <VehicleTabs vehicles={data.vehicles} colors={colors} selected={selected} onSelect={onSelectVehicle} />
            </div>
          )}
          {tab === 'reason' && (
            <div className="modal-grid">
              <RiskReasoning assessment={a} />
              <RiskEvidence assessment={a} />
            </div>
          )}
          {tab === 'movement' && (
            <div className="modal-col narrow">
              <VehicleTabs vehicles={data.vehicles} colors={colors} selected={selected} onSelect={onSelectVehicle} />
              <MovementPanel base={data.base} event={event} selected={selected} colors={colors} />
            </div>
          )}
          {tab === 'reports' && (
            <IntelPanel
              zoneName={zoneLabel(event?.zone.name ?? data.zone_id)}
              refIso={event?.reference_time ?? null}
              captureTime={event?.capture_time ?? null}
              intel={data.intel}
              reports={data.reports}
              verification={a?.report_verification ?? null}
            />
          )}
          {tab === 'tools' && (
            <div className="modal-grid">
              <RiskToolCalls assessment={a} />
              <div>
                <div className="sub-title">Pipeline / log</div>
                <div className="logwrap"><LogPanel run={run} logs={data.logs} /></div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
