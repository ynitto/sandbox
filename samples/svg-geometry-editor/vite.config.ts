import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    lib: { entry: 'src/index.ts', name: 'SvgGeometryEditor', formats: ['es'], fileName: 'index' },
    rollupOptions: { external: ['react', 'react-dom', 'react/jsx-runtime', '@mui/material', '@emotion/react', '@emotion/styled'] }
  },
  test: { environment: 'jsdom' }
});
