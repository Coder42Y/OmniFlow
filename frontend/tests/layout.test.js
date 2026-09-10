// @vitest-environment node
import { beforeAll, afterAll, describe, it, expect } from 'vitest';
import { createServer } from 'vite';
import { chromium } from 'playwright';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { relative } from 'node:path';
import {
  id,
  time,
  user,
  policy,
  capabilities,
  conversation,
  snapshot,
  message,
  version,
  artifact,
  task,
} from './fixtures.js';
let server, browser, origin;
const visualEvidence = [];
const capture = async (page, name) => {
  const path = `test-results/ui-v2/${name}.png`;
  await page.screenshot({ path: `${root}/${path}` });
  const measurements = await page.evaluate(() => ({
    viewportWidth: innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    transcriptScrollTop: document.querySelector('#transcript')?.scrollTop,
    controls: [...document.querySelectorAll('button,a,summary,[role=slider]')]
      .filter((el) => el.getClientRects().length)
      .map((el) => {
        const r = el.getBoundingClientRect();
        return {
          label: el.getAttribute('aria-label') || el.textContent.trim().slice(0, 60),
          width: r.width,
          height: r.height,
        };
      }),
  }));
  visualEvidence.push({
    path: `frontend/${path}`,
    viewport: page.viewportSize(),
    captured_at: new Date().toISOString(),
    measurements,
  });
};
const root = fileURLToPath(new URL('..', import.meta.url));
beforeAll(async () => {
  server = await createServer({
    configFile: root + '/vite.config.js',
    server: { host: '127.0.0.1', port: 0, strictPort: false },
    clearScreen: false,
  });
  await server.listen();
  origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: true,
    args: ['--disable-background-networking'],
  });
  await mkdir(root + '/test-results/ui-v2', { recursive: true });
});
afterAll(async () => {
  await browser?.close();
  await server?.close();
  const files = (await readdir(root + '/src', { recursive: true, withFileTypes: true }))
    .filter((entry) => entry.isFile())
    .map((entry) => `${entry.parentPath}/${entry.name}`)
    .sort();
  const sources = [];
  for (const file of files)
    sources.push({
      path: 'frontend/' + relative(root, file),
      sha256: createHash('sha256')
        .update(await readFile(file))
        .digest('hex'),
    });
  await writeFile(
    root + '/test-results/ui-v2/manifest.json',
    JSON.stringify(
      {
        generated_at: new Date().toISOString(),
        fixture: '仅合成账号、对话与本地绘制图片／FFmpeg测试视频，API全部拦截，无真实提供方调用',
        sources,
        screenshots: visualEvidence,
      },
      null,
      2,
    ),
  );
});

async function scene(options = {}) {
  const context = await browser.newContext({
    viewport: { width: options.width || 1100, height: options.height || 820 },
    hasTouch: !!options.touch,
    isMobile: !!options.touch,
    reducedMotion: options.reduced ? 'reduce' : 'no-preference',
  });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const errors = [],
    requests = [],
    mutations = [],
    s = snapshot(options.turns ?? 18);
  if (options.healthy || options.visual)
    await page.addInitScript(() => {
      // 合成常开 SSE，浏览器 route.fulfill 的有限正文不能模拟健康长连接。
      const original = window.fetch.bind(window);
      window.__syntheticStreamCount = 0;
      window.fetch = async (url, init) => {
        if (!String(url).includes('/events?')) return original(url, init);
        window.__syntheticStreamCount++;
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode(': synthetic heartbeat\n\n'));
            let closed = false;
            const end = () => {
              if (!closed) {
                closed = true;
                controller.close();
              }
            };
            window.__endSyntheticStream = end;
            init?.signal?.addEventListener('abort', end, { once: true });
          },
        });
        return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
      };
    });
  const visualVersion = options.visual
    ? {
        ...version(options.video ? 'local-ffmpeg' : null),
        execution_engine: options.video ? 'local-ffmpeg' : 'agnes-image-2.5-flash',
        source_task_id: id(8),
        width: options.landscape ? 640 : 360,
        height: options.landscape ? 360 : 540,
      }
    : version();
  if (options.visual)
    visualVersion.byte_size = (
      await readFile(
        root +
          `/tests/assets/visual-${options.video ? 'local.mp4' : options.landscape ? 'landscape.png' : 'portrait.png'}`,
      )
    ).length;
  const taskMutations = [];
  let sourceTask;
  let loggedIn = !options.anonymous,
    lost = false,
    storedMessage = null;
  if (options.media || options.visual) {
    s.messages.at(-1).artifact_version_ids = [id(6)];
    s.artifact_versions = [visualVersion];
    s.tasks = options.visual
      ? [
          {
            ...task('completed'),
            kind: options.video ? 'local_motion' : 'image',
            execution_engine: visualVersion.execution_engine,
            output_version_ids: [id(6)],
            requested_parameters: options.video
              ? {
                  kind: 'local_motion',
                  image_version_id: id(9),
                  motion_type: 'dolly_in',
                  seconds: 1.25,
                  aspect_ratio: '9:16',
                }
              : {
                  kind: 'image',
                  prompt: '合成视觉稿',
                  aspect_ratio: options.landscape ? '16:9' : '9:16',
                  size_tier: '1K',
                  reference_version_ids: [],
                },
          },
        ]
      : [task('submission_unknown')];
    sourceTask = s.tasks[0];
    if (options.taskStates)
      s.tasks = options.taskStates.map((status, i) => ({
        ...task(status),
        id: id(800 + i),
        error:
          status === 'failed'
            ? { code: 'PROVIDER_UNAVAILABLE', message: '提供方暂不可用，请稍后查看可用状态。' }
            : null,
      }));
    if (options.visual) {
      s.messages.at(-1).content =
        '保留冷白背景与蓝色丝带。可以选定这版画面，继续讨论修改。\n合成界面夹具，不是真实模型响应。';
      s.messages.at(-2).content = '做一版透明瓶体的视觉稿，突出蓝色丝带。';
    }
  }
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/*', async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    if (!url.pathname.startsWith('/api/v1/')) return route.continue();
    const path = url.pathname.slice('/api/v1'.length);
    requests.push({ path, method: request.method() });
    const respond = (body, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/auth/csrf')
      return respond({ csrf_token: 'synthetic_csrf_token_only_0000000000', expires_at: time });
    if (path === '/auth/policy') return respond(policy);
    if (path === '/auth/me')
      return loggedIn
        ? respond({ ...user, role: options.admin ? 'admin' : 'user' })
        : respond({ code: 'AUTH_REQUIRED', detail: '请登录', retryable: false }, 401);
    if (path === '/auth/login') {
      loggedIn = true;
      return respond({
        user,
        csrf_token: 'synthetic_login_csrf_0000000000000000',
        session_expires_at: time,
      });
    }
    if (path === '/auth/logout') {
      loggedIn = false;
      return route.fulfill({ status: 204 });
    }
    if (path === '/capabilities') return respond(options.capabilities || capabilities);
    if (path === '/conversations') {
      if (request.method() === 'POST') return respond(conversation(3), 201);
      return respond({ items: [conversation(), conversation(3)], next_cursor: null });
    }
    if (path.endsWith('/snapshot'))
      return respond({
        ...s,
        conversation: { ...s.conversation, id: path.split('/')[2] },
        messages: s.messages.map((m) => ({ ...m, conversation_id: path.split('/')[2] })),
      });
    if (path.endsWith('/events'))
      return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' });
    if (path.endsWith('/messages') && request.method() === 'POST') {
      const body = request.postDataJSON(),
        key = request.headers()['idempotency-key'];
      mutations.push({ body, key });
      if (!storedMessage)
        storedMessage = {
          ...message(s.messages.length + 1),
          content: body.content,
          client_message_id: body.client_message_id,
          attachment_version_ids: body.attachment_version_ids,
          selected_version_id: body.selected_version_id || null,
        };
      if (options.loseMessage && !lost) {
        lost = true;
        return route.abort('failed');
      }
      return respond(
        {
          message: storedMessage,
          run: {
            id: storedMessage.run_id,
            conversation_id: id(2),
            user_message_id: storedMessage.id,
            assistant_message_id: null,
            status: 'queued',
            task_ids: [],
            error: null,
            created_at: time,
            updated_at: time,
          },
        },
        202,
      );
    }
    if (path === '/artifacts' && request.method() === 'GET')
      return respond({ items: [artifact()], next_cursor: null });
    if (path.endsWith('/versions')) return respond({ items: [visualVersion], next_cursor: null });
    if (path.endsWith('/reference-confirmations'))
      return respond(
        { id: id(7), version_id: id(6), conversation_id: id(2), purpose: 'video_first_frame' },
        201,
      );
    if (path.endsWith('/content') && options.visual) {
      if (options.holdMedia) await options.holdMedia;
      if (options.brokenMedia) return route.abort('failed');
      return route.fulfill({
        contentType: options.video ? 'video/mp4' : 'image/png',
        body: await readFile(
          root +
            `/tests/assets/visual-${options.video ? 'local.mp4' : options.landscape ? 'landscape.png' : 'portrait.png'}`,
        ),
      });
    }
    if (path.endsWith('/content'))
      return route.fulfill({
        contentType: 'image/png',
        body: Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jY1sAAAAASUVORK5CYII=',
          'base64',
        ),
      });
    if (path === `/tasks/${id(8)}`) return respond(sourceTask);
    if (path === '/tasks' && request.method() === 'POST') {
      taskMutations.push({
        body: request.postDataJSON(),
        key: request.headers()['idempotency-key'],
      });
      if (options.holdTask) await options.holdTask;
      if (options.loseTask && taskMutations.length === 1) return route.abort('failed');
      return respond(task(), 202);
    }
    if (path === '/tasks') return respond({ items: s.tasks, next_cursor: null });
    if (path.endsWith('/runs')) return respond({ items: [], next_cursor: null });
    if (path.startsWith('/admin/')) return respond({ items: [], next_cursor: null });
    return respond(
      { code: 'RESOURCE_NOT_FOUND', detail: '合成测试未配置路径', retryable: false },
      404,
    );
  });
  await page.goto(origin);
  if (!options.anonymous) await page.getByLabel('创作需求').waitFor();
  return { page, requests, mutations, taskMutations, errors, close: () => context.close() };
}
async function noOverflow(page) {
  const dimensions = await page.evaluate(() => ({
    width: innerWidth,
    html: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
    modal: document.querySelector('dialog[open]') && {
      scroll: document.querySelector('dialog[open]').scrollWidth,
      width: document.querySelector('dialog[open]').clientWidth,
    },
  }));
  expect(dimensions.html).toBeLessThanOrEqual(dimensions.width);
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.width);
  if (dimensions.modal)
    expect(dimensions.modal.scroll).toBeLessThanOrEqual(dimensions.modal.width + 1);
}

describe('真实 Chromium 布局与键盘（只拦截合成 API，不是阶段07联调）', () => {
  it.each([320, 390])('%dpx 工作台／作品库／精修无横向溢出，抽屉可关闭', async (width) => {
    const sceneState = await scene({ width, media: true });
    const { page } = sceneState;
    try {
      await page.locator('.messages .turn').last().waitFor();
      await noOverflow(page);
      await page.screenshot({ path: `${root}/test-results/workbench-${width}.png` });
      await page.getByLabel('展开或收起对话历史').click();
      await page.getByRole('dialog', { name: '对话历史' }).waitFor();
      expect(await page.locator('#history-nav').getAttribute('aria-modal')).toBe('true');
      await page.keyboard.press('Shift+Tab');
      expect(
        await page
          .locator('#history-nav button')
          .last()
          .evaluate((el) => el === document.activeElement),
      ).toBe(true);
      await page.keyboard.press('Tab');
      expect(
        await page
          .getByLabel('关闭历史', { exact: true })
          .evaluate((el) => el === document.activeElement),
      ).toBe(true);
      await noOverflow(page);
      await page.keyboard.press('Escape');
      expect(await page.getByRole('dialog', { name: '对话历史' }).count()).toBe(0);
      expect(
        await page.getByLabel('展开或收起对话历史').evaluate((el) => el === document.activeElement),
      ).toBe(true);
      await page.getByRole('button', { name: '素材', exact: true }).click();
      await page.getByRole('button', { name: /合成视觉稿/ }).click();
      await noOverflow(page);
      await page.getByRole('dialog').getByRole('button', { name: '基本精修', exact: true }).click();
      await page.getByLabel('创作方式').waitFor();
      await noOverflow(page);
      await page.getByLabel('创作方式').selectOption('ai_video');
      await noOverflow(page);
      await page.screenshot({ path: `${root}/test-results/refine-${width}.png` });
      await page.keyboard.press('Escape');
      expect(await page.getByRole('dialog').count()).toBe(0);
      expect(sceneState.errors).toEqual([]);
    } finally {
      await sceneState.close();
    }
  });
  it.each([320, 390])('%dpx 登录页动态策略与键盘输入', async (width) => {
    const state = await scene({ width, anonymous: true });
    try {
      await state.page.getByRole('heading', { name: '回到你的创作' }).waitFor();
      await noOverflow(state.page);
      await state.page.getByLabel('用户名', { exact: true }).fill('synthetic_creator');
      await state.page.getByLabel('密码', { exact: true }).fill('synthetic password');
      await state.page.getByRole('button', { name: '登录', exact: true }).first().click();
      await state.page.getByLabel('创作需求').waitFor();
      await noOverflow(state.page);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it('主导航与历史独立折叠；横线波峰、预览、活动标记、键盘与拖动', async () => {
    const state = await scene({ reduced: true });
    const { page } = state;
    try {
      const rail = page.getByRole('slider', { name: '对话历史横线定位条' });
      await rail.waitFor();
      await page.getByLabel('展开或收起对话历史').click();
      await page.getByLabel('展开或收起主导航').click();
      expect(await page.getByRole('complementary', { name: '对话历史' }).count()).toBe(1);
      expect(await page.locator('.sidebar').getAttribute('class')).toContain('expanded');
      await page.getByLabel('展开或收起主导航').click();
      expect(await page.getByRole('complementary', { name: '对话历史' }).count()).toBe(1);
      await rail.focus();
      await page.keyboard.press('Home');
      await expect.poll(() => rail.getAttribute('aria-valuenow')).toBe('1');
      await page.keyboard.press('PageDown');
      await expect.poll(() => rail.getAttribute('aria-valuenow')).toBe('4');
      const rect = await rail.boundingBox();
      await page.mouse.move(rect.x + 18, rect.y + 12 + 7 * 10);
      await page.waitForFunction(
        () => Number.parseFloat(document.querySelectorAll('.rib')[7].style.width) > 27,
      );
      const widths = await page
        .locator('.rib')
        .evaluateAll((nodes) => nodes.map((n) => parseFloat(n.style.width)));
      expect(widths[7]).toBe(28);
      expect(widths[0]).toBe(6);
      expect(widths[6]).toBeGreaterThan(widths[5]);
      expect(await page.locator('.rail-tip').getAttribute('class')).toContain('visible');
      await page.mouse.down();
      await page.mouse.move(rect.x + 18, rect.y + 12 + 10 * 10);
      await page.mouse.up();
      expect(Number(await rail.getAttribute('aria-valuenow'))).toBeGreaterThanOrEqual(10);
      await page.mouse.move(rect.x + 200, rect.y);
      await page.waitForFunction(() =>
        [...document.querySelectorAll('.rib')].every((n) => parseFloat(n.style.width) === 6),
      );
      await page.mouse.move(rect.x + 18, rect.y + 50);
      await page.mouse.down();
      await page.mouse.move(rect.x + 18, rect.y + rect.height + 20);
      await page.mouse.up();
      await page.waitForFunction(() =>
        [...document.querySelectorAll('.rib')].every((n) => parseFloat(n.style.width) === 6),
      );
      await rail.focus();
      await page.keyboard.press('End');
      // 键盘输入完成不代表 Vue 与滚动回调已完成渲染；等待明确的最终轮次，
      // 不接受前一轮，也不靠固定 sleep。随后跨帧复核标记与实际目标可见性。
      await expect.poll(() => rail.getAttribute('aria-valuenow')).toBe('18');
      const settled = await rail.evaluate(async (el) => {
        await new Promise(requestAnimationFrame);
        await new Promise(requestAnimationFrame);
        const root = document.querySelector('#transcript').getBoundingClientRect();
        const target = [...document.querySelectorAll('.turn')].at(-2).getBoundingClientRect();
        return {
          value: el.getAttribute('aria-valuenow'),
          current: [...el.querySelectorAll('.rib')].findIndex((line) =>
            line.classList.contains('current'),
          ),
          targetVisible: target.bottom > root.top && target.top < root.bottom,
        };
      });
      expect(settled).toEqual({ value: '18', current: 17, targetVisible: true });
      const reduced = await page
        .locator('.rib')
        .first()
        .evaluate((el) => getComputedStyle(el).transitionDuration);
      expect(reduced).toBe('0s');
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it('已在底部按 End 不插入回到最新按钮，不抖动高度或退回上一轮', async () => {
    const state = await scene({ reduced: true });
    const { page } = state;
    let trace;
    try {
      const rail = page.getByRole('slider');
      await rail.waitFor();
      await page.waitForFunction(() => {
        const root = document.querySelector('#transcript');
        return root && root.scrollHeight - root.scrollTop - root.clientHeight < 5;
      });
      await rail.focus();
      trace = await page.evaluateHandle(async () => {
        await new Promise(requestAnimationFrame);
        await new Promise(requestAnimationFrame);
        const root = document.querySelector('#transcript');
        const record = { inserted: 0, height: root.clientHeight };
        record.observer = new MutationObserver((changes) => {
          for (const change of changes)
            for (const node of change.addedNodes)
              if (node.nodeType === 1 && node.matches('.back-latest')) record.inserted++;
        });
        record.observer.observe(root.parentElement, { childList: true });
        return record;
      });
      await page.keyboard.press('End');
      const settled = await trace.evaluate(async (record) => {
        // 跨多个实际布局帧检查，防止只断言瞬间出现的正确值掩盖随后回退。
        for (let i = 0; i < 4; i++) await new Promise(requestAnimationFrame);
        record.observer.disconnect();
        const root = document.querySelector('#transcript');
        return {
          inserted: record.inserted,
          sameHeight: root.clientHeight === record.height,
          value: document.querySelector('.rail').getAttribute('aria-valuenow'),
          remaining: root.scrollHeight - root.scrollTop - root.clientHeight,
        };
      });
      expect(settled).toEqual({ inserted: 0, sameHeight: true, value: '18', remaining: 0 });
      expect(await page.locator('.back-latest').count()).toBe(0);
      expect(state.errors).toEqual([]);
    } finally {
      await trace?.evaluate((record) => record.observer.disconnect()).catch(() => {});
      await trace?.dispose();
      await state.close();
    }
  });
  it('阅读旧消息不被新回复／媒体加载拉到底部，显式回到最新才滚动', async () => {
    const state = await scene({ turns: 25 });
    const { page } = state;
    try {
      const rail = page.getByRole('slider');
      await rail.waitFor();
      await rail.focus();
      await page.keyboard.press('Home');
      await page.keyboard.press('PageDown');
      const anchor = page.locator('.turn').nth(6);
      const before = (await anchor.boundingBox()).y;
      await page.getByLabel('创作需求').fill('合成新消息，不应强制滚到底部');
      await page.getByLabel('发送消息', { exact: true }).click();
      await page.getByText('消息已保存，文字轮次已排队', { exact: false }).waitFor();
      expect(Math.abs((await anchor.boundingBox()).y - before)).toBeLessThan(3);
      // 模拟上方媒体完成解码后的真实布局增高，走组件 ResizeObserver，而非直接改状态。
      await page
        .locator('.turn')
        .first()
        .evaluate((el) => {
          const media = document.createElement('div');
          media.style.height = '300px';
          el.append(media);
        });
      await page.waitForTimeout(100);
      expect(Math.abs((await anchor.boundingBox()).y - before)).toBeLessThan(3);
      await page.getByLabel('对话设置').click();
      await page.getByRole('dialog').waitFor();
      await page.keyboard.press('Escape');
      expect(Math.abs((await anchor.boundingBox()).y - before)).toBeLessThan(3);
      await page.getByRole('button', { name: '↓ 回到最新' }).click();
      const distance = await page
        .locator('#transcript')
        .evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight);
      expect(distance).toBeLessThan(5);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it('丢失受理响应后点击重试复用键与 client_message_id，界面不虚报完成', async () => {
    const state = await scene({ turns: 0, loseMessage: true });
    const { page } = state;
    try {
      await page.getByLabel('创作需求').fill('合成重试消息');
      await page.getByLabel('发送消息', { exact: true }).click();
      await page.getByRole('button', { name: '重试同一动作' }).waitFor();
      // 打开任务窗口不能让刷新动作清除原消息的重试闭包。
      await page.getByRole('button', { name: /^任务/ }).click();
      await page.getByRole('dialog').getByRole('button', { name: '重试同一动作' }).waitFor();
      const taskReads = () =>
        state.requests.filter((r) => r.path === '/tasks' && r.method === 'GET');
      await expect.poll(() => taskReads().length).toBe(1);
      await page.getByRole('button', { name: '刷新任务状态', exact: true }).click();
      await expect.poll(() => taskReads().length).toBe(2);
      expect(state.mutations).toHaveLength(1);
      await page.getByRole('dialog').getByRole('button', { name: '重试同一动作' }).waitFor();
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: '重试同一动作' }).click();
      await page.getByText('文字轮次 · 排队中', { exact: true }).waitFor();
      expect(state.mutations).toHaveLength(2);
      expect(state.mutations[0]).toEqual(state.mutations[1]);
      expect(await page.locator('.turn').count()).toBe(1);
      expect(await page.locator('.user-message').textContent()).toContain('合成重试消息');
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it.each([320, 1100])('%dpx 未确认消息不阻止实际注销，退出后清除重试与私密界面', async (width) => {
    const state = await scene({ width, turns: 1, loseMessage: true, healthy: true });
    const { page } = state;
    try {
      await page.getByLabel('创作需求').fill('注销前未确认的合成消息');
      await page.getByLabel('发送消息', { exact: true }).click();
      await page.getByRole('button', { name: '重试同一动作' }).waitFor();
      if (width === 320) await page.getByLabel('展开或收起主导航').click();
      await page.getByLabel('账号', { exact: true }).click();
      await page.getByRole('button', { name: '退出登录', exact: true }).click();
      await expect
        .poll(
          () =>
            state.requests.filter((r) => r.path === '/auth/logout' && r.method === 'POST').length,
        )
        .toBe(1);
      await page.getByRole('heading', { name: '回到你的创作' }).waitFor();
      expect(await page.locator('.turn, dialog[open]').count()).toBe(0);
      expect(await page.getByRole('button', { name: '重试同一动作' }).count()).toBe(0);
      expect(new URL(page.url()).search).toBe('');
      expect(state.mutations).toHaveLength(1);
      expect(state.requests.filter((r) => r.path.endsWith('/cancel'))).toHaveLength(0);
      await noOverflow(page);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it('任务查询失败单独提示，再次读取成功后仍可按原消息编号重试', async () => {
    const state = await scene({ turns: 0, loseMessage: true, healthy: true });
    const { page } = state;
    let reads = 0;
    await page.route('**/api/v1/tasks?*', async (route) => {
      if (++reads === 1)
        return route.fulfill({
          status: 503,
          contentType: 'application/problem+json',
          body: JSON.stringify({
            code: 'PROVIDER_UNAVAILABLE',
            detail: '合成任务读取暂不可用',
            retryable: true,
          }),
        });
      return route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ items: [task('submission_unknown')], next_cursor: null }),
      });
    });
    try {
      await page.getByLabel('创作需求').fill('保留原消息参数');
      await page.getByLabel('发送消息', { exact: true }).click();
      await page.getByRole('button', { name: '重试同一动作' }).waitFor();
      await page.getByRole('button', { name: /^任务/ }).click();
      await page.getByText('合成任务读取暂不可用', { exact: true }).waitFor();
      expect(await page.getByRole('button', { name: '重试同一动作' }).count()).toBe(1);
      await page.getByRole('button', { name: '刷新任务状态', exact: true }).click();
      await page.getByRole('dialog').getByText('是否受理尚不确定', { exact: true }).waitFor();
      expect(await page.getByText('合成任务读取暂不可用', { exact: true }).count()).toBe(0);
      expect(reads).toBe(2);
      expect(state.mutations).toHaveLength(1);
      await page.getByRole('button', { name: '重试同一动作' }).click();
      await page.keyboard.press('Escape');
      await page.getByText('文字轮次 · 排队中', { exact: true }).waitFor();
      expect(state.mutations).toHaveLength(2);
      expect(state.mutations[0]).toEqual(state.mutations[1]);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it('注销失败不假装已退出，也不覆盖原消息重试；再次明确退出实际撤销', async () => {
    const state = await scene({ turns: 1, loseMessage: true, healthy: true });
    const { page } = state;
    let attempts = 0;
    await page.route('**/api/v1/auth/logout', async (route) => {
      if (++attempts === 1)
        return route.fulfill({
          status: 503,
          contentType: 'application/problem+json',
          body: JSON.stringify({
            code: 'INTERNAL_ERROR',
            detail: '合成注销未完成',
            retryable: false,
          }),
        });
      return route.fallback();
    });
    try {
      await page.getByLabel('创作需求').fill('退出前的原请求');
      await page.getByLabel('发送消息', { exact: true }).click();
      await page.getByRole('button', { name: '重试同一动作' }).waitFor();
      await page.getByLabel('账号', { exact: true }).click();
      await page.getByText('退出会丢弃本页草稿和未确认请求的重试信息', { exact: false }).waitFor();
      await page.getByRole('button', { name: '退出登录', exact: true }).click();
      await page.getByText('合成注销未完成', { exact: true }).waitFor();
      expect(await page.locator('.turn').count()).toBe(snapshot(1).messages.length);
      expect(await page.getByRole('button', { name: '重试同一动作' }).count()).toBe(1);
      expect(state.mutations).toHaveLength(1);
      await page.getByRole('button', { name: '退出登录', exact: true }).click();
      await page.getByRole('heading', { name: '回到你的创作' }).waitFor();
      expect(attempts).toBe(2);
      expect(await page.locator('.turn').count()).toBe(0);
      expect(state.mutations).toHaveLength(1);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it('切换对话保持独立草稿，刷新根据非秘密对话编号恢复，不默认最近会话', async () => {
    const state = await scene({ turns: 1 });
    try {
      const { page } = state;
      await page.getByLabel('创作需求').fill('只属于第一段的未发送草稿');
      await page.getByLabel('展开或收起对话历史').click();
      await page.getByRole('button', { name: '合成创作对话 3', exact: true }).click();
      expect(await page.getByLabel('创作需求').inputValue()).toBe('');
      expect(new URL(page.url()).searchParams.get('conversation')).toBe(id(3));
      await page.reload();
      await page.getByLabel('创作需求').waitFor();
      expect(new URL(page.url()).searchParams.get('conversation')).toBe(id(3));
      expect(state.requests.some((r) => r.path === `/conversations/${id(3)}/snapshot`)).toBe(true);
      expect(state.requests.filter((r) => r.method === 'POST')).toHaveLength(0);
    } finally {
      await state.close();
    }
  });
  it('仅讨论权限与中文输入法组合输入不会意外发送', async () => {
    const state = await scene({ turns: 0 });
    try {
      const { page } = state;
      await page.getByText('创作设置', { exact: true }).click();
      await page.getByLabel('仅讨论，不创建媒体').check();
      await page.getByLabel('创作需求').fill('只咨询，不生成');
      await page
        .getByLabel('创作需求')
        .evaluate((el) =>
          el.dispatchEvent(
            new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }),
          ),
        );
      expect(state.mutations).toHaveLength(0);
      await page.getByLabel('发送消息', { exact: true }).click();
      await page.getByText('文字轮次 · 排队中', { exact: true }).waitFor();
      expect(state.mutations[0].body.generation_permission).toBe('discuss_only');
    } finally {
      await state.close();
    }
  });
  it('注销清除私密界面和内存对话，不写浏览器持久存储', async () => {
    const state = await scene();
    try {
      await state.page.getByLabel('账号', { exact: true }).click();
      await state.page.getByRole('button', { name: '退出登录', exact: true }).click();
      await state.page.getByRole('heading', { name: '回到你的创作' }).waitFor();
      expect(await state.page.locator('.turn').count()).toBe(0);
      expect(
        await state.page.evaluate(() => ({
          local: localStorage.length,
          session: sessionStorage.length,
        })),
      ).toEqual({ local: 0, session: 0 });
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it.each([320, 390])(
    'UI-01/02/04：%dpx 真实触摸命中、字级、长弹窗首尾可见与焦点循环',
    async (width) => {
      const state = await scene({
        width,
        height: 800,
        visual: true,
        touch: true,
        turns: 6,
        reduced: true,
      });
      const { page } = state;
      const targets = async (scope = 'body') => {
        const small = await page.locator(scope).evaluate((root) =>
          [
            ...root.querySelectorAll(
              'button,a,summary,textarea,select,input:not([type=file]):not([type=checkbox]),[role=slider]',
            ),
          ]
            .filter(
              (el) => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden',
            )
            .map((el) => ({
              name: el.getAttribute('aria-label') || el.textContent.trim().slice(0, 35),
              w: el.getBoundingClientRect().width,
              h: el.getBoundingClientRect().height,
            }))
            .filter((r) => r.w < 44 || r.h < 44),
        );
        expect(small).toEqual([]);
      };
      try {
        await page.locator('.turn').last().waitFor();
        await page.getByRole('slider').focus();
        await page.keyboard.press('End');
        await page.mouse.move(width - 2, 1);
        await page.getByRole('slider').blur();
        await page.waitForTimeout(100);
        await noOverflow(page);
        await targets();
        expect(
          await page
            .locator('.assistant-message')
            .last()
            .evaluate((el) => parseFloat(getComputedStyle(el).fontSize)),
        ).toBeGreaterThanOrEqual(15);
        expect(
          await page.getByLabel('创作需求').evaluate((el) => getComputedStyle(el).fontSize),
        ).toBe('16px');
        expect(await page.locator('.desktop-shortcut').isVisible()).toBe(false);
        await capture(page, `mobile-${width}-chat`);
        await page.locator('.media-card').scrollIntoViewIfNeeded();
        // 不仅尺寸够大：操作中心不能被“回到最新”或输入区盖住。
        for (const control of await page
          .locator('.media-card .actions a, .media-card .actions button')
          .all()) {
          await control.scrollIntoViewIfNeeded();
          expect(
            await control.evaluate((el) => {
              const r = el.getBoundingClientRect();
              return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
            }),
          ).toBe(true);
        }
        await capture(page, `mobile-${width}-media`);
        await page.getByRole('button', { name: '基本精修', exact: true }).last().click();
        const dialog = page.getByRole('dialog');
        await dialog.getByLabel('创作方式').selectOption('ai_video');
        await page.getByText('版本与生成说明', { exact: true }).click();
        await page.getByLabel('修改／创作要求').fill('让丝带轻轻摆动，固定镜头');
        await noOverflow(page);
        await targets('dialog');
        const header = await page.locator('.modal-header').boundingBox();
        const submit = dialog.getByRole('button', { name: '提交一份任务', exact: true });
        const footer = await submit.boundingBox();
        expect(header.y).toBeGreaterThanOrEqual(0);
        expect(footer.y + footer.height).toBeLessThanOrEqual(800);
        expect(await submit.isDisabled()).toBe(true);
        expect(await page.locator('#refine-requirements').textContent()).toContain(
          '确认此图片首帧',
        );
        await capture(page, `refine-${width}-disabled`);
        const close = page.getByLabel('关闭窗口', { exact: true });
        await close.focus();
        await page.keyboard.press('Shift+Tab');
        expect(
          await page
            .getByText('版本与生成说明', { exact: true })
            .evaluate((el) => el === document.activeElement),
        ).toBe(true);
        await page.keyboard.press('Tab');
        expect(await close.evaluate((el) => el === document.activeElement)).toBe(true);
        await page.getByLabel('画幅').selectOption('9:16');
        await page.getByLabel('请求时长（秒）').fill('4');
        await page.getByRole('button', { name: '确认：用这张图片版本做视频' }).click();
        await page.waitForFunction(() => !document.querySelector('.refine-footer button').disabled);
        await submit.focus();
        await page.keyboard.press('Tab');
        expect(await close.evaluate((el) => el === document.activeElement)).toBe(true);
        await close.focus();
        await page.keyboard.press('Shift+Tab');
        expect(await submit.evaluate((el) => el === document.activeElement)).toBe(true);
        await page.locator('.refine-scroll').evaluate((el) => {
          el.scrollTop = el.scrollHeight;
        });
        await capture(page, `refine-${width}-ready`);
        expect((await submit.boundingBox()).y).toBe(footer.y);
        await page.keyboard.press('Escape');
        expect(await dialog.count()).toBe(0);
        expect(
          await page
            .getByRole('button', { name: '基本精修', exact: true })
            .last()
            .evaluate((el) => el === document.activeElement),
        ).toBe(true);
        // 再打开并真实点击固定主操作；请求仍绑定明确确认，关闭不代表已生成。
        await page.getByRole('button', { name: '基本精修', exact: true }).last().click();
        await page.getByLabel('修改／创作要求').fill('只改变背景');
        await page.getByLabel('画幅').selectOption('1:1');
        await page.getByLabel('尺寸档位').selectOption('1K');
        await page.getByRole('button', { name: '提交一份任务', exact: true }).tap();
        await page.getByText('任务已持久受理', { exact: false }).waitFor();
        await page.getByLabel('展开或收起主导航').tap();
        await page.getByLabel('基本精修与参数创作').tap();
        await page.getByRole('dialog').waitFor();
        await page.keyboard.press('Escape');
        expect(
          await page.getByLabel('展开或收起主导航').evaluate((el) => el === document.activeElement),
        ).toBe(true);
        expect(state.taskMutations).toHaveLength(1);
        expect(state.taskMutations[0].body).toMatchObject({
          base_version_id: id(6),
          target_artifact_id: id(5),
          reference_version_ids: [id(6)],
        });
        expect(state.errors).toEqual([]);
      } finally {
        await state.close();
      }
    },
  );
  it.each([320, 390])('%dpx 缩短可视高度时精修主操作与关闭入口仍可见', async (width) => {
    const state = await scene({ width, height: 800, healthy: true, turns: 2 });
    const { page } = state;
    try {
      await page.getByLabel('展开或收起主导航').click();
      await page.getByLabel('基本精修与参数创作').click();
      await page.getByLabel('修改／创作要求').fill('合成窄屏输入');
      // 只模拟可视区域缩小，不把 Chromium 检查称作 iOS 真机软键盘验证。
      await page.setViewportSize({ width, height: 420 });
      await noOverflow(page);
      for (const control of [page.getByLabel('关闭窗口'), page.locator('.refine-footer button')]) {
        const r = await control.boundingBox();
        expect(r.y).toBeGreaterThanOrEqual(0);
        expect(r.y + r.height).toBeLessThanOrEqual(420);
        expect(
          await control.evaluate((el) => {
            const box = el.getBoundingClientRect();
            return el.contains(
              document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2),
            );
          }),
        ).toBe(true);
      }
      await page.getByLabel('画幅').selectOption('1:1');
      await page.getByLabel('尺寸档位').selectOption('1K');
      expect(await page.locator('.refine-footer button').isDisabled()).toBe(false);
      await page.keyboard.press('Escape');
      expect(await page.getByRole('dialog').count()).toBe(0);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it('UI-03/06：桌面新截图、展开收起与波峰、横图完整比例、正常连接无重连按钮', async () => {
    const state = await scene({ width: 1440, height: 1000, visual: true, turns: 8, reduced: true });
    const { page } = state;
    try {
      await page.getByText('实时连接', { exact: true }).waitFor();
      expect(await page.getByRole('button', { name: '重新连接', exact: true }).count()).toBe(0);
      await page.getByRole('slider').focus();
      await page.keyboard.press('End');
      await page.getByRole('slider').blur();
      await page.mouse.move(1400, 1);
      await capture(page, 'desktop-1440-chat');
      await page.locator('.media-card').scrollIntoViewIfNeeded();
      await capture(page, 'desktop-1440-media');
      await page.getByRole('slider').focus();
      await page.keyboard.press('End');
      await page.getByRole('slider').blur();
      expect(await page.locator('.media-card').count()).toBe(1);
      await page.getByLabel('展开或收起主导航').click();
      await page.getByLabel('展开或收起对话历史').click();
      await capture(page, 'desktop-1440-expanded');
      const rail = page.getByRole('slider');
      const rect = await rail.boundingBox();
      await page.mouse.move(rect.x + 18, rect.y + 12 + 4 * 10);
      await page.waitForFunction(
        () => parseFloat(document.querySelectorAll('.rib')[4].style.width) === 28,
      );
      await capture(page, 'desktop-1440-wave');
      await noOverflow(page);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
    const landscape = await scene({
      width: 1440,
      height: 1000,
      visual: true,
      landscape: true,
      turns: 3,
    });
    try {
      const image = landscape.page.locator('.media-card img');
      await image.waitFor();
      await image.scrollIntoViewIfNeeded();
      await landscape.page.waitForFunction(
        () => document.querySelector('.media-card img')?.naturalWidth === 640,
      );
      const ratio = await image.evaluate(
        (el) => el.getBoundingClientRect().width / el.getBoundingClientRect().height,
      );
      expect(ratio).toBeCloseTo(640 / 360, 2);
      await capture(landscape.page, 'desktop-1440-landscape');
    } finally {
      await landscape.close();
    }
  });
  it('UI-05：媒体读取失败可恢复、连接恢复只订阅，保留原生成请求数量', async () => {
    const options = { width: 390, height: 844, turns: 3, visual: true, brokenMedia: true };
    let releaseMedia;
    const state = await scene(options);
    const { page } = state;
    try {
      await page.getByRole('button', { name: '重试读取' }).waitFor();
      expect(await page.locator('.media-card').textContent()).not.toContain('已删除');
      await page.evaluate(() => window.__endSyntheticStream());
      await page.getByRole('button', { name: '重新连接', exact: true }).waitFor();
      await capture(page, 'mobile-390-error-recovery');
      // 重连会重新挂载卡片；明确让这次媒体读取晚于连接恢复，而非依赖机器速度。
      options.holdMedia = new Promise((resolve) => {
        releaseMedia = resolve;
      });
      await page.getByRole('button', { name: '重新连接', exact: true }).click();
      await page.getByText('实时连接', { exact: true }).waitFor();
      await expect
        .poll(() => state.requests.filter((r) => r.path.endsWith('/content')).length)
        .toBe(2);
      await page.locator('.media-card img').waitFor();
      expect(await page.getByRole('button', { name: '重试读取' }).count()).toBe(0);
      // 连接恢复不等于图片已读完：先完成本次失败并等错误态，再解除故障。
      releaseMedia();
      await page.getByRole('button', { name: '重试读取' }).waitFor();
      options.brokenMedia = false;
      await page.getByRole('button', { name: '重试读取' }).click();
      await page.waitForFunction(
        () => document.querySelector('.media-card img')?.naturalWidth === 360,
      );
      expect(state.requests.filter((r) => r.path.endsWith('/content'))).toHaveLength(3);
      expect(state.requests.filter((r) => r.method === 'POST')).toEqual([]);
      expect(await page.evaluate(() => window.__syntheticStreamCount)).toBe(2);
      await page.getByRole('button', { name: '选为参考图', exact: true }).click();
      await page.getByRole('button', { name: '确认用这张做视频', exact: true }).click();
      await page.getByText('已确认视频首帧', { exact: true }).first().waitFor();
      await capture(page, 'mobile-390-confirmed');
      expect(state.errors).toEqual([]);
    } finally {
      releaseMedia?.();
      await state.close();
    }
  });
  it('UI-05：提交中保留窗口与原动作，禁用重复提交', async () => {
    let finish;
    const holdTask = new Promise((resolve) => {
      finish = resolve;
    });
    const state = await scene({ width: 320, height: 800, visual: true, turns: 2, holdTask });
    const { page } = state;
    try {
      await page.getByRole('button', { name: '基本精修', exact: true }).last().click();
      await page.getByRole('button', { name: '提交一份任务', exact: true }).click();
      await page.waitForFunction(
        () => document.querySelector('.refine-form')?.getAttribute('aria-busy') === 'true',
      );
      expect(await page.locator('.refine-footer button').isDisabled()).toBe(true);
      expect(await page.getByLabel('关闭窗口', { exact: true }).isDisabled()).toBe(true);
      await page.keyboard.press('Escape');
      expect(await page.getByRole('dialog').count()).toBe(1);
      await page
        .locator('.refine-form')
        .evaluate((el) =>
          el.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
        );
      await capture(page, 'refine-320-submitting');
      expect(state.taskMutations).toHaveLength(1);
      finish();
      await page.getByText('任务已持久受理', { exact: false }).waitFor();
      expect(await page.getByRole('dialog').count()).toBe(0);
      expect(state.errors).toEqual([]);
    } finally {
      finish();
      await state.close();
    }
  });
  it('精修响应丢失后关闭不能丢弃原编号；重试原请求后才关闭', async () => {
    const state = await scene({ width: 320, visual: true, turns: 2, loseTask: true });
    const { page } = state;
    try {
      await page.getByRole('button', { name: '基本精修', exact: true }).last().click();
      await page.getByRole('button', { name: '提交一份任务', exact: true }).click();
      await page.getByRole('button', { name: '重试同一动作' }).waitFor();
      expect(await page.getByLabel('关闭窗口', { exact: true }).isDisabled()).toBe(true);
      await page.keyboard.press('Escape');
      expect(await page.getByRole('dialog').count()).toBe(1);
      await noOverflow(page);
      await page.getByRole('button', { name: '重试同一动作' }).click();
      await page.getByText('任务已持久受理', { exact: false }).waitFor();
      expect(await page.getByRole('dialog').count()).toBe(0);
      expect(state.taskMutations).toHaveLength(2);
      expect(state.taskMutations[0]).toEqual(state.taskMutations[1]);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it('精修仅明确放弃才丢弃重试；关闭不会再提交或取消已受理工作', async () => {
    const state = await scene({ width: 390, visual: true, turns: 2, loseTask: true });
    const { page } = state;
    try {
      await page.getByRole('button', { name: '基本精修', exact: true }).last().click();
      await page.getByRole('button', { name: '提交一份任务', exact: true }).click();
      const discard = page.getByRole('button', { name: '放弃重试并关闭（不撤销工作）' });
      await discard.waitFor();
      expect(await page.getByRole('dialog').textContent()).toContain('请先到任务列表核对');
      const writesBefore = state.requests.filter((r) => r.method === 'POST');
      await discard.click();
      expect(await page.getByRole('dialog').count()).toBe(0);
      expect(state.requests.filter((r) => r.method === 'POST')).toEqual(writesBefore);
      expect(state.taskMutations).toHaveLength(1);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it.each([320, 390])('桌面双侧栏缩到 %dpx 时只保留一个可操作抽屉', async (width) => {
    const state = await scene({ width: 1440, healthy: true });
    const { page } = state;
    try {
      await page.getByLabel('展开或收起主导航').click();
      await page.getByLabel('展开或收起对话历史').click();
      await page.setViewportSize({ width, height: 800 });
      await page.getByRole('dialog', { name: '对话历史' }).waitFor();
      expect(await page.getByRole('dialog').count()).toBe(1);
      await page.waitForFunction(() =>
        document.querySelector('#history-nav').contains(document.activeElement),
      );
      await noOverflow(page);
      await page.keyboard.press('Escape');
      expect(await page.getByRole('dialog').count()).toBe(0);
      expect(
        await page.getByLabel('展开或收起对话历史').evaluate((el) => el === document.activeElement),
      ).toBe(true);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it('UI-05：任务异常与恢复动作、提供方暂停禁用明确给出原因', async () => {
    const cap = structuredClone(capabilities);
    cap.image.availability = { available: false, reason: 'FREE_ACCESS_UNCONFIRMED' };
    const state = await scene({
      width: 390,
      height: 844,
      visual: true,
      turns: 2,
      capabilities: cap,
      taskStates: [
        'queued',
        'running',
        'saving',
        'submission_unknown',
        'needs_reconciliation',
        'failed',
        'canceled',
      ],
    });
    const { page } = state;
    try {
      await page.getByRole('button', { name: /^任务/ }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('button', { name: '取消排队任务' }).waitFor();
      expect(await dialog.getByRole('button', { name: '取消排队任务' }).count()).toBe(1);
      expect(
        await dialog.getByRole('button', { name: '续查／恢复下载（不重新生成）' }).count(),
      ).toBe(1);
      await capture(page, 'tasks-390-queued-saving');
      await dialog.getByText('是否受理尚不确定', { exact: true }).scrollIntoViewIfNeeded();
      await capture(page, 'tasks-390-unknown');
      expect(await dialog.getByRole('button', { name: '重新生成', exact: true }).count()).toBe(0);
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: '基本精修', exact: true }).last().click();
      await page.getByText('当前不可新增：', { exact: false }).waitFor();
      await page
        .locator('#refine-requirements')
        .filter({ hasText: '免费使用条件尚未核验' })
        .waitFor();
      expect(await page.locator('#refine-requirements').textContent()).toContain(
        '免费使用条件尚未核验',
      );
      expect(
        await page.getByRole('button', { name: '提交一份任务', exact: true }).isDisabled(),
      ).toBe(true);
      await capture(page, 'refine-390-provider-paused');
      expect(state.taskMutations).toEqual([]);
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
  });
  it('UI-06/07：合成本地视频真实可播放，登录入口不重复、人工找回可发现', async () => {
    const state = await scene({ width: 1440, height: 1000, visual: true, video: true, turns: 2 });
    try {
      const video = state.page.locator('video');
      await video.waitFor();
      await video.scrollIntoViewIfNeeded();
      await state.page.waitForFunction(() => document.querySelector('video')?.readyState >= 1);
      expect(
        await video.evaluate((el) => el.paused && !el.autoplay && el.controls && el.playsInline),
      ).toBe(true);
      await video.evaluate((el) => el.play());
      await state.page.waitForFunction(() => document.querySelector('video').currentTime > 0);
      await video.evaluate((el) => el.pause());
      await capture(state.page, 'desktop-1440-local-video');
      expect(await state.page.locator('.media-card').textContent()).toContain('非 AI 动态生成');
      expect(state.errors).toEqual([]);
    } finally {
      await state.close();
    }
    for (const width of [320, 390]) {
      const login = await scene({ width, height: 844, anonymous: true });
      try {
        await login.page.getByRole('heading', { name: '回到你的创作' }).waitFor();
        expect(await login.page.getByRole('button', { name: '登录', exact: true }).count()).toBe(1);
        for (const button of await login.page.getByRole('button').all()) {
          const rect = await button.boundingBox();
          expect(rect.width).toBeGreaterThanOrEqual(44);
          expect(rect.height).toBeGreaterThanOrEqual(44);
        }
        await noOverflow(login.page);
        await capture(login.page, `login-${width}`);
        await login.page.getByRole('button', { name: '登录', exact: true }).click();
        expect(
          await login.page
            .getByLabel('用户名', { exact: true })
            .evaluate((el) => getComputedStyle(el).borderTopColor),
        ).toBe('rgb(145, 63, 67)');
        expect(login.requests.filter((r) => r.method === 'POST')).toEqual([]);
        await capture(login.page, `login-${width}-invalid`);
        await login.page.getByRole('button', { name: '忘记密码', exact: true }).click();
        await login.page.getByText('没有邮箱或短信找回。', { exact: false }).waitFor();
        await noOverflow(login.page);
        await capture(login.page, `reset-${width}`);
        expect(login.errors).toEqual([]);
      } finally {
        await login.close();
      }
    }
  });
  it('批准快照逐字节不变', async () => {
    const data = await readFile(root + '/../docs/specs/assets/codex-wave-rail.approved.html');
    expect(createHash('sha256').update(data).digest('hex')).toBe(
      'be8f15d9510bfb6a2bd937409c142fa7473876c17f901a7e495228ece1189448',
    );
  });
});
