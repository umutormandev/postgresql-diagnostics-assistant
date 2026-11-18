import { defineConfig } from 'vite';

const proxy = {
  '/diagnose': process.env.DIAGNOSTICS_API_URL || 'http://127.0.0.1:8000',
};

export default defineConfig({ server: { proxy }, preview: { proxy } });
