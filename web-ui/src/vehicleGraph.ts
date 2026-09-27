import type { VehicleGraph, VehicleGraphNode, VehicleLink } from './types'

export function findGraphNode(graph: VehicleGraph, eventId: string, trackId: string): VehicleGraphNode | null {
  return graph.nodes.find((node) => node.event_id === eventId && node.track_id === trackId) ?? null
}

export function formatGap(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} sn`
  if (seconds < 3600) return `${Math.round(seconds / 60)} dk`
  const hours = seconds / 3600
  return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} sa`
}

export function linkLabel(link: VehicleLink): string {
  return `Possible match · ${Math.round(link.appearance_similarity * 100)}% · Δt ${formatGap(link.temporal_gap_seconds)}`
}
