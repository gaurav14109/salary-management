import react from '@vitejs/plugin-react'
// vitest/config, not vite: the `test` key below is Vitest's, and vite's own
// defineConfig does not know about it. `tsc -b` rejects the file otherwise, which
// is what `npm run build` discovered the first time anything ran it.
import { defineConfig } from 'vitest/config'

const BFF = 'http://localhost:8001'

export default defineConfig({
  plugins: [react()],
  server: {
    // Everything goes through the BFF. The browser never addresses a service
    // directly, and proxying keeps it all same-origin, so the session cookie is sent
    // without CORS and without credentialed cross-origin requests.
    proxy: {
      '/api': { target: BFF, changeOrigin: true },
      '/auth': { target: BFF, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
