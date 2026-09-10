import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtemp, rm, writeFile, readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('../..', import.meta.url));
const python = root + '/.venv/bin/python';
const password = 'Synthetic E2E Password 123!';
let temporary,
  server,
  browser,
  origin,
  stderr = '';
const contexts = [],
  acknowledgments = new Map();
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms).unref());
async function until(fn, message, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const value = await fn();
    if (value) return value;
    await delay(100);
  }
  throw new Error(message + '\n' + stderr);
}

before(
  async () => {
    await mkdir(root + '/backend/var', { recursive: true });
    temporary = await mkdtemp(root + '/backend/var/e2e-');
    execFileSync(
      '/usr/bin/openssl',
      [
        'req',
        '-x509',
        '-newkey',
        'rsa:2048',
        '-nodes',
        '-keyout',
        temporary + '/key.pem',
        '-out',
        temporary + '/cert.pem',
        '-days',
        '1',
        '-subj',
        '/CN=127.0.0.1',
        '-addext',
        'subjectAltName=IP:127.0.0.1',
      ],
      { stdio: 'ignore' },
    );
    server = spawn(python, [root + '/backend/tests/e2e_server.py', temporary], {
      cwd: root,
      env: { PATH: process.env.PATH, PYTHONUNBUFFERED: '1' },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    server.stderr.on('data', (data) => {
      stderr += data.toString();
    });
    let buffer = '';
    server.stdout.on('data', (data) => {
      buffer += data.toString();
      while (buffer.includes('\n')) {
        const i = buffer.indexOf('\n'),
          line = buffer.slice(0, i);
        buffer = buffer.slice(i + 1);
        const value = JSON.parse(line);
        if (value.origin) origin = value.origin;
        if (value.ack) acknowledgments.get(value.ack)?.();
      }
    });
    await until(() => {
      if (server.exitCode !== null) throw new Error('联调服务提前退出：' + stderr);
      return origin;
    }, '联调服务启动超时');
    browser = await chromium.launch({
      executablePath: '/usr/bin/google-chrome',
      headless: true,
      args: ['--disable-background-networking'],
    });
  },
  { timeout: 30000 },
);

after(async () => {
  for (const context of contexts) await context.close();
  await browser?.close();
  if (server && server.exitCode === null) {
    const exited = once(server, 'exit');
    server.stdin.end();
    await Promise.race([
      exited,
      delay(15000).then(() => {
        if (server.exitCode === null) throw new Error('本轮服务没有正常退出：' + stderr);
      }),
    ]);
  }
  assert.equal(server?.exitCode, 0, stderr);
  if (temporary) await rm(temporary, { recursive: true, force: true });
});

async function context(width = 1100) {
  const c = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport: { width, height: 820 },
    acceptDownloads: true,
  });
  // 仅拒绝非本轮来源，不替换任何 API 响应。证书例外限此临时 context。
  await c.route('**/*', (route) =>
    new URL(route.request().url()).origin === origin ? route.continue() : route.abort(),
  );
  contexts.push(c);
  c.setDefaultTimeout(10000);
  const page = await c.newPage();
  page.setDefaultTimeout(10000);
  await until(async () => {
    try {
      return (await c.request.get(origin + '/api/v1/health/ready')).status() === 200;
    } catch {
      return false;
    }
  }, '真实 API 未就绪');
  await page.goto(origin);
  return { c, page };
}

async function login(page, username = 'e2e_owner', secret = password) {
  await page.getByLabel('用户名', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(secret);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.getByLabel('创作需求', { exact: true }).waitFor();
}

async function request(page, path, method = 'GET', body, key, extra = {}) {
  return page.evaluate(
    async ({ path, method, body, key, extra }) => {
      const headers = { ...extra };
      if (method !== 'GET' && method !== 'HEAD') {
        const csrf = await (await fetch('/api/v1/auth/csrf')).json();
        headers['X-CSRF-Token'] = csrf.csrf_token;
        headers['Content-Type'] = 'application/json';
        if (key) headers['Idempotency-Key'] = key;
      }
      const r = await fetch('/api/v1' + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const text = await r.text();
      return {
        status: r.status,
        headers: Object.fromEntries(r.headers),
        body: text && r.headers.get('content-type')?.includes('json') ? JSON.parse(text) : text,
      };
    },
    { path, method, body, key, extra },
  );
}
async function ok(page, path, method = 'GET', body, key, status = 200) {
  const r = await request(page, path, method, body, key);
  assert.equal(r.status, status, `${method} ${path}: ${JSON.stringify(r.body)}`);
  return r.body;
}
async function command(action) {
  const id = crypto.randomUUID();
  const ack = new Promise((resolve) => acknowledgments.set(id, resolve));
  server.stdin.write(JSON.stringify({ action, id }) + '\n');
  await Promise.race([
    ack,
    delay(10000).then(() => {
      throw new Error('worker 控制超时');
    }),
  ]);
  acknowledgments.delete(id);
}
async function send(page, text) {
  await page.getByLabel('创作需求', { exact: true }).fill(text);
  await page.getByLabel('发送消息', { exact: true }).click();
  await page.getByText('合成回复：' + text, { exact: true }).waitFor();
}
async function navigation(page, name) {
  const button = page.getByRole('button', { name, exact: true });
  if (!(await button.isVisible()))
    await page.getByRole('button', { name: '展开或收起主导航', exact: true }).click();
  await button.click();
}
async function newConversation(page) {
  const previous = new URL(page.url()).searchParams.get('conversation');
  if (await page.getByRole('button', { name: '新建对话', exact: true }).isVisible())
    await page.getByRole('button', { name: '新建对话', exact: true }).click();
  else await navigation(page, '新建创作');
  return until(async () => {
    const cid = new URL(page.url()).searchParams.get('conversation');
    return cid && cid !== previous ? cid : false;
  }, '没有新对话编号');
}
async function tasks(page, cid) {
  return (await ok(page, '/tasks?conversation_id=' + cid)).items;
}
async function completed(page, cid, kind) {
  return until(
    async () => (await tasks(page, cid)).find((t) => t.kind === kind && t.status === 'completed'),
    '任务没有真实完成',
  );
}
async function layout(page, width) {
  await page.setViewportSize({ width, height: 820 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const sendButton = page.getByLabel('发送消息', { exact: true });
  const r = await sendButton.boundingBox();
  assert.ok(r && r.x >= 0 && r.x + r.width <= width && r.y + r.height <= 820);
}
function ledger() {
  return JSON.parse(
    execFileSync(
      python,
      [
        '-c',
        'import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); print(json.dumps([json.loads(r[0])["id"] for r in c.execute("SELECT task FROM submissions")]))',
        temporary + '/provider.sqlite3',
      ],
      { encoding: 'utf8' },
    ),
  );
}

test(
  '真实 HTTPS API＋浏览器＋独立假提供方：账号、创作、恢复和安全闭环',
  { timeout: 180000 },
  async (t) => {
    const owner = await context();
    await login(owner.page);
    const cookies = await owner.c.cookies();
    const session = cookies.find((c) => c.name === '__Host-omniflow_session');
    assert.ok(
      session?.secure && session.httpOnly && session.sameSite === 'Lax' && session.path === '/',
    );
    assert.equal(
      await owner.page.evaluate(() => document.cookie.includes('omniflow_session')),
      false,
    );
    const invitation = await ok(owner.page, '/admin/invitations', 'POST', {}, undefined, 201);
    const alice = await context(390),
      bob = await context(320);
    const pageErrors = [];
    alice.page.on('pageerror', (e) => pageErrors.push(e.message));
    let cid, uploaded, imageTask, videoTask, videoVersion;

    await t.test('一次性邀请注册、Secure Cookie 和 CSRF 不降级', async () => {
      await alice.page.goto(invitation.invite_url);
      await alice.page.getByRole('button', { name: '校验链接', exact: true }).click();
      await alice.page.getByText(/链接有效至/).waitFor();
      assert.equal(new URL(alice.page.url()).hash, '');
      await alice.page.getByLabel('用户名', { exact: true }).fill('e2e_alice');
      await alice.page.getByLabel('密码', { exact: true }).fill(password);
      await alice.page.getByRole('button', { name: '注册并登录' }).click();
      await alice.page.getByLabel('创作需求', { exact: true }).waitFor();
      const token = new URL(invitation.invite_url).hash.slice(1);
      assert.equal(
        (
          await request(bob.page, '/auth/invitations/validate', 'POST', {
            token: new URLSearchParams(token).get('token'),
          })
        ).status,
        400,
      );
      const bad = await alice.page.evaluate(async () => {
        const r = await fetch('/api/v1/conversations', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Idempotency-Key': 'bad-csrf-test',
          },
          body: '{}',
        });
        return { status: r.status, body: await r.json() };
      });
      assert.equal(bad.status, 403);
      assert.equal(bad.body.code, 'CSRF_INVALID');
      const otherInvite = await ok(owner.page, '/admin/invitations', 'POST', {}, undefined, 201);
      await ok(
        bob.page,
        '/auth/register',
        'POST',
        {
          username: 'e2e_bob',
          password,
          invite_token: new URLSearchParams(new URL(otherInvite.invite_url).hash.slice(1)).get(
            'token',
          ),
        },
        undefined,
        201,
      );
      await bob.page.reload();
      await bob.page.getByLabel('创作需求', { exact: true }).waitFor();
    });

    await t.test('上传→文字流与工具生图→固定版本确认→关页面及 worker 重启→视频下载', async () => {
      cid = await newConversation(alice.page);
      const uploadResponse = alice.page.waitForResponse(
        (r) => r.url().endsWith('/api/v1/uploads') && r.request().method() === 'POST',
      );
      await alice.page
        .locator('input[type=file]')
        .setInputFiles(root + '/frontend/tests/assets/visual-portrait.png');
      uploaded = (await (await uploadResponse).json()).version;
      await alice.page
        .getByText('图片已保存为不可变版本，不是生成结果。', { exact: true })
        .waitFor();
      await send(alice.page, '请生成一张图片：合成蓝色方块');
      imageTask = await completed(alice.page, cid, 'image');
      assert.equal(imageTask.execution_engine, 'agnes-image-2.5-flash');
      // 真正的 SSE 将作品卡片送进当前页面，不靠 reload 或响应拦截。
      await alice.page.locator('figure').filter({ hasText: 'AI 图片' }).first().waitFor();
      const snap = await ok(alice.page, `/conversations/${cid}/snapshot`);
      const image = snap.artifact_versions.find((v) => v.id === imageTask.output_version_ids[0]);
      assert.equal(image.width, 12);
      const imageCard = alice.page
        .locator('figure')
        .filter({ has: alice.page.locator(`a[href*="${image.id}"]`) });
      await imageCard.getByRole('button', { name: '选为参考图', exact: true }).click();
      const confirmResponse = alice.page.waitForResponse(
        (r) => r.url().endsWith('/reference-confirmations') && r.request().method() === 'POST',
      );
      await alice.page.getByRole('button', { name: /确认.*做视频/ }).click();
      const proof = await (await confirmResponse).json();
      assert.equal(proof.version_id, image.id);
      await command('stop-media');
      await send(alice.page, '请用已确认图片生成视频：轻轻移动');
      videoTask = (await tasks(alice.page, cid)).find((task) => task.kind === 'ai_video');
      assert.equal(videoTask.status, 'queued');
      assert.equal(videoTask.requested_parameters.reference_confirmation_id, proof.id);
      // 第二个浏览器视图看同一事实，不产生新任务；关页面不取消排队任务。
      const second = await alice.c.newPage();
      await second.goto(alice.page.url());
      await second
        .getByText('合成回复：请用已确认图片生成视频：轻轻移动', { exact: true })
        .waitFor();
      await second.close();
      const savedURL = alice.page.url();
      await alice.page.close();
      await command('start-media');
      alice.page = await alice.c.newPage();
      await alice.page.goto(savedURL);
      videoTask = await completed(alice.page, cid, 'ai_video');
      const restored = await ok(alice.page, `/conversations/${cid}/snapshot`);
      videoVersion = restored.artifact_versions.find(
        (v) => v.id === videoTask.output_version_ids[0],
      );
      assert.equal(videoVersion.duration_seconds, 1.25);
      assert.equal(videoTask.requested_parameters.seconds, 4);
      const card = alice.page
        .locator('figure')
        .filter({ has: alice.page.locator(`a[href*="${videoVersion.id}"]`) });
      await card.locator('video').waitFor();
      await until(
        () => card.locator('video').evaluate((v) => v.readyState >= 1 && v.duration === 1.25),
        '视频元数据未加载',
      );
      const downloaded = alice.page.waitForEvent('download');
      await card.getByRole('link', { name: '下载此版本' }).click();
      assert.equal(await (await downloaded).failure(), null);
      const url = videoVersion.content_url.replace('/api/v1', '');
      const head = await request(alice.page, url, 'HEAD');
      assert.equal(head.status, 200);
      assert.equal(Number(head.headers['content-length']), videoVersion.byte_size);
      assert.equal(head.body, '');
      const range = await request(alice.page, url, 'GET', undefined, undefined, {
        Range: 'bytes=0-7',
      });
      assert.equal(range.status, 206);
      assert.equal(range.headers['content-range'], `bytes 0-7/${videoVersion.byte_size}`);
      for (const width of [320, 390, 1100]) await layout(alice.page, width);
      assert.deepEqual(ledger().sort(), [imageTask.id, videoTask.id].sort());
    });

    await t.test('同用户 A/B/A、越权 GET/HEAD/Range、幂等和明确精修新增版本', async () => {
      const originalURL = alice.page.url();
      const otherCid = await newConversation(alice.page);
      assert.notEqual(otherCid, cid);
      await send(alice.page, '随机代号 E2E_B_独立上下文');
      const isolated = await ok(alice.page, `/conversations/${otherCid}/snapshot`);
      assert.equal(isolated.messages.length, 2);
      assert.ok(isolated.messages.every((m) => !m.content.includes('蓝色方块')));
      await alice.page.goto(originalURL);
      await alice.page
        .getByText('合成回复：请生成一张图片：合成蓝色方块', { exact: true })
        .waitFor();
      await alice.c.setOffline(true);
      await delay(250);
      await alice.c.setOffline(false);
      await send(alice.page, '回到 A，保持本段独立');
      const identity = await ok(alice.page, '/auth/me');
      const aFrames = await readFile(
        `${temporary}/cli/${identity.id}/${cid}/workspace/received.jsonl`,
        'utf8',
      );
      const bFrames = await readFile(
        `${temporary}/cli/${identity.id}/${otherCid}/workspace/received.jsonl`,
        'utf8',
      );
      assert.equal(aFrames.trim().split('\n').length, 3);
      assert.equal(bFrames.trim().split('\n').length, 1);
      assert.ok(!aFrames.includes('E2E_B_独立上下文') && !bFrames.includes('蓝色方块'));
      const mappings = JSON.parse(
        execFileSync(
          python,
          [
            '-c',
            'import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); print(json.dumps(c.execute("SELECT cli_session_id FROM conversations WHERE id IN (?,?)", sys.argv[2:]).fetchall()))',
            temporary + '/data/omniflow.sqlite3',
            cid,
            otherCid,
          ],
          { encoding: 'utf8' },
        ),
      );
      assert.equal(mappings.length, 2);
      assert.ok(mappings[0][0] && mappings[1][0] && mappings[0][0] !== mappings[1][0]);
      for (const path of [
        `/conversations/${cid}`,
        `/tasks/${videoTask.id}`,
        `/artifacts/${videoVersion.artifact_id}`,
      ]) {
        assert.equal((await request(bob.page, path)).status, 404);
      }
      for (const method of ['GET', 'HEAD']) {
        const r = await request(
          bob.page,
          videoVersion.content_url.replace('/api/v1', ''),
          method,
          undefined,
          undefined,
          { Range: 'bytes=0-7' },
        );
        assert.equal(r.status, 404);
        assert.equal(r.headers['content-range'], undefined);
      }
      assert.equal((await request(bob.page, '/admin/users')).status, 403);
      const key = crypto.randomUUID();
      const body = { title: '幂等测试' };
      const first = await request(alice.page, '/conversations', 'POST', body, key);
      const again = await request(alice.page, '/conversations', 'POST', body, key);
      assert.equal(first.status, 201);
      assert.deepEqual(again.body, first.body);
      assert.equal(again.headers['idempotency-replayed'], 'true');
      assert.equal(
        (await request(alice.page, '/conversations', 'POST', { title: '不同语义' }, key)).status,
        409,
      );
      // 真正操作精修窗口，生成图片新版本，不替换已完成视频绑定的旧参考。
      const imageVersionId = imageTask.output_version_ids[0];
      // 同一版本分别作为前轮产物和后轮附件出现，选择最早产物卡片。
      const card = alice.page
        .locator('figure')
        .filter({ has: alice.page.locator(`a[href*="${imageVersionId}"]`) })
        .first();
      await card.getByRole('button', { name: '基本精修', exact: true }).click();
      const dialog = alice.page.getByRole('dialog');
      await until(
        async () => (await dialog.locator('form').getAttribute('aria-busy')) === 'false',
        '精修参数尚未读取完成',
      );
      await dialog.getByLabel('修改／创作要求', { exact: true }).fill('合成新版蓝色方块');
      await dialog.getByLabel('画幅').selectOption('9:16');
      await dialog.getByLabel('尺寸档位').selectOption('1K');
      const submitted = alice.page.waitForResponse(
        (r) => r.url().endsWith('/api/v1/tasks') && r.request().method() === 'POST',
      );
      await dialog.getByRole('button', { name: /提交.*任务/ }).click();
      const editTask = await (await submitted).json();
      await until(
        async () => (await ok(alice.page, '/tasks/' + editTask.id)).status === 'completed',
        '精修没有完成',
      );
      const versions = (
        await ok(
          alice.page,
          `/artifacts/${editTask.requested_parameters.target_artifact_id}/versions`,
        )
      ).items;
      assert.equal(versions.length, 2);
      assert.equal(versions[0].parent_version_id, imageVersionId);
      const oldTask = await ok(alice.page, '/tasks/' + videoTask.id);
      assert.equal(
        oldTask.requested_parameters.reference_confirmation_id,
        videoTask.requested_parameters.reference_confirmation_id,
      );
      assert.equal(ledger().filter((id) => id === editTask.id).length, 1);
    });

    await t.test('下载失败只恢复原结果，提交未知不再生成，退出撤销旧流与账号访问', async () => {
      await writeFile(temporary + '/media-mode', 'download_error');
      const recoveryKey = crypto.randomUUID();
      const recoveryBody = {
        conversation_id: cid,
        kind: 'image',
        prompt: '合成恢复测试',
        aspect_ratio: '1:1',
        size_tier: '1K',
      };
      const task = await ok(alice.page, '/tasks', 'POST', recoveryBody, recoveryKey, 202);
      const replay = await request(alice.page, '/tasks', 'POST', recoveryBody, recoveryKey);
      assert.equal(replay.status, 202);
      assert.equal(replay.body.id, task.id);
      assert.equal(replay.headers['idempotency-replayed'], 'true');
      await until(async () => {
        const current = await ok(alice.page, '/tasks/' + task.id);
        return current.status === 'saving' && current.can_recover;
      }, '没有保留下载失败事实');
      await command('stop-media');
      await writeFile(temporary + '/media-mode', 'ok');
      const restored = await ok(
        alice.page,
        '/tasks/' + task.id + '/recover',
        'POST',
        undefined,
        undefined,
        202,
      );
      assert.equal(restored.id, task.id);
      await command('start-media');
      await until(
        async () => (await ok(alice.page, '/tasks/' + task.id)).status === 'completed',
        '恢复原文件失败',
      );
      assert.equal(ledger().filter((id) => id === task.id).length, 1);
      await writeFile(temporary + '/media-mode', 'lost_response');
      const unknown = await ok(
        alice.page,
        '/tasks',
        'POST',
        {
          conversation_id: cid,
          kind: 'image',
          prompt: '合成未知提交',
          aspect_ratio: '1:1',
          size_tier: '1K',
        },
        crypto.randomUUID(),
        202,
      );
      await until(
        async () => (await ok(alice.page, '/tasks/' + unknown.id)).status === 'submission_unknown',
        '未如实标记未知提交',
      );
      assert.equal(
        (await request(alice.page, '/tasks/' + unknown.id + '/recover', 'POST')).status,
        409,
      );
      await command('stop-media');
      await writeFile(temporary + '/media-mode', 'ok');
      await command('start-media');
      assert.equal((await ok(alice.page, '/tasks/' + unknown.id)).status, 'submission_unknown');
      assert.equal(ledger().filter((id) => id === unknown.id).length, 1);
      const watcher = await alice.c.newPage();
      await watcher.goto(alice.page.url());
      await watcher.getByText('合成回复：请生成一张图片：合成蓝色方块', { exact: true }).waitFor();
      await navigation(alice.page, '账号');
      await alice.page.getByRole('button', { name: '退出登录', exact: true }).click();
      await alice.page.getByRole('heading', { name: '回到你的创作' }).waitFor();
      await watcher.getByRole('heading', { name: '回到你的创作' }).waitFor();
      assert.equal((await request(watcher, '/auth/me')).status, 401);
      assert.equal(
        (await request(watcher, videoVersion.content_url.replace('/api/v1', ''))).status,
        401,
      );
      assert.deepEqual(pageErrors, []);
      await watcher.close();
    });
  },
);
