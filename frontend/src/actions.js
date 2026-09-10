import { ref, shallowRef } from 'vue';

// 每个明确动作的闭包持有原 body 与幂等键。网络故障只允许手动重试，不自动改键。
export function useActions() {
  const busy = ref(false),
    error = shallowRef(null),
    retry = shallowRef(null);
  let generation = 0,
    notBefore = 0,
    pendingWork = null;
  async function execute(work, { repeatable = false } = {}) {
    if (busy.value || Date.now() < notBefore || (retry.value && work !== pendingWork)) return;
    const current = generation;
    pendingWork = null;
    busy.value = true;
    error.value = null;
    retry.value = null;
    try {
      return await work();
    } catch (e) {
      if (current !== generation || e.name === 'AbortError') return;
      error.value = e;
      notBefore = Date.now() + (e.retryAfter || 0) * 1000;
      if (
        repeatable &&
        (e.code === 'NETWORK_ERROR' || (e.retryable && (e.status === 429 || e.status >= 500)))
      ) {
        const availableAt = Date.now() + (e.retryAfter || 0) * 1000;
        pendingWork = work;
        retry.value = async () => {
          if (Date.now() >= availableAt) return execute(work, { repeatable });
        };
      }
    } finally {
      if (current === generation) busy.value = false;
    }
  }
  function clear() {
    generation++;
    notBefore = 0;
    pendingWork = null;
    busy.value = false;
    error.value = null;
    retry.value = null;
  }
  return { busy, error, retry, execute, clear };
}

export const statusName = (status) =>
  ({
    queued: '排队中',
    submitting: '正在提交',
    running: '处理中',
    saving: '正在保存',
    completed: '已完成',
    failed: '失败',
    canceled: '已取消',
    stopping: '正在停止',
    interrupted: '已中断',
    submission_unknown: '是否受理尚不确定',
    needs_reconciliation: '等待核对',
    streaming: '正在回复',
    active: '启用',
    disabled: '停用',
    used: '已使用',
    revoked: '已撤销',
    expired: '已过期',
  })[status] || status;
export const engineName = (engine) =>
  engine === 'local-ffmpeg'
    ? '本地运镜 · 非 AI 动态生成'
    : engine || '上传／直接保存 · 未标注 AI 来源';
export const mediaName = (engine, type) =>
  engine === 'local-ffmpeg'
    ? '本地运镜 · 非 AI 动态生成'
    : engine?.startsWith('agnes-video')
      ? 'AI 视频'
      : engine?.startsWith('agnes-image')
        ? 'AI 图片'
        : type === 'text/plain'
          ? '文案'
          : '上传／直接保存';
export const fileSize = (bytes) =>
  bytes >= 1048576
    ? `${Number((bytes / 1048576).toFixed(2))} MB`
    : `${Number((bytes / 1024).toFixed(2))} KB`;
export const reasonName = (code) =>
  ({
    PROVIDER_UNAVAILABLE: '提供方尚不可用',
    FREE_ACCESS_UNCONFIRMED: '免费使用条件尚未核验',
    PROVIDER_LIMIT_REACHED: '提供方要求暂停新增',
    GENERATION_PAUSED: '管理员已暂停新增',
    STORAGE_UNAVAILABLE: '存储空间不足，暂停新增',
    RECONCILIATION_REQUIRED: '结果尚未核清，不会自动重新生成',
  })[code] || code;
