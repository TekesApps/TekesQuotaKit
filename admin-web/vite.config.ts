import { defineConfig } from 'vite';

// The build ships inside the Python package and is served at /admin (or /<prefix>/admin
// behind a proxy). The page URL has no trailing slash, so the HTML must reference
// "admin/assets/..." to stay under the prefix; assets loaded from JS resolve relative to
// the importing file.
export default defineConfig({
  base: './',
  build: {
    outDir: '../src/tekes_quota_kit/admin_assets',
    emptyOutDir: true,
    assetsDir: 'assets',
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      // antd marks modules "use client" for React Server Components; irrelevant to this SPA.
      onwarn(warning, warn) { if (warning.code !== 'MODULE_LEVEL_DIRECTIVE') warn(warning); },
    },
  },
  experimental: {
    renderBuiltUrl(filename, { hostType }) {
      return hostType === 'html' ? `admin/${filename}` : { relative: true };
    },
  },
  server: { port: 5173, strictPort: true, proxy: { '/v1': 'http://127.0.0.1:9460' } },
});
