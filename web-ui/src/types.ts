// Gateway sözleşmesi (GET /dashboard/state, POST /pipeline/run). Alanlar backend ile birebir.

export type Risk = 'LOW' | 'MEDIUM' | 'HIGH'
export type Trust = 'high' | 'medium' | 'low'
export type PatternName = 'CONVOY' | 'DIRECT_APPROACH' | 'LOITERING' | 'RANDOM'
export type StepStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'

export interface Base {
  name: string
  lat: number
  lon: number
  radius_m: number
  alert_radius_m: number
}

export interface Drone {
  id: string
  callsign?: string
  lat: number
  lon: number
  heading: number
  fov: number
  status: string
  mode?: string
  alt?: number
  gimbal_pitch?: number
  battery_pct?: number
}

export interface TracePoint {
  lat: number
  lon: number
  ts: string
}

export interface AnalysisBrief {
  speed_mps: number
  heading_deg: number | null
  approaching: boolean
  eta_min: number | null
  distance_to_base_m: number | null
  closing_speed_mps: number
  stale: boolean
}

export interface Vehicle {
  vehicle_id: string
  class: string
  demo: boolean
  last_seen: string
  last_position: { lat: number; lon: number }
  n_points: number
  trace: TracePoint[]
  analysis: AnalysisBrief
  pattern: PatternName | null
  matched_patterns: PatternName[]
  in_latest_detection: boolean
  risk_level: Risk | null
}

export interface IntelItem {
  text: string
  source: string
  ts: string
  confidence: 'low'
}

export interface ReportItem {
  /** mock-data-svc rapor kimliği (assessment.report_verification.items[].id ile eşleşir) */
  id?: string
  text: string
  reporter?: string
  /** official (resmî) | third_party (üçüncü taraf) | ... */
  source?: string
  ts: string
  /** dosyadaki ham saat, örn. "13:05" */
  time?: string
  /** olay anına (capture_time) göre yaş, dakika */
  age_min?: number | null
  distance_m?: number | null
  location?: { lat: number; lon: number } | null
}

export interface EvidenceItem {
  source: 'detection' | 'movement' | 'pattern' | 'drone' | 'intel' | 'reports'
  weight: number
  summary: string
  trust: Trust
  effect: 'raises' | 'neutral' | 'lowers'
}

export interface ToolCallLog {
  seq: number
  tool: string
  arguments: Record<string, unknown>
  status: 'ok' | 'error'
  duration_ms: number
  result_summary: string
  result: unknown
  origin: 'llm' | 'auto' | 'system'
  ts: string
}

export type ReportVerdict = 'compatible' | 'incompatible' | 'unverifiable' | 'irrelevant'

/** Raporların KENDİ tespit+iz verimizle nicel karşılaştırması (risk-agent → core-svc /verify-claims) */
export interface ReportVerification {
  total: number
  by_verdict: Partial<Record<ReportVerdict, number>>
  by_source: Record<string, Partial<Record<ReportVerdict, number>>>
  irrelevant_reasons: Record<string, number>
  identity_claims: number
  identity_claims_describing_an_approaching_vehicle: number
  /** ilgisizler hariç; uyumsuz → uyumlu → doğrulanamadı */
  items: { id: string; time?: string; source?: string; text: string; verdict: ReportVerdict; summary: string }[]
}

export interface Assessment {
  assessment_id: string
  zone_id: string
  detection_id: string
  drone_id: string | null
  vehicle_ids: string[]
  risk_level: Risk
  confidence: number
  rationale: string
  evidence_breakdown: EvidenceItem[]
  tool_calls_log: ToolCallLog[]
  mode: 'llm' | 'rule-based'
  model: string | null
  fallback_reason: string | null
  own_data_level: Risk
  own_data_reasons: string[]
  policy_adjustments: string[]
  /** değerlendirme anı (görüntünün capture_time'ı) */
  reference_time: string | null
  capture_time: string | null
  pattern: { pattern: PatternName; confidence: number; matched: PatternName[]; involved_vehicles: string[] } | null
  report_verification?: ReportVerification | null
  /** risk hesabına katılamayan nesneler: izsiz tespitler (hareket verisi YOK) ve analiz sınırını aşan araçlar */
  data_gaps?: { untracked_detections: number; untracked_classes: Record<string, number>; vehicles_over_limit: string[]; note: string } | null
  usage: { total_tokens: number; llm_calls: number }
  duration_ms: number
  created_at: string
  cached: boolean
}

export interface PipelineStep {
  name: string
  status: StepStatus
  started_at: string | null
  duration_ms: number | null
  detail: string | null
  error: string | null
}

export interface BBox {
  x1: number
  y1: number
  x2: number
  y2: number
}

export interface DetectionObj {
  vehicle_id: string | null
  class: string
  conf: number
  lat: number
  lon: number
  box_index: number
  bbox: BBox
  /** olay akışı: eşleşen iz noktasına uzaklık (m); null = izsiz nesne */
  match_distance_m?: number | null
  /** 'exact': time == capture_time satırı (resmî yol) | 'interpolated': ızgara dışı yedek yol | null: iz bulunamadı */
  match_method?: 'exact' | 'interpolated' | null
}

export type CornerName = 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'

export interface EventInfo {
  image_id: string | null
  /** görüntünün çekildiği an, ham ("14:10") */
  capture_time: string
  reference_time: string | null
  center: { lat: number; lon: number }
  footprint_m: { width: number; height: number } | null
  width_px: number | null
  height_px: number | null
  corner_coordinates: Record<CornerName, [number, number]>
  zone: { zone_id: string; name: string; distance_m: number | null }
  base: { lat: number; lon: number; radius_m: number; name?: string; alert_radius_m?: number }
}

export interface CatalogEvent {
  image_id: string
  capture_time: string
  capture_iso: string
  width_px: number
  height_px: number
  center: { lat: number; lon: number }
  footprint_m: { width: number; height: number }
  zone_id: string | null
  zone_name: string | null
  last_run: RunSummary | null
}

/** zones.json bölgesi: üssün etrafındaki yol/sektör merkezi (pusula yönü `bearing_from_base_deg`) */
export interface ZoneInfo {
  zone_id: string
  name: string
  center?: { lat: number; lon: number }
  distance_from_base_m?: number
  bearing_from_base_deg?: number
}

export interface EventsResponse {
  dataset_date: string | null
  errors: string[]
  n_tracks: number
  base: Base | null
  zones: ZoneInfo[]
  events: CatalogEvent[]
}

export interface RunResult {
  event: EventInfo | null
  drone: Drone | null
  image: { image_id: string; url: string | null; width: number; height: number; mode: string; backend?: string | null; fallback_reason?: string | null; timestamp: string } | null
  detection_id: string | null
  detections: DetectionObj[]
  boxes?: unknown[]
  vehicles: Record<string, unknown>
  pattern: { pattern: PatternName; confidence: number; involved_vehicles: string[] } | null
  assessment: Assessment | null
  message: string | null
}

export interface Run {
  run_id: string
  mode: 'event' | 'drone'
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  created_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  request: { image_id: string | null; drone_id: string | null; zone_id: string | null; has_image: boolean; reset_demo: boolean }
  steps: PipelineStep[]
  result: RunResult
  error: { step: string | null; message: string } | null
}

export interface RunSummary {
  run_id: string
  mode: Run['mode'] | null
  status: Run['status']
  created_at: string
  duration_ms: number | null
  image_id: string | null
  capture_time: string | null
  drone_id: string | null
  zone_id: string | null
  risk_level: Risk | null
  pattern: PatternName | null
}

export interface LogEntry {
  ts: string
  level: 'info' | 'warn' | 'error'
  run_id: string | null
  step: string | null
  message: string
}

export interface ServiceHealth {
  status: 'up' | 'down' | 'degraded'
  latency_ms: number | null
  info?: Record<string, unknown>
  error?: string
}

export interface DashboardState {
  server_time: string
  /** event: son koşu bir olay (capture_time bazlı) · drone: eski/demo (canlı) · idle: henüz koşu yok */
  mode: 'event' | 'drone' | 'idle'
  event: EventInfo | null
  capabilities: { demo: boolean; dataset: { images: number; tracks: number; errors: string[] } | null }
  zone_id: string
  base: Base
  drones: Drone[]
  vehicles: Vehicle[]
  intel: IntelItem[]
  reports: ReportItem[]
  latest_run: Run | null
  assessment: Assessment | null
  runs: RunSummary[]
  logs: LogEntry[]
  sweep_at: string | null
  services: Record<string, ServiceHealth>
  errors: Record<string, string>
}

export interface PipelineRequest {
  /** olay akışı: veri setindeki görüntü kimliği */
  image_id?: string
  /** yüklenen görüntü (data URL/base64); image_id ile birlikte verilir */
  image_b64?: string
  /** eski/demo akışı */
  drone_id?: string
  reset_demo?: boolean
}
