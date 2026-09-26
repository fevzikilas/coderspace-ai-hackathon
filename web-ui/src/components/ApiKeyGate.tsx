import { useState } from 'react'
import { setApiKey } from '../api'

/** Gateway 401 döndürünce görünür. Anahtar yalnızca tarayıcının localStorage'ında saklanır (build'e gömülmez). */
export default function ApiKeyGate({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState('')
  return (
    <div className="gate" role="dialog" aria-modal="true" aria-label="API anahtarı">
      <form
        className="gate-box"
        onSubmit={(e) => {
          e.preventDefault()
          setApiKey(value.trim())
          onSaved()
        }}
      >
        <h2>API anahtarı gerekli</h2>
        <p>Gateway kimlik doğrulaması istiyor. Anahtar yalnızca bu tarayıcıda saklanır.</p>
        <input type="password" autoFocus value={value} onChange={(e) => setValue(e.target.value)} placeholder="X-API-Key" />
        <button className="btn primary" type="submit" disabled={!value.trim()}>Kaydet ve bağlan</button>
      </form>
    </div>
  )
}
