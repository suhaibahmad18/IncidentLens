import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Forward API calls to the FastAPI backend during development. changeOrigin stays false so
    // the backend sees Host: localhost:5173, matching the browser's Origin; otherwise its
    // cross-site write protection would (correctly) refuse sign-in and every other POST/DELETE.
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: false },
    },
  },
})
