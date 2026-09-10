export class ApiError extends Error {
  constructor(problem, retryAfter = null) {
    super(problem.detail || '请求未完成');
    Object.assign(this, problem);
    this.retryAfter = retryAfter;
  }
}

export function retrySeconds(value, now = Date.now()) {
  if (!value) return 0;
  if (/^\d+$/.test(value)) return Number(value);
  const time = Date.parse(value);
  return Number.isFinite(time) ? Math.max(0, Math.ceil((time - now) / 1000)) : 0;
}

export function createApi(fetcher = (...args) => fetch(...args)) {
  let csrf = null;
  let epoch = new AbortController();
  let onUnauthorized = () => {};
  let bootstrap = null;
  let writeNotBefore = 0,
    pausedProblem = null;

  async function request(path, { method = 'GET', body, key, signal, raw = false } = {}) {
    if (!path.startsWith('/api/v1/') || path.includes('..') || path.includes('\\')) {
      throw new Error('只允许受保护的同源接口');
    }
    // 暂缓新增不能妨碍用户撤销登录；精确豁免注销，仍走下方 Cookie／CSRF 校验。
    const logout = method === 'POST' && path === '/api/v1/auth/logout';
    if (method !== 'GET' && method !== 'HEAD' && !logout && Date.now() < writeNotBefore) {
      throw new ApiError(pausedProblem, Math.ceil((writeNotBefore - Date.now()) / 1000));
    }
    const sessionSignal = epoch.signal;
    const combined = signal ? AbortSignal.any([signal, sessionSignal]) : sessionSignal;
    const headers = new Headers({ Accept: raw ? '*/*' : 'application/json' });
    if (method !== 'GET' && method !== 'HEAD') {
      if (!csrf) await getCsrf();
      combined.throwIfAborted();
      headers.set('X-CSRF-Token', csrf);
    }
    if (key) headers.set('Idempotency-Key', key);
    if (body !== undefined && !(body instanceof FormData)) {
      headers.set('Content-Type', 'application/json');
      body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetcher(path, {
        method,
        headers,
        body,
        signal: combined,
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
        referrerPolicy: 'no-referrer',
      });
      combined.throwIfAborted();
    } catch (error) {
      if (combined.aborted) throw combined.reason;
      throw new ApiError({
        code: 'NETWORK_ERROR',
        status: 0,
        detail: '连接中断，未确认是否受理。请先查看状态；重试同一动作不会换新编号。',
        retryable: true,
      });
    }
    if (!response.ok) {
      let problem;
      try {
        problem = await response.json();
      } catch {
        /* 不显示代理 HTML 或原始响应 */
      }
      combined.throwIfAborted();
      const error = new ApiError(
        problem?.code
          ? problem
          : {
              status: response.status,
              code: 'HTTP_ERROR',
              detail: '服务暂未返回有效结果，请稍后检查。',
              retryable: false,
            },
        retrySeconds(response.headers.get('Retry-After')),
      );
      error.status = response.status;
      if (response.status === 429 && error.retryAfter > 0) {
        writeNotBefore = Math.max(writeNotBefore, Date.now() + error.retryAfter * 1000);
        pausedProblem = {
          code: error.code,
          status: 429,
          detail: '服务要求暂缓操作，请等待后重试。已有作品仍可读取。',
          retryable: error.retryable,
        };
      }
      if (error.code === 'AUTH_REQUIRED' || error.code === 'ACCOUNT_DISABLED') onUnauthorized();
      if (error.code === 'CSRF_INVALID') csrf = null;
      throw error;
    }
    if (raw) return response;
    if (response.status === 204) return null;
    try {
      const data = await response.json();
      combined.throwIfAborted();
      return data;
    } catch {
      combined.throwIfAborted();
      throw new ApiError({
        code: 'NETWORK_ERROR',
        status: 0,
        detail: '服务响应不完整，尚不能确认受理结果。请核对状态，或使用原编号重试同一动作。',
        retryable: true,
      });
    }
  }
  async function getCsrf() {
    if (!bootstrap)
      bootstrap = request('/api/v1/auth/csrf')
        .then((data) => {
          csrf = data.csrf_token;
        })
        .finally(() => {
          bootstrap = null;
        });
    return bootstrap;
  }
  return {
    request,
    getCsrf,
    setCsrf(value) {
      csrf = value;
    },
    onUnauthorized(fn) {
      onUnauthorized = fn;
    },
    clear() {
      epoch.abort();
      epoch = new AbortController();
      csrf = null;
      bootstrap = null;
      writeNotBefore = 0;
      pausedProblem = null;
    },
  };
}

export const api = createApi();
export const actionKey = () => crypto.randomUUID();
export const query = (path, params) => {
  const values = Object.entries(params).filter(
    ([, value]) => value !== null && value !== undefined && value !== '',
  );
  return '/api/v1' + path + (values.length ? '?' + new URLSearchParams(values) : '');
};

// 不直接信任 content_url；只组装精确作品版本路径，防止主动内容或外站取图。
export function contentPath(version, download = false) {
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!uuid.test(version?.id) || !uuid.test(version?.artifact_id)) return null;
  return `/api/v1/artifacts/${version.artifact_id}/versions/${version.id}/content${download ? '?download=true' : ''}`;
}

export function takeToken(location = window.location, history = window.history) {
  const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
  // 页面启动的第一次同步动作；链接凭据不进路由、持久存储或日志。
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  return {
    token,
    mode:
      location.pathname === '/register'
        ? 'register'
        : location.pathname === '/reset-password'
          ? 'reset'
          : 'login',
  };
}
