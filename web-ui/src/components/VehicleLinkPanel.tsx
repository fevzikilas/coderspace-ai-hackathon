import type { VehicleGraph, VehicleLink } from '../types'
import { findGraphNode, formatGap, linkLabel } from '../vehicleGraph'
import CropThumbnail from './CropThumbnail'

interface Props {
  graph: VehicleGraph
  links: VehicleLink[]
  selectedId: string | null
  onSelect: (linkId: string) => void
}

function distance(value: number | null): string {
  if (value == null) return 'koordinat yok'
  return value >= 1000 ? `${(value / 1000).toFixed(2)} km` : `${Math.round(value)} m`
}

export default function VehicleLinkPanel({ graph, links, selectedId, onSelect }: Props) {
  if (links.length === 0) {
    return (
      <div className="reid-empty">
        <b>Possible Vehicle Matches</b>
        <p>Bu olay için similarity ve feasibility eşiklerini geçen cross-event görsel aday yok.</p>
      </div>
    )
  }
  const selected = links.find((link) => link.link_id === selectedId) ?? links[0]
  const source = findGraphNode(graph, selected.source_event_id, selected.source_track_id)
  const target = findGraphNode(graph, selected.target_event_id, selected.target_track_id)
  const model = Array.isArray(selected.evidence.model) ? selected.evidence.model.filter(Boolean).join(' / ') : selected.evidence.model

  return (
    <div className="reid-layout">
      <aside className="reid-list" aria-label="Possible vehicle matches">
        <div className="reid-guard">Visual candidate evidence. Confirmed identity değildir ve edge'ler transitive değildir.</div>
        {links.map((link) => (
          <button key={link.link_id} className={link.link_id === selected.link_id ? 'active' : ''} onClick={() => onSelect(link.link_id)}>
            <span>{link.source_track_id} → {link.target_track_id}</span>
            <small>{linkLabel(link)}</small>
          </button>
        ))}
      </aside>
      <section className="reid-detail" aria-label="Selected vehicle candidate evidence">
        <header>
          <div>
            <span className="chip reid-chip">Possible match</span>
            <h3>{selected.source_track_id} → {selected.target_track_id}</h3>
          </div>
          <strong>{Math.round(selected.appearance_similarity * 100)}%</strong>
        </header>
        <div className="reid-crops">
          <CropThumbnail crop={selected.evidence.source_crop} label={`${selected.source_event_id} / ${selected.source_track_id}`} />
          <CropThumbnail crop={selected.evidence.target_crop} label={`${selected.target_event_id} / ${selected.target_track_id}`} />
        </div>
        <dl className="reid-metrics">
          <div><dt>Visual similarity</dt><dd>{selected.appearance_similarity.toFixed(3)}</dd></div>
          <div><dt>Temporal gap</dt><dd>{formatGap(selected.temporal_gap_seconds)}</dd></div>
          <div><dt>Spatial displacement</dt><dd>{distance(selected.spatial_distance_m)}</dd></div>
          <div><dt>Implied straight-line speed</dt><dd>{selected.implied_speed_mps == null ? 'hesaplanmadı' : `${selected.implied_speed_mps.toFixed(1)} m/s`}</dd></div>
        </dl>
        <p className="reid-meta">
          {source?.class ?? 'vehicle'} · {source?.timestamp ?? selected.source_event_id} → {target?.class ?? 'vehicle'} · {target?.timestamp ?? selected.target_event_id}
          <br />Model: {model || 'unknown'} · Spatial check: {selected.feasibility.spatial_checked ? 'coarse coordinate guard passed' : 'not available'}
        </p>
      </section>
    </div>
  )
}
