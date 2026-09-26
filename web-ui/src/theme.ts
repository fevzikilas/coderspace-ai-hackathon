import type { EvidenceItem, PatternName, Risk, Trust } from './types'

export const RISK_COLOR: Record<Risk, string> = { LOW: '#2ecc71', MEDIUM: '#f5a524', HIGH: '#ff453a' }
export const RISK_LABEL: Record<Risk, string> = { LOW: 'DÜŞÜK', MEDIUM: 'ORTA', HIGH: 'YÜKSEK' }

export const PATTERN_COLOR: Record<PatternName, string> = {
  CONVOY: '#ff453a',
  DIRECT_APPROACH: '#ff8a3d',
  LOITERING: '#b388ff',
  RANDOM: '#8b98a8',
}
export const PATTERN_LABEL: Record<PatternName, string> = {
  CONVOY: 'KAFİLE',
  DIRECT_APPROACH: 'DOĞRUDAN YAKLAŞMA',
  LOITERING: 'BEKLEME',
  RANDOM: 'RASTGELE',
}

export const SOURCE_META: Record<EvidenceItem['source'], { label: string; color: string }> = {
  movement: { label: 'Hareket', color: '#4cc9f0' },
  pattern: { label: 'Patern', color: '#a78bfa' },
  detection: { label: 'Tespit', color: '#34d399' },
  drone: { label: 'Drone', color: '#94a3b8' },
  intel: { label: 'İstihbarat', color: '#f59e0b' },
  reports: { label: 'Saha raporu', color: '#fb923c' },
}

export const TRUST_LABEL: Record<Trust, string> = { high: 'yüksek güven', medium: 'orta güven', low: 'DÜŞÜK GÜVEN' }

export const DRONE_STATUS_COLOR: Record<string, string> = {
  ACTIVE: '#4cc9f0',
  STANDBY: '#94a3b8',
  RTB: '#f5a524',
  OFFLINE: '#5b6675',
}

export const TILE_URL: string =
  (import.meta.env.VITE_TILE_URL as string | undefined) ?? 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
