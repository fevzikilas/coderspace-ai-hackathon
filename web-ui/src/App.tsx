import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchEvents, fetchState, startPipeline } from './api'
import ApiKeyGate from './components/ApiKeyGate'
import EventPicker from './components/EventPicker'
import ImageViewer from './components/ImageViewer'
import IntelPanel from './components/IntelPanel'
import LogPanel from './components/LogPanel'
import MapPanel from './components/MapPanel'
import MovementPanel from './components/MovementPanel'
import RiskCard from './components/RiskCard'
import TopBar from './components/TopBar'
import { usePolling } from './usePolling'

const DEMO_DRONE = 'DRN-03'

function Panel({ title, children, className = '', badge }: { title: string; children: React.ReactNode; className?: string; badge?: React.ReactNode }) {
  return (
    <section className={`panel ${className}`}>
      <h2>
        {title}
        {badge}
      </h2>
      <div className="panel-body">{children}</div>
    </section>
  )
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

  const start = useCallback(
    async (body: Parameters<typeof startPipeline>[0]) => {
      setActionError(null)
      setStarting(true)
      try {
        await startPipeline(body)
        setSelectedId(null)
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

  if (unauthorized) return <ApiKeyGate onSaved={refresh} />

  const errorEntries = Object.entries(data?.errors ?? {})
  const mode = data?.mode ?? 'idle'
  const zoneName = event?.zone.name ?? data?.zone_id ?? '—'

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

      <main className="grid">
        <Panel title="Harita" className="p-map">
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
            />
          ) : (
            <div className="empty">Gateway’den veri bekleniyor…</div>
          )}
        </Panel>

        <Panel title="Risk kartı" className="p-risk" badge={latest ? <small className={`run-status ${latest.status}`}>{latest.status}</small> : null}>
          <RiskCard assessment={data?.assessment ?? null} run={latest} />
        </Panel>

        <Panel title="Görüntü — bbox overlay" className="p-image">
          <ImageViewer result={latest?.result ?? null} event={event} vehicles={data?.vehicles ?? []} selectedId={selected?.vehicle_id ?? null} onSelect={setSelectedId} />
        </Panel>

        <Panel title="Hareket" className="p-move">
          {data ? <MovementPanel base={data.base} event={event} vehicles={data.vehicles} selected={selected} onSelect={setSelectedId} /> : <div className="empty">—</div>}
        </Panel>

        <Panel title="Olay seçici" className="p-evt" badge={events.data ? <small className="chip">{events.data.events.length} görüntü</small> : null}>
          <EventPicker
            data={events.data}
            error={events.error}
            selectedId={selectedEvent}
            runningId={runningImageId}
            busy={busy}
            onSelect={setSelectedEvent}
            onRun={runEvent}
          />
        </Panel>

        <Panel title="İstihbarat / saha raporları" className="p-intel" badge={<small className="chip chip-low">düşük güven</small>}>
          <IntelPanel zoneName={zoneName} refIso={event?.reference_time ?? null} captureTime={event?.capture_time ?? null} intel={data?.intel ?? []} reports={data?.reports ?? []} />
        </Panel>

        <Panel title="Pipeline / log" className="p-log">
          <LogPanel run={latest} logs={data?.logs ?? []} />
        </Panel>
      </main>
    </div>
  )
}
