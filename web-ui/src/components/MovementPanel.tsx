import { useMemo } from 'react'
import { fmtClock, fmtDist, haversine } from '../geo'
import { PATTERN_COLOR, PATTERN_LABEL } from '../theme'
import { colorOf } from '../vehicleColors'
import type { Base, EventInfo, Vehicle } from '../types'

interface Props {
  base: Base
  event: EventInfo | null
  selected: Vehicle | null
  /** araç renkleri (sekmeler ve harita ile aynı) */
  colors: Record<string, string>
}

/** Üsse mesafe (m) – zaman grafiği: eğim aşağı = yaklaşıyor. */
function DistanceChart({ v, base, color }: { v: Vehicle; base: Base; color: string }) {
  const series = useMemo(
    () => v.trace.map((p) => ({ t: new Date(p.ts).getTime(), d: haversine(p.lat, p.lon, base.lat, base.lon) })),
    [v.trace, base.lat, base.lon],
  )
  if (series.length < 2) return <div className="empty small">Yeterli iz noktası yok</div>
  const W = 320
  const H = 96
  const t0 = series[0].t
  const t1 = series[series.length - 1].t
  const dMax = Math.max(base.radius_m * 1.2, ...series.map((s) => s.d))
  const x = (t: number) => 6 + ((t - t0) / Math.max(1, t1 - t0)) * (W - 12)
  const y = (d: number) => H - 14 - (d / dMax) * (H - 24)
  const pts = series.map((s) => `${x(s.t).toFixed(1)},${y(s.d).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Üsse mesafe grafiği">
      <line x1={6} x2={W - 6} y1={y(base.radius_m)} y2={y(base.radius_m)} stroke="#ff453a" strokeDasharray="4 4" opacity={0.6} />
      <text x={W - 8} y={y(base.radius_m) - 3} textAnchor="end" fontSize={9} fill="#ff453a">üs sınırı</text>
      <polyline points={`${x(t0)},${H - 14} ${pts} ${x(t1)},${H - 14}`} fill={color} opacity={0.12} />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={2} />
      {series.length <= 40 && series.map((sp) => <circle key={sp.t} cx={x(sp.t)} cy={y(sp.d)} r={2} fill={color} />)}
      <circle cx={x(t1)} cy={y(series[series.length - 1].d)} r={3.5} fill={color} />
      <text x={6} y={H - 3} fontSize={9} fill="#7d8b9a">{fmtClock(new Date(t0).toISOString())}</text>
      <text x={W - 6} y={H - 3} textAnchor="end" fontSize={9} fill="#7d8b9a">{fmtClock(new Date(t1).toISOString())}</text>
      <text x={6} y={10} fontSize={9} fill="#7d8b9a">{fmtDist(dMax)}</text>
    </svg>
  )
}

/** Üs (sol) → uyarı halkası (sağ) doğrusal ölçek; araç konumu ve yaklaşma oku. */
function ApproachGauge({ v, base }: { v: Vehicle; base: Base }) {
  const d = v.analysis.distance_to_base_m ?? base.alert_radius_m
  const pct = Math.min(100, (d / base.alert_radius_m) * 100)
  const basePct = (base.radius_m / base.alert_radius_m) * 100
  const approaching = v.analysis.approaching
  return (
    <div className="gauge" aria-label="Yaklaşma göstergesi">
      <div className="gauge-track">
        <div className="gauge-base" style={{ width: `${basePct}%` }} />
        <div className={`gauge-marker ${approaching ? 'approaching' : ''}`} style={{ left: `${pct}%` }}>
          <span>{approaching ? '◀◀' : '●'}</span>
        </div>
      </div>
      <div className="gauge-scale">
        <span>ÜS</span>
        <span>{fmtDist(base.radius_m)} sınır</span>
        <span>{fmtDist(base.alert_radius_m)}</span>
      </div>
      <div className={`approach-state ${approaching ? 'yes' : 'no'}`}>
        {approaching ? `▶ ÜSSE YAKLAŞIYOR${v.analysis.eta_min != null ? ` — tahmini varış ${v.analysis.eta_min.toFixed(1)} dk` : ''}` : 'Üsse yaklaşmıyor'}
      </div>
    </div>
  )
}

export default function MovementPanel({ base, event, selected, colors }: Props) {
  return (
    <div className="movement">
      {!selected ? (
        <div className="empty">Görüntünün altındaki sekmelerden bir araç seçin.</div>
      ) : (
        <>
          <div className="mv-head">
            <i className="vtab-dot" style={{ background: colorOf(colors, selected.vehicle_id) }} />
            <b>{selected.vehicle_id}</b> <span className="muted">{selected.class}</span>
            {selected.pattern && (
              <span className="chip" style={{ color: PATTERN_COLOR[selected.pattern], borderColor: PATTERN_COLOR[selected.pattern] }}>
                {PATTERN_LABEL[selected.pattern]}
              </span>
            )}
            {selected.analysis.stale && <span className="chip chip-warn">bayat veri</span>}
          </div>
          <ApproachGauge v={selected} base={base} />
          <div className="metrics">
            <div><label>Hız</label><b>{(selected.analysis.speed_mps * 3.6).toFixed(0)} km/sa</b><small>{selected.analysis.speed_mps.toFixed(1)} m/s</small></div>
            <div><label>Yön</label><b>{selected.analysis.heading_deg != null ? `${Math.round(selected.analysis.heading_deg)}°` : '—'}</b></div>
            <div><label>Üsse mesafe</label><b>{fmtDist(selected.analysis.distance_to_base_m)}</b></div>
            <div><label>Kapanma hızı</label><b>{selected.analysis.closing_speed_mps.toFixed(1)} m/s</b></div>
          </div>
          <div className="sub-title">Üsse mesafe — {event ? `iz (${event.capture_time}'a kadar, 5 dk adım)` : 'iz (son 30 dk)'}</div>
          <DistanceChart v={selected} base={base} color={colorOf(colors, selected.vehicle_id)} />
        </>
      )}
    </div>
  )
}
