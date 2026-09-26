import { useState } from 'react'
import { fmtTime } from '../geo'
import { PATTERN_COLOR, PATTERN_LABEL, RISK_COLOR, RISK_LABEL, SOURCE_META, TRUST_LABEL } from '../theme'
import type { Assessment, EvidenceItem, Run, ToolCallLog } from '../types'

export function RiskBadge({ level, size = 'md' }: { level: Assessment['risk_level'] | null; size?: 'md' | 'lg' }) {
  if (!level) return <span className={`risk-badge none size-${size}`}>—</span>
  return (
    <span className={`risk-badge size-${size} ${level === 'HIGH' ? 'pulse' : ''}`} style={{ background: RISK_COLOR[level] }} aria-label={`Risk seviyesi ${level}`}>
      {level} <small>{RISK_LABEL[level]}</small>
    </span>
  )
}

/** Yığılmış çubuk: genişlik = ağırlık, renk = kaynak, çizgili doku = düşük güven. */
function EvidenceBar({ items }: { items: EvidenceItem[] }) {
  return (
    <div className="ev-bar" role="img" aria-label="Kanıt ağırlıkları">
      {items.map((e) => (
        <div
          key={e.source}
          className={`ev-seg ${e.trust === 'low' ? 'hatched' : ''}`}
          style={{ width: `${e.weight * 100}%`, background: SOURCE_META[e.source].color }}
          title={`${SOURCE_META[e.source].label}: ${(e.weight * 100).toFixed(0)}% (${TRUST_LABEL[e.trust]})`}
        >
          {e.weight >= 0.08 ? `${Math.round(e.weight * 100)}%` : ''}
        </div>
      ))}
    </div>
  )
}

function EvidenceList({ items }: { items: EvidenceItem[] }) {
  return (
    <ul className="ev-list">
      {items.map((e) => (
        <li key={e.source} className={e.trust === 'low' ? 'lowtrust' : ''}>
          <span className="ev-dot" style={{ background: SOURCE_META[e.source].color }} />
          <div>
            <div className="ev-head">
              <b>{SOURCE_META[e.source].label}</b>
              <span className="ev-w">{(e.weight * 100).toFixed(0)}%</span>
              <span className={`chip trust-${e.trust}`}>{TRUST_LABEL[e.trust]}</span>
              <span className={`effect ${e.effect}`}>{e.effect === 'raises' ? '▲ artırır' : e.effect === 'lowers' ? '▼ azaltır' : '• nötr'}</span>
            </div>
            <div className="ev-sum">{e.summary}</div>
          </div>
        </li>
      ))}
    </ul>
  )
}

function ToolTimeline({ log }: { log: ToolCallLog[] }) {
  const [open, setOpen] = useState<number | null>(null)
  return (
    <ol className="timeline">
      {log.map((t) => {
        const isOpen = open === t.seq
        return (
          <li key={t.seq} className={`tl-item ${t.status} origin-${t.origin}`}>
            <span className="tl-dot" />
            <button className="tl-btn" onClick={() => setOpen(isOpen ? null : t.seq)} aria-expanded={isOpen}>
              <span className="tl-name">{t.tool}</span>
              <span className={`chip origin-chip ${t.origin}`}>{t.origin === 'llm' ? 'LLM' : t.origin === 'auto' ? 'OTOMATİK' : 'SİSTEM'}</span>
              <span className="tl-ms">{t.duration_ms.toFixed(0)} ms</span>
              <span className="tl-caret">{isOpen ? '▾' : '▸'}</span>
              <span className="tl-sum">{t.result_summary}</span>
            </button>
            {isOpen && (
              <div className="tl-detail">
                <div className="tl-label">argümanlar</div>
                <pre>{JSON.stringify(t.arguments, null, 2)}</pre>
                <div className="tl-label">sonuç</div>
                <pre>{JSON.stringify(t.result, null, 2)}</pre>
                <div className="tl-label">{fmtTime(t.ts)}</div>
              </div>
            )}
          </li>
        )
      })}
    </ol>
  )
}

export default function RiskCard({ assessment, run }: { assessment: Assessment | null; run: Run | null }) {
  if (!assessment) {
    const failed = run?.status === 'failed'
    return (
      <div className="riskcard empty-state">
        <RiskBadge level={null} size="lg" />
        <p>
          {failed
            ? `Son pipeline başarısız: [${run?.error?.step ?? '?'}] ${run?.error?.message ?? ''}`
            : run?.result.message ?? 'Henüz risk değerlendirmesi yok. Bir drone seçip pipeline’ı çalıştırın.'}
        </p>
      </div>
    )
  }
  const a = assessment
  return (
    <div className={`riskcard level-${a.risk_level}`}>
      <div className="rc-head">
        <RiskBadge level={a.risk_level} size="lg" />
        <div className="rc-conf">
          <label>güven</label>
          <div className="conf-track"><div style={{ width: `${a.confidence * 100}%`, background: RISK_COLOR[a.risk_level] }} /></div>
          <b>{Math.round(a.confidence * 100)}%</b>
        </div>
        <span className={`chip mode-${a.mode}`} title={a.fallback_reason ?? ''}>
          {a.mode === 'llm' ? `LLM · ${a.model}` : 'KURAL TABANLI'}
        </span>
      </div>

      {a.capture_time && (
        <div className="asof">
          ⏱ <b>{a.capture_time}</b> itibarıyla değerlendirme <span className="muted">(görüntünün çekim zamanı; bu andan sonraki bilgi kullanılmadı)</span>
        </div>
      )}

      <div className="rc-meta">
        {a.pattern?.matched.map((p) => (
          <span key={p} className="chip" style={{ color: PATTERN_COLOR[p], borderColor: PATTERN_COLOR[p] }}>{PATTERN_LABEL[p]}</span>
        ))}
        {a.pattern && a.pattern.matched.length === 0 && <span className="chip">{PATTERN_LABEL[a.pattern.pattern]}</span>}
        <span className="muted">{a.vehicle_ids.join(', ')} · {a.zone_id} · {fmtTime(a.created_at)}</span>
      </div>

      {a.fallback_reason && <div className="note warn">LLM devre dışı → kural motoru: {a.fallback_reason}</div>}

      <p className="rationale">{a.rationale}</p>

      {a.policy_adjustments.filter((p) => !a.rationale.includes(p)).length > 0 && (
        <ul className="policy">
          {a.policy_adjustments.filter((p) => !a.rationale.includes(p)).map((p, i) => <li key={i}>⚑ {p}</li>)}
        </ul>
      )}

      <div className="sub-title">Kanıt dağılımı (evidence_breakdown)</div>
      <EvidenceBar items={a.evidence_breakdown} />
      <EvidenceList items={a.evidence_breakdown} />
      <div className="ev-legend"><span className="hatch-sample" /> çizgili = düşük güvenli kaynak (ağırlık en çok %20)</div>

      <div className="sub-title">Araç çağrısı zaman çizelgesi ({a.tool_calls_log.length}) <small className="muted">— tıklayınca açılır</small></div>
      <ToolTimeline log={a.tool_calls_log} />
      <div className="rc-foot muted">
        {a.duration_ms.toFixed(0)} ms{a.usage.llm_calls > 0 ? ` · ${a.usage.llm_calls} LLM çağrısı · ${a.usage.total_tokens} token` : ''}
      </div>
    </div>
  )
}
