import { useEffect, useRef } from 'react'
import { fmtTime } from '../geo'
import type { LogEntry, PipelineStep, Run } from '../types'

const STEP_LABEL: Record<string, string> = {
  event_context: 'Olay bağlamı',
  drone_context: 'Drone bağlamı',
  detect: 'Tespit (D-fine)',
  georeference: 'Georef + iz eşleştirme',
  tracks: 'İz + hareket',
  pattern: 'Patern',
  assess: 'Risk ajanı',
}

const STEP_ICON: Record<PipelineStep['status'], string> = {
  pending: '○',
  running: '◔',
  succeeded: '●',
  failed: '✕',
  skipped: '–',
}

export default function LogPanel({ run, logs }: { run: Run | null; logs: LogEntry[] }) {
  const boxRef = useRef<HTMLDivElement>(null)
  const last = logs.length ? logs[logs.length - 1].ts + logs.length : ''
  useEffect(() => {
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [last])

  return (
    <div className="logpanel">
      <div className="steps">
        {(run?.steps ?? []).map((s, i) => (
          <div key={s.name} className={`step ${s.status}`} title={s.error ?? s.detail ?? ''}>
            <span className="step-icon">{STEP_ICON[s.status]}</span>
            <span>{i + 1}. {STEP_LABEL[s.name] ?? s.name}</span>
            {s.duration_ms != null && <small>{s.duration_ms.toFixed(0)} ms</small>}
          </div>
        ))}
        {!run && <div className="empty small">Pipeline henüz çalışmadı</div>}
      </div>
      <div className="logbox" ref={boxRef} role="log" aria-live="polite">
        {logs.length === 0 && <div className="empty small">Log yok</div>}
        {logs.map((l, i) => (
          <div key={i} className={`logline ${l.level}`}>
            <time>{fmtTime(l.ts)}</time>
            {l.step && <span className="logstep">{l.step}</span>}
            <span>{l.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
