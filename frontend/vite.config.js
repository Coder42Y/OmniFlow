import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('.', import.meta.url));
export default defineConfig({
  root,
  plugins: [vue()],
  // 不加载任何项目 .env；只提供回环开发服务器，不隐式代理到旧站。
  envDir: false,
  server: { host: '127.0.0.1', fs: { strict: true, allow: [root] } },
  preview: { host: '127.0.0.1' },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.js'],
    restoreMocks: true,
    testTimeout: 20000,
    hookTimeout: 30000,
  },
});
