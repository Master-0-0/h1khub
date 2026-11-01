import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // Brand assets (app icon, favicon) live in ../assets so the desktop entry and
  // the web build share a single copy.
  publicDir: '../assets',
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:38110', changeOrigin: true },
      '/rpc': { target: 'ws://127.0.0.1:38110', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    chunkSizeWarningLimit: 2000,
  },
})
