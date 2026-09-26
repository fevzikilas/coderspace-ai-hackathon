import { fmtDist } from '../geo'
import { colorOf, inkOn } from '../vehicleColors'
import type { Vehicle } from '../types'

interface Props {
  vehicles: Vehicle[]
  colors: Record<string, string>
  selected: Vehicle | null
  onSelect: (id: string) => void
}

/** Araç seçici sekmeler: her araç KENDİ rengiyle (görüntüdeki kutu ve haritadaki izle aynı). Altında seçili aracın tek satırlık özeti. */
export default function VehicleTabs({ vehicles, colors, selected, onSelect }: Props) {
  if (vehicles.length === 0) return null
  const ordered = [...vehicles].sort((a, b) => Number(b.in_latest_detection) - Number(a.in_latest_detection) || a.vehicle_id.localeCompare(b.vehicle_id))
  const a = selected?.analysis
  return (
    <div className="vtabs">
      <div className="vtab-row" role="tablist" aria-label="Araçlar">
        {ordered.map((v) => {
          const color = colorOf(colors, v.vehicle_id)
          const active = selected?.vehicle_id === v.vehicle_id
          return (
            <button
              key={v.vehicle_id}
              role="tab"
              aria-selected={active}
              className={`vtab ${active ? 'active' : ''}`}
              style={{ borderColor: color, background: active ? color : `${color}1f`, color: active ? inkOn(color) : 'var(--text)' }}
              onClick={() => onSelect(v.vehicle_id)}
              title={`${v.vehicle_id} · ${v.class}${v.analysis.approaching ? ' · üsse yaklaşıyor' : ''}`}
            >
              <i className="vtab-dot" style={{ background: color }} />
              {v.vehicle_id}
              <small>{v.class}</small>
              {v.analysis.approaching && <span className="vtab-warn" aria-label="üsse yaklaşıyor">▶</span>}
            </button>
          )
        })}
      </div>
      {selected && a && (
        <div className="vtab-line">
          <b style={{ color: colorOf(colors, selected.vehicle_id) }}>{selected.vehicle_id}</b>
          <span>{(a.speed_mps * 3.6).toFixed(0)} km/sa</span>
          <span>üsse {fmtDist(a.distance_to_base_m)}</span>
          {a.approaching ? <span className="vtab-yes">▶ yaklaşıyor{a.eta_min != null ? ` · ETA ${a.eta_min.toFixed(1)} dk` : ''}</span> : <span className="muted">yaklaşmıyor</span>}
        </div>
      )}
    </div>
  )
}
