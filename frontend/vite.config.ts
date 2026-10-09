import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // framer-motion reads React context; if the dep optimizer hands it a second
  // prebundled copy of React the context is null and every motion.* component
  // throws "Invalid hook call". Deduping pins one instance for all consumers.
  resolve: {
    dedupe: ['react', 'react-dom'],
  },
  build: {
    rollupOptions: {
      output: {
        // One 960 kB bundle, two thirds of it the SQL editor. Splitting the
        // heavy, rarely-changing dependencies out means a code change ships a
        // small chunk instead of re-downloading CodeMirror, and no single file
        // sits over the 500 kB warning threshold.
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('codemirror') || id.includes('@lezer')) return 'editor'
          if (id.includes('framer-motion') || id.includes('motion-dom') || id.includes('motion-utils')) {
            return 'motion'
          }
          return 'vendor'
        },
      },
    },
  },
})
