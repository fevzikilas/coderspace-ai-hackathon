import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Geliştirmede /api -> gateway. UI yalnızca gateway ile konuşur.
const gateway = process.env.VITE_DEV_GATEWAY ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: gateway, changeOrigin: true } },
  },
})
