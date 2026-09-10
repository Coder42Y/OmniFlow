// 只复制这一个已批准的公开规范文件，不扩大 Vite 静态访问范围。
import { copyFileSync } from 'node:fs';
copyFileSync(
  new URL('../../docs/design/tokens.css', import.meta.url),
  new URL('../src/tokens.css', import.meta.url),
);
