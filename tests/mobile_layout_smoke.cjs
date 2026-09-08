// 运行：NODE_PATH=<已安装 playwright 的 node_modules 路径> node tests/mobile_layout_smoke.cjs
// 使用本机 Chrome；所有 /api/ 请求均拦截，不调用真实生成服务。
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const cases = [
  { width: 320, height: 568, name: 'narrow' },
  { width: 360, height: 640, name: 'android-wechat', userAgent: 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49' },
  { width: 390, height: 844, name: 'iphone-wechat', userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.49' },
  { width: 1440, height: 900, name: 'desktop' }
];
(async () => {
  const server = http.createServer((req, res) => {
    if (req.url !== '/') { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end(fs.readFileSync(path.join(root, 'index.html')));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
    for (const test of cases) {
      const mobile = test.width < 768;
      const page = await browser.newPage({ viewport: { width: test.width, height: test.height }, isMobile: mobile, hasTouch: mobile, userAgent: test.userAgent });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/api/**', route => route.fulfill({ contentType: 'application/json', body: '{}' }));
      await page.goto(process.env.MOBILE_TEST_URL || `http://127.0.0.1:${server.address().port}/`, { waitUntil: 'networkidle' });
      await page.waitForSelector('#app:not([v-cloak])');
      assert.equal(await page.locator('.desktop-sidebar').isVisible(), !mobile);
      assert.equal(await page.locator('.mobile-nav').isVisible(), mobile);
      if (mobile) {
        assert.equal(await page.locator('.session-panel').evaluate(el => getComputedStyle(el).width), '0px');
        assert.equal(await page.locator('.mobile-nav').evaluate(el => el.parentElement.id), 'app');
        for (const label of ['文案', '海报', '视频', '对话']) {
          await page.getByRole('navigation', { name: '手机工作区导航' }).getByRole('button', { name: label, exact: true }).click();
          const layout = await page.evaluate(() => ({
            width: document.documentElement.scrollWidth,
            viewport: innerWidth,
            workspaces: [...document.querySelector('main').children].filter(el => getComputedStyle(el).display !== 'none').map(el => ({ scroll: el.scrollWidth, client: el.clientWidth }))
          }));
          assert.ok(layout.width <= layout.viewport, `${test.name} ${label}: 页面溢出`);
          assert.ok(layout.workspaces.every(el => el.scroll <= el.client + 1), `${test.name} ${label}: 工作区溢出`);
          if (label === '海报') {
            const advanced = page.locator('.mobile-advanced');
            assert.equal(await advanced.evaluate(el => el.open), false);
            await advanced.locator('summary').click();
            assert.equal(await advanced.evaluate(el => el.open), true);
          }
        }
        await page.locator('.chat-header button').first().click();
        assert.equal(await page.locator('.mobile-drawer-close').isVisible(), true);
        await page.locator('.mobile-drawer-close').click();
        const composer = await page.locator('.chat-composer').boundingBox();
        const nav = await page.locator('.mobile-nav').boundingBox();
        assert.ok(composer.y + composer.height <= nav.y + 1, '输入框被导航遮挡');
        await page.locator('.chat-composer textarea').fill('测试输入，不发送');
        assert.equal(await page.locator('.mobile-nav').isVisible(), false);
        await page.locator('.chat-composer textarea').blur();
        await page.waitForTimeout(150);
        assert.equal(await page.locator('.mobile-nav').isVisible(), true);
      }
      assert.deepEqual(errors, [], '存在页面脚本异常');
      if (process.env.MOBILE_SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.MOBILE_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.MOBILE_SCREENSHOT_DIR, test.name + '.png') });
      }
      console.log(`PASS ${test.name} ${test.width}×${test.height}`);
      await page.close();
    }
    console.log('注意：微信标识模拟仍使用 Chrome 内核，不等于 iOS/微信真机验收。');
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
