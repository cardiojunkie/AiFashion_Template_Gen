import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    allowedHosts: [`.${process.env.GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN || 'app.github.dev'}`],
    proxy: { '/api': { target: process.env.VITE_PROXY_TARGET || 'http://backend:8000', changeOrigin: true } },
  },
  test: { environment: 'jsdom', setupFiles: './src/test/setup.ts' },
})
