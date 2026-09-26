import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchEvents, fetchState, startPipeline } from './api'
import ApiKeyGate from './components/ApiKeyGate'
import DetailsModal from './components/DetailsModal'
import EventPicker from './components/EventPicker'
import EventSummary, { type DetailsTab } from './components/EventSummary'
import ImagePanel from './components/ImagePanel'
import MapPanel from './components/MapPanel'
import TopBar from './components/TopBar'
import { usePolling } from './usePolling'
import { assignVehicleColors } from './vehicleColors'

const DEMO_DRONE = 'DRN-03'
/** Bu genişliğin altında özet kart ve görüntü haritanın ÜSTÜNE değil ALTINA gelir (styles.css'teki kırılım ile aynı). */
const OVERLAY_MIN_WIDTH = 1100
/** Haritanın sağına bindirilen sütunun (özet kart / görüntü) genişliği, px — styles.css'teki --overlay-w ile aynı */
const OVERLAY_WIDTH = 420

function useWide(minWidth: number): boolean {
  const query = `(min-width: ${minWidth}px)`
  const [wide, setWide] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const on = () => setWide(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [query])
  return wide
}

export default function App() {
  const { data, error, unauthorized, lastOk, refresh } = usePolling(fetchState)
  const events = usePolling(fetchEvents, 8000)
  const refreshEvents = events.refresh
  const [selectedEvent, setSelectedEvent] = useState<string | null>(null)
  const [droneId, setDroneId] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  // Teknik ayrıntılar (tam gerekçe, kanıt yüzdeleri, araç çağrıları, hareket sayıları, raporlar) MODAL içinde; null = kapalı
  const [detailsTab, setDetailsTab] = useState<DetailsTab | null>(null)
  const closeDetails = useCallback(() => setDetailsTab(null), [])
  const wide = useWide(OVERLAY_MIN_WIDTH)

  const latest = data?.latest_run ?? null
  const busy = starting || latest?.status === 'queued' || latest?.status === 'running'
  const event = data?.event ?? null
  const runningImageId = busy ? (latest?.request.image_id ?? selectedEvent) : null

  // Varsayılan seçim: çalışan/son koşunun olayı, yoksa listedeki ilk olay
  useEffect(() => {
    if (selectedEvent) return
    const fromRun = latest?.request.image_id
    if (fromRun) setSelectedEvent(fromRun)
    else if (events.data?.events.length) setSelectedEvent(events.data.events[0].image_id)
  }, [selectedEvent, latest?.request.image_id, events.data?.events])

  // Koşu bitince soldaki şeridin risk rozetini beklemeden güncelle (katalog aksi halde 8 sn'de bir yoklanır)
  const finishedRun = latest && (latest.status === 'succeeded' || latest.status === 'failed') ? latest.run_id : null
  useEffect(() => {
    if (finishedRun) refreshEvents()
  }, [finishedRun, refreshEvents])

  // Demo (eski akış): varsayılan drone
  useEffect(() => {
    if (droneId || !data?.drones.length) return
    const pick = data.drones.find((d) => d.id === DEMO_DRONE && d.status !== 'OFFLINE') ?? data.drones.find((d) => d.status === 'ACTIVE') ?? data.drones[0]
    setDroneId(pick.id)
  }, [data?.drones, droneId])

  // Seçili araç yoksa: değerlendirmedeki en riskli/ilk araç, yoksa yaklaşan, yoksa ilk
  const selected = useMemo(() => {
    const vs = data?.vehicles ?? []
    if (selectedId) {
      const hit = vs.find((v) => v.vehicle_id === selectedId)
      if (hit) return hit
    }
    const detected = vs.filter((v) => v.in_latest_detection)
    return detected.find((v) => v.analysis.approaching) ?? detected[0] ?? vs.find((v) => v.analysis.approaching) ?? vs[0] ?? null
  }, [data?.vehicles, selectedId])

  // Araç → renk (sabit, deterministik; ≤10 araçta çakışmasız). Görüntüdeki kutu, haritadaki iz ve araç sekmesi AYNI renk haritasını kullanır.
  const vehicleKey = (data?.vehicles ?? []).map((v) => v.vehicle_id).sort().join('|')
  const colors = useMemo(() => assignVehicleColors(vehicleKey ? vehicleKey.split('|') : []), [vehicleKey])

  const start = useCallback(
    async (body: Parameters<typeof startPipeline>[0]) => {
      setActionError(null)
      setStarting(true)
      try {
        await startPipeline(body)
        setSelectedId(null)
        setDetailsTab(null)
        refresh()
        refreshEvents()
      } catch (e) {
        setActionError(e instanceof Error ? e.message : String(e))
      } finally {
        setStarting(false)
      }
    },
    [refresh, refreshEvents],
  )

  const runEvent = useCallback(
    ({ imageId, imageB64 }: { imageId: string; imageB64?: string }) => {
      setSelectedEvent(imageId)
      return start({ image_id: imageId, ...(imageB64 ? { image_b64: imageB64 } : {}) })
    },
    [start],
  )

  const runDemo = useCallback(() => start({ drone_id: droneId || DEMO_DRONE, reset_demo: true }), [start, droneId])

  if (unauthorized) return <ApiKeyGate onSaved={() => { refresh(); refreshEvents() }} />

  const errorEntries = Object.entries(data?.errors ?? {})
  const mode = data?.mode ?? 'idle'
  const picked = events.data?.events.find((e) => e.image_id === selectedEvent) ?? null

  return (
    <div className="app">
      <TopBar
        mode={mode}
        event={event}
        busy={busy}
        globalRisk={data?.assessment?.risk_level ?? null}
        services={data?.services ?? {}}
        connectionError={error}
        lastOk={lastOk}
        demo={{ available: !!data?.capabilities.demo, drones: data?.drones ?? [], droneId, onDroneChange: setDroneId, onRun: runDemo }}
      />

      {(error || actionError || errorEntries.length > 0) && (
        <div className="banner" role="alert">
          {error && <span>⚠ Gateway’e ulaşılamıyor ({error}) — son bilinen veri gösteriliyor. </span>}
          {actionError && <span>⚠ İstek başarısız: {actionError}. </span>}
          {errorEntries.map(([k, v]) => <span key={k}>⚠ {k}: {v}. </span>)}
        </div>
      )}

      {/* SOL: bölge süzgeci + kronolojik olay şeridi · SAĞ SÜTUN: harita (ana eleman) + üstüne bindirilmiş özet kart, görüntü ve lejand */}
      <div className="workspace">
        <EventPicker
          data={events.data}
          error={events.error}
          selectedId={selectedEvent}
          runningId={runningImageId}
          busy={busy}
          onSelect={setSelectedEvent}
          onRun={runEvent}
        />

        <main className="stage">
          {/* Harita sağ sütunun tamamı; üstüne bindirilir: özet kart (sağ üst), görüntü + kutular (sağ alt), lejand (sol alt, MapPanel içinde) */}
          <div className="stage-map">
            {data ? (
              <MapPanel
                base={data.base}
                drones={data.drones}
                vehicles={data.vehicles}
                detections={latest?.result.detections ?? []}
                event={event}
                selectedId={selected?.vehicle_id ?? null}
                onSelect={setSelectedId}
                focusKey={latest?.status === 'succeeded' && latest.result.detections.length > 0 ? latest.run_id : null}
                focusDroneId={latest?.request.drone_id ?? null}
                colors={colors}
                zones={events.data?.zones ?? []}
                activeZoneId={event?.zone.zone_id ?? null}
                insetRight={wide ? OVERLAY_WIDTH + 20 : 0}
              />
            ) : (
              <div className="empty">Gateway’den veri bekleniyor…</div>
            )}
          </div>
          <EventSummary
            assessment={data?.assessment ?? null}
            run={latest}
            event={event}
            picked={picked}
            busy={busy}
            vehicles={data?.vehicles ?? []}
            colors={colors}
            selected={selected}
            onSelectVehicle={setSelectedId}
            onRun={(imageId) => runEvent({ imageId })}
            onOpenDetails={setDetailsTab}
          />
          <ImagePanel
            result={latest?.result ?? null}
            event={event}
            colors={colors}
            selectedId={selected?.vehicle_id ?? null}
            onSelect={setSelectedId}
            onEnlarge={() => setDetailsTab('image')}
          />
        </main>
      </div>

      {detailsTab && data && (
        <DetailsModal tab={detailsTab} onTab={setDetailsTab} onClose={closeDetails} data={data} colors={colors} selected={selected} onSelectVehicle={setSelectedId} />
      )}
    </div>
  )
}
