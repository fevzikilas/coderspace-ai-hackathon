import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  type ChannelState,
  audioReady,
  disableTorch,
  enableTorch,
  flashTorch,
  isAndroid,
  notificationState,
  notify,
  playAlarm,
  requestNotifications,
  torchPossible,
  unlockAudio,
  vibrate,
  vibrationSupported,
} from './alerts'
import LoginModal, { PANEL_PATH, hasSession } from './LoginModal'
import SimMap, { type SimBase, type SimMapVehicle, type SimZone } from './SimMap'
import './simulation.css'

// Karşılama sayfası: canlı backend'e BAĞLI DEĞİL. `scripts/export_simulation_data.py` ile DB'deki önceden hesaplanmış
// sonuçlardan üretilen statik /sim-data/events.json'u oynatır. Her olay: önce "analiz" aşaması (araçlar kutulanır, hareket ve
// raporlar incelenir), sonra risk açıklanır — ekran çerçevesi risk rengine döner, YÜKSEK RİSK'te ses/titreşim/flaş.

type Risk = 'LOW' | 'MEDIUM' | 'HIGH'
interface SimBox { x1: number; y1: number; x2: number; y2: number; label: string; state: 'approaching' | 'other' | 'unknown' }
interface SimEvent {
  image_id: string
  image: string
  width: number
  height: number
  capture_time: string
  zone_id: string | null
  zone: string
  risk_level: Risk
  summary: string
  explanation: string
  explanation_source: 'llm' | 'rule-based'
  explanation_model: string | null
  vehicles: number
  approaching: number
  boxes: SimBox[]
  footprint: [number, number][]
  map_vehicles: SimMapVehicle[]
}
interface SimData { base: SimBase; zones: SimZone[]; events: SimEvent[] }

const DATA_URL = '/sim-data/events.json'
const RISK_TEXT: Record<Risk, string> = { HIGH: 'YÜKSEK RİSK', MEDIUM: 'ORTA RİSK', LOW: 'DÜŞÜK RİSK' }
// Olay başına süre (Normal hız); "Hızlı" 3 kat hızlandırır
const DWELL_MS: Record<Risk, number> = { HIGH: 8000, MEDIUM: 7500, LOW: 7000 }
const SPEEDS = { normal: 1, fast: 3 } as const
type Speed = keyof typeof SPEEDS
// Analiz aşaması adımlarının bitiş anları (olay zamanı, ms); sonuncusundan sonra risk açıklanır
const STEP_END = [900, 1800, 2500, 3200]
const REVEAL_MS = STEP_END[STEP_END.length - 1]
const TICK_MS = 100

const asset = (p: string): string => `/sim-data/${p}`

function stepTexts(ev: SimEvent): { active: string; done: string }[] {
  return [
    { active: 'Görüntüdeki araçlar tespit ediliyor…', done: `${ev.vehicles} araç tespit edildi` },
    {
      active: 'Araçların hareketi inceleniyor…',
      done: ev.approaching > 0 ? `${ev.approaching} araç üsse doğru ilerliyor` : 'Üsse doğru ilerleyen araç yok',
    },
    { active: 'Saha raporları karşılaştırılıyor…', done: 'Saha raporları karşılaştırıldı' },
    { active: 'Risk hesaplanıyor…', done: 'Risk hesaplandı' },
  ]
}

export default function SimulationPage() {
  const [data, setData] = useState<SimData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [idx, setIdx] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState<Speed>('normal')
  const [elapsed, setElapsed] = useState(0)
  const [loop, setLoop] = useState(1)
  const [notif, setNotif] = useState<ChannelState>(notificationState())
  const [torch, setTorch] = useState<ChannelState>(isAndroid() && torchPossible() ? 'off' : 'unsupported')
  const [sound, setSound] = useState(true)
  const [audioOk, setAudioOk] = useState(false)
  const [loginOpen, setLoginOpen] = useState(false)
  const [session, setSession] = useState(false)
  const closeLogin = useCallback(() => setLoginOpen(false), [])

  useEffect(() => {
    document.title = 'Üs Koruma — Canlı Simülasyon'
    fetch(DATA_URL, { cache: 'no-cache' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: SimData) => setData(d))
      .catch((e: Error) => setError(e.message))
    void hasSession().then(setSession)

    // İzinler açılışta istenir. Bazı tarayıcılar (Safari, Firefox) izni ve sesi yalnızca kullanıcı hareketiyle verir:
    // ilk dokunuş/tıklama/tuşta ses kilidi açılır ve hâlâ sorulmamış bildirim izni yeniden istenir.
    if (notificationState() === 'off') void requestNotifications().then(setNotif)
    if (isAndroid() && torchPossible()) void enableTorch().then(setTorch)
    const onFirst = () => {
      unlockAudio()
      window.setTimeout(() => setAudioOk(audioReady()), 250)
      if (notificationState() === 'off') void requestNotifications().then(setNotif)
    }
    window.addEventListener('pointerdown', onFirst, { once: true })
    window.addEventListener('keydown', onFirst, { once: true })
    return () => {
      window.removeEventListener('pointerdown', onFirst)
      window.removeEventListener('keydown', onFirst)
      disableTorch()
    }
  }, [])

  const events = data?.events
  const ev = events?.[idx] ?? null
  const dwell = ev ? DWELL_MS[ev.risk_level] : 7500
  const revealed = elapsed >= REVEAL_MS
  const step = STEP_END.findIndex((t) => elapsed < t) // -1: hepsi bitti

  const go = useCallback(
    (delta: number) => {
      if (!events?.length) return
      setElapsed(0)
      setIdx((i) => {
        const n = i + delta
        if (n >= events.length) {
          setLoop((l) => l + 1)
          return 0
        }
        return n < 0 ? events.length - 1 : n
      })
    },
    [events],
  )
  const jump = (i: number) => {
    setElapsed(0)
    setIdx(i)
  }

  // Oynatma saati: hız yalnızca geçen süreyi ölçekler, duraklatma kaldığı yerden devam eder
  useEffect(() => {
    if (!playing || !ev) return
    const t = window.setInterval(() => setElapsed((e) => e + TICK_MS * SPEEDS[speed]), TICK_MS)
    return () => window.clearInterval(t)
  }, [playing, speed, ev])
  useEffect(() => {
    if (elapsed >= dwell) go(1)
  }, [elapsed, dwell, go])

  // Sonraki görüntüyü önceden yükle
  useEffect(() => {
    const next = events?.[(idx + 1) % (events?.length || 1)]
    if (next) new Image().src = asset(next.image)
  }, [events, idx])

  // Risk açıklandığı an: YÜKSEK RİSK'te ses + titreşim + flaş; sekme arka plandaysa sistem bildirimi (ekrandaki çerçeve görünmez)
  const revealKey = revealed && ev ? `${loop}-${idx}` : null
  useEffect(() => {
    if (!revealKey || !ev || ev.risk_level !== 'HIGH') return
    if (sound) playAlarm()
    vibrate()
    void flashTorch()
    if (document.hidden) void notify(`Yüksek risk — ${ev.zone}`, `Saat ${ev.capture_time}: ${ev.summary}`)
    // yalnızca açıklama anında bir kez (ses tercihi değişince yeniden çalmasın)
  }, [revealKey])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (loginOpen || e.target instanceof HTMLInputElement || e.target instanceof HTMLButtonElement) return
      if (e.code === 'Space') {
        e.preventDefault()
        setPlaying((p) => !p)
      } else if (e.key === 'ArrowRight') go(1)
      else if (e.key === 'ArrowLeft') go(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, loginOpen])

  const tally = useMemo(() => {
    const t: Record<Risk, number> = { HIGH: 0, MEDIUM: 0, LOW: 0 }
    events?.slice(0, revealed ? idx + 1 : idx).forEach((e) => t[e.risk_level]++)
    return t
  }, [events, idx, revealed])

  const toggleSound = () => {
    unlockAudio()
    window.setTimeout(() => setAudioOk(audioReady()), 250)
    setSound((s) => !s)
  }

  if (error) {
    return (
      <div className="sim sim-center">
        <p>Simülasyon verisi yüklenemedi ({error}).</p>
        <p className="muted">Önce <code>python scripts/export_simulation_data.py</code> çalıştırılmalı.</p>
      </div>
    )
  }
  if (!data || !events || !ev) return <div className="sim sim-center muted">Sinyal bekleniyor…</div>

  const level = ev.risk_level.toLowerCase()
  const colored = elapsed >= STEP_END[1] // hareket incelendikten sonra kutular/harita renklenir
  const shownBoxes = revealed || step > 0 ? ev.boxes.length : Math.ceil((ev.boxes.length * elapsed) / STEP_END[0])
  const steps = stepTexts(ev)
  const progress = Math.min(1, elapsed / dwell)

  return (
    <div className={`sim ${revealed ? `risk-${level}` : 'analyzing'}`}>
      <div className={`sim-glow ${revealed ? level : 'scan'}`} key={revealKey ?? `scan-${loop}-${idx}`} aria-hidden />
      {loginOpen && <LoginModal onClose={closeLogin} />}

      <header className="sim-head">
        <div className="sim-brand">
          <span className="sim-live" aria-hidden />
          <div>
            <h1>Üs Koruma · Canlı Sinyal</h1>
            <small>Gerçek 40 olayın önceden hesaplanmış sonuçları, hızlandırılmış oynatım (simülasyon)</small>
          </div>
        </div>
        {session ? (
          <a className="sim-login" href={PANEL_PATH}>Operasyon paneli</a>
        ) : (
          <button className="sim-login" onClick={() => setLoginOpen(true)}>Giriş</button>
        )}
      </header>

      <main className="sim-main">
        <section className="sim-stage" aria-label="Drone görüntüsü">
          <div className="sim-frame" style={{ aspectRatio: `${ev.width} / ${ev.height}` }}>
            <img key={ev.image_id} src={asset(ev.image)} alt={`${ev.zone}, saat ${ev.capture_time} drone görüntüsü`} />
            <svg viewBox={`0 0 ${ev.width} ${ev.height}`} preserveAspectRatio="none" aria-hidden>
              {ev.boxes.slice(0, shownBoxes).map((b, i) => (
                <rect key={i} className={`bx ${colored ? b.state : 'scan'}`} x={b.x1} y={b.y1} width={b.x2 - b.x1} height={b.y2 - b.y1} vectorEffect="non-scaling-stroke" />
              ))}
            </svg>
            {!revealed && <div className="sim-scanline" aria-hidden />}
            <span className="sim-stamp">{ev.capture_time}</span>
          </div>
          <div className="sim-legend" aria-hidden>
            <span><i className="approaching" /> üsse yaklaşan</span>
            <span><i className="other" /> yaklaşmayan</span>
            <span><i className="unknown" /> hareketi bilinmiyor</span>
          </div>
        </section>

        <section className="sim-info" aria-live="polite">
          <div className="sim-meta">
            <span>Saat <b>{ev.capture_time}</b></span>
            <span>{ev.zone}</span>
          </div>

          <div className="sim-result">
            {!revealed ? (
              <div className="sim-analysis">
                <div className="sim-analysis-title">Analiz ediliyor…</div>
                <ol className="sim-steps">
                  {steps.map((s, i) => {
                    const st = step === -1 || i < step ? 'done' : i === step ? 'active' : 'pending'
                    return (
                      <li key={i} className={st}>
                        <span className="dot" aria-hidden />
                        {st === 'done' ? s.done : s.active}
                      </li>
                    )
                  })}
                </ol>
              </div>
            ) : (
              <>
                <div className={`sim-badge ${level}`}>{RISK_TEXT[ev.risk_level]}</div>
                <p className="sim-summary">{ev.summary}</p>
                <div className="sim-explain">
                  <div className="sim-explain-label">
                    {ev.explanation_source === 'llm' ? `Yapay zekâ değerlendirmesi · ${ev.explanation_model}` : 'Sistem değerlendirmesi · kural tabanlı'}
                  </div>
                  <p>{ev.explanation}</p>
                </div>
              </>
            )}
          </div>

          <div className="sim-progress" aria-hidden><div style={{ width: `${progress * 100}%` }} /></div>

          <div className="sim-controls">
            <button className="sim-btn icon" onClick={() => go(-1)} aria-label="Önceki olay">
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden><path fill="currentColor" d="M6 5h2v14H6zM20 5v14L9 12z" /></svg>
            </button>
            <button className="sim-btn play" onClick={() => setPlaying((p) => !p)} aria-label={playing ? 'Duraklat' : 'Oynat'}>
              {playing ? '❚❚ Duraklat' : '▶ Oynat'}
            </button>
            <button className="sim-btn icon" onClick={() => go(1)} aria-label="Sonraki olay">
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden><path fill="currentColor" d="M16 5h2v14h-2zM4 5v14l11-7z" /></svg>
            </button>
            <div className="sim-speed" role="group" aria-label="Hız">
              {(['normal', 'fast'] as const).map((s) => (
                <button key={s} className={`sim-btn ${speed === s ? 'on' : ''}`} aria-pressed={speed === s} onClick={() => setSpeed(s)}>
                  {s === 'normal' ? 'Normal' : 'Hızlı'}
                </button>
              ))}
            </div>
            <span className="sim-count">{idx + 1} / {events.length}{loop > 1 ? ` · tur ${loop}` : ''}</span>
          </div>

          <div className="sim-tally">
            <span className="high">{tally.HIGH} yüksek</span>
            <span className="medium">{tally.MEDIUM} orta</span>
            <span className="low">{tally.LOW} düşük</span>
          </div>

          <div className="sim-alerts">
            <button className={`sim-btn ${sound ? 'on-soft' : ''}`} onClick={toggleSound} aria-pressed={sound}>
              {sound ? 'Ses açık' : 'Ses kapalı'}
            </button>
            <span className={`chip ${notif === 'on' ? 'ok' : ''}`}>Bildirim: {notif === 'on' ? 'açık' : notif === 'unsupported' ? 'yok' : 'izin yok'}</span>
            <span className={`chip ${vibrationSupported() ? 'ok' : ''}`}>Titreşim: {vibrationSupported() ? 'var' : 'yok'}</span>
            {isAndroid() && <span className={`chip ${torch === 'on' ? 'ok' : ''}`}>Flaş: {torch === 'on' ? 'açık' : 'yok'}</span>}
            {sound && !audioOk && <small className="muted">Sesin çalması için ekrana bir kez dokunun.</small>}
          </div>
        </section>

        <section className="sim-mapwrap" aria-label="Harita">
          <SimMap
            id={`${loop}-${idx}`}
            base={data.base}
            zones={data.zones}
            zoneId={ev.zone_id}
            footprint={ev.footprint}
            vehicles={ev.map_vehicles}
            colored={colored}
          />
        </section>
      </main>

      <footer className="sim-feed" aria-label="Olay akışı">
        {events.map((e, i) => (
          <button
            key={e.image_id}
            className={`tick ${e.risk_level.toLowerCase()} ${i < idx || (i === idx && revealed) ? 'past' : 'future'} ${i === idx ? 'now' : ''}`}
            onClick={() => jump(i)}
            title={i < idx || (i === idx && revealed) ? `${e.capture_time} · ${e.zone} · ${RISK_TEXT[e.risk_level]}` : `${e.capture_time} · ${e.zone}`}
            aria-label={`${i + 1}. olay, ${e.capture_time}`}
          />
        ))}
      </footer>
    </div>
  )
}
