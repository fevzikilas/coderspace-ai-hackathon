import { RiskBadge } from './RiskCard'
import { fmtDist } from '../geo'
import { zoneLabel } from '../zoneLabels'
import type { DashboardState, Drone, EventInfo, Risk, ServiceHealth } from '../types'

const SERVICE_LABEL: Record<string, string> = {
  detection: 'detection',
  core: 'core',
  pattern: 'pattern',
  mock: 'mock-data',
  risk: 'risk-agent',
}

interface Props {
  mode: DashboardState['mode']
  event: EventInfo | null
  busy: boolean
  globalRisk: Risk | null
  services: Record<string, ServiceHealth>
  connectionError: string | null
  lastOk: number | null
  /** eski/demo akışı (yalnızca core-svc DEMO_MODE=true iken) */
  demo: { available: boolean; drones: Drone[]; droneId: string; onDroneChange: (id: string) => void; onRun: () => void }
}

/** Değerlendirme anı başlığı: görüntünün capture_time'ı açıkça görünür. */
function EventChip({ mode, event }: { mode: Props['mode']; event: EventInfo | null }) {
  if (mode === 'event' && event) {
    return (
      <div className="event-chip" aria-live="polite">
        <span className="ec-time">{event.capture_time}</span>
        <span className="ec-label">itibarıyla<br />değerlendirme</span>
        <span className="ec-meta">
          <b>{event.image_id ?? 'yüklenen görüntü'}</b> · {zoneLabel(event.zone.name)}
          <br />
          {event.base.name ?? 'Üs'} · üs sınırı {fmtDist(event.base.radius_m)}
        </span>
      </div>
    )
  }
  if (mode === 'drone') {
    return (
      <div className="event-chip demo">
        <span className="ec-time">CANLI</span>
        <span className="ec-label">demo<br />akışı</span>
        <span className="ec-meta">Zaman = şimdi (DEMO_MODE)</span>
      </div>
    )
  }
  return (
    <div className="event-chip idle">
      <span className="ec-label">Değerlendirilecek bir olay seçin →</span>
    </div>
  )
}

export default function TopBar({ mode, event, busy, globalRisk, services, connectionError, lastOk, demo }: Props) {
  return (
    <header className={`topbar ${globalRisk === 'HIGH' && mode !== 'idle' ? 'alarm' : ''}`}>
      <div className="brand">
        <span className="brand-mark">◈</span>
        <div>
          <h1>ÜS KORUMA</h1>
          <small>Tehlike Alarm Sistemi</small>
        </div>
      </div>

      <EventChip mode={mode} event={event} />

      <div className="global-risk">
        <label>Genel durum</label>
        <RiskBadge level={mode === 'idle' ? null : globalRisk} />
      </div>

      {demo.available && (
        <div className="controls">
          <label className="field">
            <span>Demo drone</span>
            <select value={demo.droneId} onChange={(e) => demo.onDroneChange(e.target.value)} disabled={busy}>
              {demo.drones.map((d) => (
                <option key={d.id} value={d.id} disabled={d.status === 'OFFLINE'}>
                  {d.id} · {d.callsign ?? ''} · {d.status}
                </option>
              ))}
            </select>
          </label>
          <button className="btn primary" onClick={demo.onRun} disabled={busy} title="DEMO_MODE: DRN-03 üzerinde kafile demosu (izleri şimdiye çapalar)">
            ⚡ Demo: Kafile
          </button>
        </div>
      )}

      <div className="health" title="Servis sağlığı (gateway üzerinden)">
        {Object.entries(services).map(([name, h]) => (
          <span key={name} className={`hdot ${h.status}`} title={`${name}: ${h.status}${h.latency_ms != null ? ` (${h.latency_ms} ms)` : ''}${h.error ? ' — ' + h.error : ''}${h.info?.fallback_reason ? ' — ' + String(h.info.fallback_reason) : ''}`}>
            {SERVICE_LABEL[name] ?? name}
          </span>
        ))}
        <span className={`conn ${connectionError ? 'bad' : 'ok'}`}>{connectionError ? '⚠ gateway yok' : lastOk ? '● canlı' : '…'}</span>
      </div>
    </header>
  )
}
