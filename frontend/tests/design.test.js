// @vitest-environment node
import { describe, it, expect } from 'vitest';
import { readFile } from 'node:fs/promises';
const root = new URL('../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');
const luminance = (hex) => {
  const c = hex
    .replace('#', '')
    .match(/../g)
    .map((x) => parseInt(x, 16) / 255)
    .map((x) => (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4));
  return c[0] * 0.2126 + c[1] * 0.7152 + c[2] * 0.0722;
};
export const contrast = (a, b) => {
  const values = [luminance(a), luminance(b)].sort((a, b) => a - b);
  return (values[1] + 0.05) / (values[0] + 0.05);
};
describe('视觉规范的受控接入', () => {
  it('应用 tokens 逐字节来自规范，Vite 不扩大文件可见范围', async () => {
    expect(await read('src/tokens.css')).toBe(await read('../docs/design/tokens.css'));
    expect(await read('src/style.css')).toContain("@import './tokens.css'");
    expect(await read('vite.config.js')).toContain('allow: [root]');
    expect(await read('vite.config.js')).toContain('envDir: false');
  });
  it('正常文字、主操作、必要边界和焦点达到规范对比度，波峰使用批准例外', async () => {
    const css = await read('src/tokens.css');
    const tokens = Object.fromEntries(
      [...css.matchAll(/--of-([\w-]+):\s*(#[0-9a-f]{6})/g)].map((m) => [m[1], m[2]]),
    );
    for (const [a, b] of [
      ['text', 'bg'],
      ['muted', 'bg'],
      ['muted', 'sidebar'],
      ['primary', 'surface'],
      ['primary-hover', 'surface'],
      ['danger', 'danger-bg'],
      ['warning', 'warning-bg'],
      ['success', 'success-bg'],
    ])
      expect(contrast(tokens[a], tokens[b]), `${a}/${b}`).toBeGreaterThanOrEqual(4.5);
    for (const [a, b] of [
      ['control-border', 'surface'],
      ['focus', 'surface'],
      ['focus', 'sidebar'],
    ])
      expect(contrast(tokens[a], tokens[b]), `${a}/${b}`).toBeGreaterThanOrEqual(3);
    expect(tokens.primary).toBe('#506b83');
    // 波峰灰色几何已批准，不能借统一对比度重设计；焦点环另有>=3:1检查。
    expect(css).toContain('--of-rail-idle: 6px');
    expect(css).toContain('--of-rail-step: 10px');
    expect(css).toContain('--of-rail-peak: 28px');
  });
});
