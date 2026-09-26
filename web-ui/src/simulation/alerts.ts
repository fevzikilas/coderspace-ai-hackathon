// Yüksek risk uyarı kanalları (best-effort): tarayıcı bildirimi, titreşim, kamera flaşı.
// Hiçbiri garanti değildir; desteklenmeyen/izin verilmeyen kanal sessizce atlanır, hata fırlatılmaz.

export type ChannelState = 'unsupported' | 'off' | 'denied' | 'on'

export const notificationSupported = (): boolean => typeof window !== 'undefined' && 'Notification' in window && window.isSecureContext

export function notificationState(): ChannelState {
  if (!notificationSupported()) return 'unsupported'
  return Notification.permission === 'granted' ? 'on' : Notification.permission === 'denied' ? 'denied' : 'off'
}

/** İzin ister (kullanıcı hareketi içinde çağrılmalı). */
export async function requestNotifications(): Promise<ChannelState> {
  if (!notificationSupported()) return 'unsupported'
  try {
    await Notification.requestPermission()
  } catch {
    /* eski Safari: callback imzası — sonuç aşağıda okunur */
  }
  return notificationState()
}

/** Bildirim gösterir; gösterilemediyse false (çağıran ekrandaki banner'a güvenir). */
export async function notify(title: string, body: string): Promise<boolean> {
  if (notificationState() !== 'on') return false
  const opts: NotificationOptions & { renotify?: boolean } = { body, tag: 'uskoruma-sim', renotify: true, lang: 'tr' }
  try {
    new Notification(title, opts)
    return true
  } catch {
    // Android Chrome `new Notification` desteklemez: kayıtlı bir service worker varsa onunla dene
    try {
      const reg = await navigator.serviceWorker?.getRegistration()
      if (reg) {
        await reg.showNotification(title, opts)
        return true
      }
    } catch {
      /* yok say */
    }
    return false
  }
}

export const vibrationSupported = (): boolean => typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function'

/** Kısa alarm paterni. iOS Safari'de API yoktur → no-op. */
export function vibrate(): boolean {
  if (!vibrationSupported()) return false
  try {
    return navigator.vibrate([220, 90, 220, 90, 420])
  } catch {
    return false
  }
}

// ---------------------------------------------------------------- flaş (torch) — deneysel
// Standart bir "flaş" API'si yok: yalnızca açık bir arka kamera akışında `torch` kısıtı destekleniyorsa yakılabilir
// (bazı Android + Chrome kombinasyonları). iOS Safari'de desteklenmez. Kamera izni ve güvenli bağlam (HTTPS) gerekir.

type TorchCaps = MediaTrackCapabilities & { torch?: boolean }
type TorchConstraint = MediaTrackConstraintSet & { torch?: boolean }

let torchTrack: MediaStreamTrack | null = null

export const torchPossible = (): boolean => typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && window.isSecureContext

/** Arka kamerayı açar ve torch desteğini yoklar. Destek yoksa akışı kapatır ve 'unsupported' döner. */
export async function enableTorch(): Promise<ChannelState> {
  if (!torchPossible()) return 'unsupported'
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
    const track = stream.getVideoTracks()[0]
    let caps: TorchCaps = {}
    try {
      caps = (track?.getCapabilities?.() ?? {}) as TorchCaps
    } catch {
      /* eski tarayıcı */
    }
    if (!track || !caps.torch) {
      // Bazı Chrome sürümleri yetenekleri ancak ImageCapture üzerinden bildirir
      const IC = (window as unknown as { ImageCapture?: new (t: MediaStreamTrack) => { getPhotoCapabilities(): Promise<{ fillLightMode?: string[] }> } }).ImageCapture
      let viaImageCapture = false
      if (track && IC) {
        try {
          const pc = await new IC(track).getPhotoCapabilities()
          viaImageCapture = (pc.fillLightMode ?? []).includes('flash')
        } catch {
          /* yok say */
        }
      }
      if (!viaImageCapture) {
        stream.getTracks().forEach((t) => t.stop())
        return 'unsupported'
      }
    }
    torchTrack = track
    return 'on'
  } catch (e) {
    return (e as DOMException)?.name === 'NotAllowedError' ? 'denied' : 'unsupported'
  }
}

export function disableTorch(): void {
  try {
    torchTrack?.stop()
  } catch {
    /* yok say */
  }
  torchTrack = null
}

const setTorch = async (on: boolean): Promise<void> => {
  if (!torchTrack || torchTrack.readyState !== 'live') return
  await torchTrack.applyConstraints({ advanced: [{ torch: on } as TorchConstraint] })
}

/** İki kısa flaş; her hata sessizce yutulur. */
export async function flashTorch(): Promise<void> {
  if (!torchTrack) return
  try {
    for (let i = 0; i < 2; i++) {
      await setTorch(true)
      await new Promise((r) => setTimeout(r, 260))
      await setTorch(false)
      await new Promise((r) => setTimeout(r, 180))
    }
  } catch {
    /* destek yok: pas geç */
  }
}

// ---------------------------------------------------------------- ses (WebAudio alarm)
// Tarayıcılar sesi ancak bir kullanıcı hareketinden (dokunma/tıklama/tuş) sonra çalar: ilk harekette unlockAudio() çağrılır.

let audioCtx: AudioContext | null = null

export function unlockAudio(): boolean {
  try {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AC) return false
    audioCtx ??= new AC()
    if (audioCtx.state === 'suspended') void audioCtx.resume()
    return true
  } catch {
    return false
  }
}

export const audioReady = (): boolean => audioCtx?.state === 'running'

/** Yüksek risk alarmı: iki tonlu, üç tekrar (~1.4 s). Kilitliyse sessizce atlanır. */
export function playAlarm(): void {
  const ctx = audioCtx
  if (!ctx || ctx.state !== 'running') return
  try {
    const gain = ctx.createGain()
    gain.gain.value = 0.0001
    gain.connect(ctx.destination)
    const osc = ctx.createOscillator()
    osc.type = 'square'
    osc.connect(gain)
    const t0 = ctx.currentTime + 0.02
    for (let i = 0; i < 6; i++) {
      const t = t0 + i * 0.23
      osc.frequency.setValueAtTime(i % 2 ? 660 : 880, t)
      gain.gain.setValueAtTime(0.0001, t)
      gain.gain.exponentialRampToValueAtTime(0.16, t + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.2)
    }
    osc.start(t0)
    osc.stop(t0 + 6 * 0.23 + 0.05)
    osc.onended = () => gain.disconnect()
  } catch {
    /* yok say */
  }
}

// Flaş yalnızca Android'de denenir: masaüstünde kamera izni istemek (ve web kamerası ışığını yakmak) anlamsız, iOS'ta torch yok.
export const isAndroid = (): boolean => typeof navigator !== 'undefined' && /Android/i.test(navigator.userAgent)
