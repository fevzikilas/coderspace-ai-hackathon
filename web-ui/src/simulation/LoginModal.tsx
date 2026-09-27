import { type FormEvent, useEffect, useRef, useState } from 'react'

// Simülasyonun üstünde açılan giriş penceresi: auth-svc'nin JSON girişini (POST /auth/login) kullanır; oturum çerezi
// (HttpOnly) sunucu tarafından yazılır. Başarılıysa operasyon paneline (/panel) geçilir. Arkadaki demo akışı durmaz.

export const PANEL_PATH = '/panel'

/** Geçerli oturum var mı (auth-proxy → auth-svc /auth/verify: 204/401). Ağ/dev ortamı hatasında false. */
export async function hasSession(): Promise<boolean> {
  try {
    const r = await fetch('/auth/verify', { credentials: 'same-origin', cache: 'no-store' })
    return r.status === 204
  } catch {
    return false
  }
}

export default function LoginModal({ onClose }: { onClose: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const userRef = useRef<HTMLInputElement>(null)
  // Arkadaki simülasyon her 100 ms'de yeniden çizilir ve onClose her seferinde yeni bir fonksiyon olur: effect'i ona bağlamak
  // odağı her çizimde kullanıcı adı kutusuna geri çekiyordu (şifre yazılamıyordu). Odak yalnızca açılışta; onClose ref'ten okunur.
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    userRef.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onCloseRef.current()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await fetch('/auth/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ username, password, next: PANEL_PATH }),
      })
      if (r.ok) {
        window.location.assign(PANEL_PATH)
        return
      }
      setError(
        r.status === 401
          ? 'Kullanıcı adı veya şifre hatalı.'
          : r.status === 429
            ? 'Çok fazla deneme yapıldı, bir dakika sonra tekrar deneyin.'
            : `Giriş servisi yanıt vermedi (HTTP ${r.status}).`,
      )
    } catch {
      setError('Giriş servisine ulaşılamadı.')
    }
    setPassword('')
    setBusy(false)
  }

  return (
    <div className="sim-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sim-modal" role="dialog" aria-modal="true" aria-labelledby="sim-login-title">
        <div className="sim-modal-head">
          <h2 id="sim-login-title">Operasyon paneline giriş</h2>
          <button className="sim-modal-x" onClick={onClose} aria-label="Kapat">×</button>
        </div>
        <p className="muted">Yetkili personel içindir. Simülasyon arkada oynamaya devam eder.</p>
        <form onSubmit={submit}>
          <label htmlFor="sim-u">Kullanıcı adı</label>
          <input id="sim-u" ref={userRef} name="username" autoComplete="username" autoCapitalize="none" spellCheck={false}
            value={username} onChange={(e) => setUsername(e.target.value)} required />
          <label htmlFor="sim-p">Şifre</label>
          <input id="sim-p" name="password" type="password" autoComplete="current-password"
            value={password} onChange={(e) => setPassword(e.target.value)} required />
          {error && <div className="sim-modal-err" role="alert">{error}</div>}
          <button type="submit" className="sim-btn play" disabled={busy}>{busy ? 'Giriş yapılıyor…' : 'Giriş yap'}</button>
        </form>
      </div>
    </div>
  )
}
