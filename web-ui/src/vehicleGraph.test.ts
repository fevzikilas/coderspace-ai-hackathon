import { describe, expect, it } from 'vitest'
import { findGraphNode, formatGap, linkLabel } from './vehicleGraph'
import type { VehicleGraph, VehicleLink } from './types'

const link: VehicleLink = {
  link_id: 'vl-1',
  source_event_id: 'img-a',
  source_track_id: 'T17',
  target_event_id: 'img-b',
  target_track_id: 'T42',
  relation: 'POSSIBLE_SAME_VEHICLE',
  appearance_similarity: 0.873,
  temporal_gap_seconds: 420,
  spatial_distance_m: 1300,
  implied_speed_mps: 3.1,
  feasibility: { temporal: true, spatial: true, spatial_checked: true },
  evidence: { source_crop: null, target_crop: null, source_quality: null, target_quality: null, model: 'test' },
}

const graph: VehicleGraph = {
  relation_semantics: 'candidate_edges_are_independent_not_identity_clusters',
  nodes: [
    { node_id: 'img-a::T17', event_id: 'img-a', track_id: 'T17', timestamp: '2025-06-01T10:00:00Z', position: { lat: 1, lon: 2 }, class: 'car', crop: null, crop_quality: null },
  ],
  edges: [link],
}

describe('vehicle graph presentation helpers', () => {
  it('finds nodes by event and track without treating track id as global identity', () => {
    expect(findGraphNode(graph, 'img-a', 'T17')?.node_id).toBe('img-a::T17')
    expect(findGraphNode(graph, 'img-b', 'T17')).toBeNull()
  })

  it('formats candidate evidence with neutral language', () => {
    expect(formatGap(420)).toBe('7 dk')
    expect(linkLabel(link)).toBe('Possible match · 87% · Δt 7 dk')
  })
})
