import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8001', changeOrigin: true },
      '/camera': { target: 'http://localhost:8001', changeOrigin: true },
    },
  },
  resolve: { alias: { buffer: 'buffer/' } },
  define: { global: 'globalThis' },
  optimizeDeps: { include: ['buffer'] },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('react-dom') || id.includes('react-router-dom')) return 'vendor-react'
          if (id.includes('recharts')) return 'vendor-charts'
          if (id.includes('plotly')) return 'vendor-plotly'
          if (id.includes('@xyflow') || id.includes('dagre')) return 'vendor-flow'
        },
      },
    },
  },
})
