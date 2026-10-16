import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Desktop packaging serves built assets from FastAPI /app/.
  // Existing local Vite development continues to serve from /.
  base: process.env.LLM_COUNCIL_DESKTOP_BUILD ? '/app/' : '/',
})
