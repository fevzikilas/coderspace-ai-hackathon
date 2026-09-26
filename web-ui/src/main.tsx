import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import 'leaflet/dist/leaflet.css'
import './styles.css'
import App from './App'

// Yönlendirme (auth-proxy ile birlikte):
//   /panel          → operasyonel panel (giriş gerekli; auth-proxy korur)
//   / , /simulation → simülasyon = karşılama sayfası (herkese açık; canlı backend'e bağlı değil, ayrı paket)
const SimulationPage = lazy(() => import('./simulation/SimulationPage'))
const isPanel = /^\/panel\/?$/.test(window.location.pathname)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isPanel ? (
      <App />
    ) : (
      <Suspense fallback={null}>
        <SimulationPage />
      </Suspense>
    )}
  </StrictMode>,
)
