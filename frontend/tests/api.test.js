import { describe, it, expect, vi } from 'vitest';
import { createApi, contentPath, takeToken, retrySeconds, ApiError } from '../src/api.js';
import { useActions } from '../src/actions.js';
import { json, version } from './fixtures.js';

describe('网站安全与请求纪律', () => {
  it('匿名引导、登录轮换与修改请求均带同源 Cookie/CSRF，秘密不放 URL', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(json({ csrf_token: 'anonymous-token' }))
      .mockResolvedValueOnce(json({ csrf_token: 'login-token' }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const api = createApi(fetcher);
    const login = await api.request('/api/v1/auth/login', {
      method: 'POST',
      body: { username: 'synthetic', password: ' test password ' },
    });
    api.setCsrf(login.csrf_token);
    await api.request('/api/v1/auth/logout', { method: 'POST' });
    expect(fetcher.mock.calls[1][1].headers.get('X-CSRF-Token')).toBe('anonymous-token');
    expect(fetcher.mock.calls[2][1].headers.get('X-CSRF-Token')).toBe('login-token');
    for (const [url, options] of fetcher.mock.calls) {
      expect(url).not.toContain('password');
      expect(options.credentials).toBe('same-origin');
      expect(options.redirect).toBe('error');
      expect(options.cache).toBe('no-store');
    }
    expect(JSON.parse(fetcher.mock.calls[1][1].body).password).toBe(' test password ');
  });
  it('202 之后正文截断仍是未确认受理，保留原动作可重试', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('{"id":', { status: 202 }));
    const api = createApi(fetcher);
    api.setCsrf('csrf');
    await expect(
      api.request('/api/v1/tasks', { method: 'POST', body: {}, key: 'same-key' }),
    ).rejects.toMatchObject({ code: 'NETWORK_ERROR', retryable: true });
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it('multipart 不手写 boundary，保留幂等键', async () => {
    const fetcher = vi.fn().mockResolvedValue(json({}));
    const api = createApi(fetcher);
    api.setCsrf('csrf');
    const body = new FormData();
    body.append('file', new Blob(['test'], { type: 'image/png' }), 'synthetic.png');
    await api.request('/api/v1/uploads', { method: 'POST', key: 'same-action', body });
    expect(fetcher.mock.calls[0][1].headers.has('Content-Type')).toBe(false);
    expect(fetcher.mock.calls[0][1].body).toBe(body);
    expect(fetcher.mock.calls[0][1].headers.get('Idempotency-Key')).toBe('same-action');
  });
  it('401 清理、403 不盲目重试、HTML 不透传', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ code: 'AUTH_REQUIRED', detail: '已失效' }), { status: 401 }),
      )
      .mockResolvedValueOnce(new Response('<html>private proxy details</html>', { status: 503 }));
    const api = createApi(fetcher),
      revoke = vi.fn();
    api.onUnauthorized(revoke);
    await expect(api.request('/api/v1/auth/me')).rejects.toMatchObject({ code: 'AUTH_REQUIRED' });
    expect(revoke).toHaveBeenCalledOnce();
    await expect(api.request('/api/v1/capabilities')).rejects.toMatchObject({
      code: 'HTTP_ERROR',
      retryable: false,
    });
  });
  it('注销终止在途读取，晚到的其他账号资源不可返回', async () => {
    let finish;
    const api = createApi(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = api.request('/api/v1/conversations');
    api.clear();
    finish(json({ items: ['private'] }));
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });
  it.each([
    'https://private.invalid/api/v1/tasks',
    '//private.invalid',
    '/api/v1/../secret',
    '/api/v1/\\secret',
  ])('拒绝非受控请求 %s', async (path) => {
    const fetcher = vi.fn(),
      api = createApi(fetcher);
    await expect(api.request(path)).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('只构造精确受保护下载链接，不使用外站 content_url', () => {
    const v = { ...version(), content_url: 'javascript:alert(1)' };
    expect(contentPath(v, true)).toBe(
      `/api/v1/artifacts/${v.artifact_id}/versions/${v.id}/content?download=true`,
    );
    expect(contentPath({ ...v, id: '../secret' })).toBeNull();
  });
  it('启动同步清除 fragment，注册与重置路径分开', () => {
    const history = { replaceState: vi.fn() };
    expect(
      takeToken(
        { hash: '#token=synthetic_token', pathname: '/reset-password', search: '' },
        history,
      ),
    ).toEqual({ mode: 'reset', token: 'synthetic_token' });
    expect(history.replaceState).toHaveBeenCalledWith(null, '', '/reset-password');
  });
  it('429 等待跨界面操作生效，仍允许读取作品；到期才新增', async () => {
    vi.useFakeTimers();
    try {
      const fetcher = vi
        .fn()
        .mockResolvedValueOnce(
          new Response(
            JSON.stringify({ code: 'QUEUE_BACKPRESSURE', detail: '暂缓', retryable: true }),
            { status: 429, headers: { 'Retry-After': '120' } },
          ),
        )
        .mockImplementation(async () => json({ items: [] }));
      const api = createApi(fetcher);
      api.setCsrf('csrf');
      await expect(
        api.request('/api/v1/tasks', { method: 'POST', body: {} }),
      ).rejects.toMatchObject({ retryAfter: 120 });
      await expect(
        api.request('/api/v1/conversations', { method: 'POST', body: {} }),
      ).rejects.toMatchObject({ status: 429 });
      await api.request('/api/v1/artifacts');
      expect(fetcher).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(120000);
      await api.request('/api/v1/tasks', { method: 'POST', body: {} });
      expect(fetcher).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
  it('新增操作的 429 等待不拦截注销，仍保留 CSRF 和其他写入的等待', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ code: 'QUEUE_BACKPRESSURE', retryable: true }), {
          status: 429,
          headers: { 'Retry-After': '120' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const api = createApi(fetcher);
    api.setCsrf('synthetic-csrf');
    await expect(api.request('/api/v1/tasks', { method: 'POST', body: {} })).rejects.toMatchObject({
      status: 429,
    });
    await expect(api.request('/api/v1/auth/logout', { method: 'POST' })).resolves.toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1][1].headers.get('X-CSRF-Token')).toBe('synthetic-csrf');
    await expect(api.request('/api/v1/tasks', { method: 'POST', body: {} })).rejects.toMatchObject({
      status: 429,
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('Retry-After 支持秒和日期', () => {
    expect(retrySeconds('120')).toBe(120);
    expect(retrySeconds('bad')).toBe(0);
    expect(retrySeconds('Tue, 08 Sep 2026 08:02:00 GMT', Date.parse('2026-09-08T08:00:00Z'))).toBe(
      120,
    );
  });
});

describe('明确动作与不确定结果', () => {
  it('快速双击只执行一次，网络失败只手动重试同一闭包', async () => {
    const actions = useActions();
    let fail;
    const job = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((_, reject) => {
            fail = reject;
          }),
      )
      .mockResolvedValue({ id: 'same' });
    const first = actions.execute(job, { repeatable: true });
    await actions.execute(job, { repeatable: true });
    expect(job).toHaveBeenCalledOnce();
    fail(new ApiError({ code: 'NETWORK_ERROR', status: 0, retryable: true }));
    await first;
    expect(job).toHaveBeenCalledOnce();
    await actions.retry.value();
    expect(job).toHaveBeenCalledTimes(2);
  });
  it('其他界面动作不能清除尚未核对的原请求重试', async () => {
    const actions = useActions();
    const original = vi
      .fn()
      .mockRejectedValueOnce(new ApiError({ code: 'NETWORK_ERROR', status: 0, retryable: true }))
      .mockResolvedValue({ id: 'same' });
    const other = vi.fn();
    await actions.execute(original, { repeatable: true });
    const retry = actions.retry.value;
    await actions.execute(other);
    expect(other).not.toHaveBeenCalled();
    expect(actions.retry.value).toBe(retry);
    await actions.retry.value();
    expect(original).toHaveBeenCalledTimes(2);
    expect(actions.retry.value).toBeNull();
    await actions.execute(other);
    expect(other).toHaveBeenCalledOnce();
  });
  it.each(['VERSION_CONFLICT', 'REFERENCE_CONFIRMATION_REQUIRED', 'RECONCILIATION_REQUIRED'])(
    '%s 不换键重做',
    async (code) => {
      const actions = useActions();
      await actions.execute(
        () => Promise.reject(new ApiError({ code, status: 409, retryable: false })),
        { repeatable: true },
      );
      expect(actions.retry.value).toBeNull();
    },
  );
  it('等待期间点击手动重试也不发请求', async () => {
    vi.useFakeTimers();
    try {
      const actions = useActions(),
        job = vi
          .fn()
          .mockRejectedValueOnce(
            new ApiError({ code: 'QUEUE_BACKPRESSURE', status: 429, retryable: true }, 120),
          )
          .mockResolvedValue({});
      await actions.execute(job, { repeatable: true });
      await actions.retry.value();
      expect(job).toHaveBeenCalledOnce();
      await vi.advanceTimersByTimeAsync(120000);
      await actions.retry.value();
      expect(job).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
  it('一次性签发失败不暴露重试入口', async () => {
    const actions = useActions();
    await actions.execute(() =>
      Promise.reject(new ApiError({ code: 'NETWORK_ERROR', status: 0, retryable: true })),
    );
    expect(actions.retry.value).toBeNull();
  });
});
