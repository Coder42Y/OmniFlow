import { reactive } from 'vue';
import { ApiError } from './api.js';

export class StreamGap extends Error {}
export async function readSSE(response, onFrame, signal) {
  if (!response.body || !response.headers.get('Content-Type')?.startsWith('text/event-stream'))
    throw new StreamGap('事件格式无效');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '',
    data = [],
    event = '',
    id = '';
  const line = async (value) => {
    if (!value) {
      if (data.length)
        await onFrame({ event: event || 'message', id, data: JSON.parse(data.join('\n')) });
      data = [];
      event = '';
      id = '';
      return;
    }
    if (value[0] === ':') return;
    const split = value.indexOf(':');
    const field = split < 0 ? value : value.slice(0, split);
    const text = split < 0 ? '' : value.slice(split + 1).replace(/^ /, '');
    if (field === 'data') data.push(text);
    if (field === 'event') event = text;
    if (field === 'id') id = text;
  };
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      if (buffer.length + data.join('').length > 2_000_000) throw new StreamGap('事件过长');
      let match;
      while ((match = /\r\n|\n|\r(?!$)/.exec(buffer))) {
        const text = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        await line(text);
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function createConversationState() {
  return reactive({
    conversation: null,
    messages: [],
    runs: [],
    tasks: [],
    versions: {},
    cursor: '0',
    older: null,
    chunks: {},
    connection: '未连接',
  });
}
export function upsert(items, value) {
  const index = items.findIndex((item) => item.id === value.id);
  if (index < 0) items.push(value);
  else if (
    !items[index].updated_at ||
    !value.updated_at ||
    value.updated_at >= items[index].updated_at
  )
    items[index] = value;
}
export function applySnapshot(state, snapshot) {
  state.conversation = snapshot.conversation;
  state.messages = snapshot.messages;
  state.runs = snapshot.runs;
  state.tasks = snapshot.tasks;
  state.versions = Object.fromEntries(snapshot.artifact_versions.map((v) => [v.id, v]));
  state.cursor = snapshot.last_event_id;
  state.older = snapshot.messages_next_cursor;
  state.chunks = Object.fromEntries(snapshot.messages.map((m) => [m.id, null]));
}
export function applyEvent(state, frame) {
  const item = frame.data;
  if (frame.event === 'control')
    throw new ApiError({
      status: item.code === 'AUTH_REQUIRED' ? 401 : 410,
      code: item.code,
      detail: item.message,
    });
  if (
    !/^\d+$/.test(frame.id) ||
    item.event_id !== frame.id ||
    item.type !== frame.event ||
    item.conversation_id !== state.conversation?.id
  )
    throw new StreamGap('事件归属或序号无效');
  if (BigInt(frame.id) <= BigInt(state.cursor)) return;
  const value = item.data;
  switch (item.type) {
    case 'message.created':
    case 'message.updated':
      upsert(state.messages, value);
      state.messages.sort((a, b) => a.seq - b.seq);
      if (item.type === 'message.created') state.chunks[value.id] = 0;
      break;
    case 'message.delta': {
      const message = state.messages.find((m) => m.id === value.message_id);
      const previous = state.chunks[value.message_id];
      if (
        !message ||
        !Number.isInteger(value.chunk_index) ||
        value.chunk_index < 1 ||
        (previous != null && value.chunk_index !== previous + 1)
      )
        throw new StreamGap('文字片段不连续');
      message.content += value.delta;
      message.status = 'streaming';
      state.chunks[value.message_id] = value.chunk_index;
      break;
    }
    case 'run.updated':
      upsert(state.runs, value);
      break;
    case 'task.updated':
      upsert(state.tasks, value);
      break;
    case 'artifact.ready':
      state.versions[value.id] = value;
      break;
    case 'conversation.updated':
      state.conversation = value;
      break;
    default:
      throw new StreamGap('未知事件');
  }
  state.cursor = frame.id;
}

const sleep = (ms, signal) =>
  new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener('abort', finish, { once: true });
    if (signal.aborted) finish();
  });

export function followConversation(
  api,
  state,
  cid,
  { onAuth = () => {}, onError = () => {} } = {},
) {
  const controller = new AbortController();
  const signal = controller.signal;
  const base = `/api/v1/conversations/${cid}`;
  const done = (async () => {
    let snapshotNeeded = true,
      failures = 0;
    while (!signal.aborted) {
      try {
        state.connection = snapshotNeeded ? '正在恢复记录' : '正在重连';
        if (snapshotNeeded) {
          const snapshot = await api.request(base + '/snapshot', { signal });
          signal.throwIfAborted();
          applySnapshot(state, snapshot);
          snapshotNeeded = false;
        }
        const response = await api.request(base + '/events?after_event_id=' + state.cursor, {
          raw: true,
          signal,
        });
        signal.throwIfAborted();
        state.connection = '实时连接';
        await readSSE(
          response,
          (frame) => {
            signal.throwIfAborted();
            applyEvent(state, frame);
            failures = 0;
          },
          signal,
        );
        if (!signal.aborted) throw new Error('连接结束');
      } catch (error) {
        if (signal.aborted || error.name === 'AbortError') return;
        if (error.code === 'AUTH_REQUIRED' || error.code === 'ACCOUNT_DISABLED') {
          onAuth();
          return;
        }
        if (
          error.code === 'RESOURCE_NOT_FOUND' ||
          error.code === 'RESOURCE_GONE' ||
          error.status === 403
        ) {
          state.conversation = null;
          state.messages = [];
          state.versions = {};
          state.runs = [];
          state.tasks = [];
          state.connection = '记录不可访问';
          onError(error);
          return;
        }
        snapshotNeeded =
          error instanceof StreamGap ||
          error instanceof SyntaxError ||
          ['EVENT_CURSOR_EXPIRED', 'INVALID_EVENT_CURSOR'].includes(error.code);
        failures++;
        if (failures >= 6) {
          state.connection = '连接已暂停，请手动重新连接';
          onError(error);
          return;
        }
        state.connection = '连接中断，已有任务继续处理';
        await sleep(
          Math.max(Math.min(1000 * 2 ** (failures - 1), 15000), (error.retryAfter || 0) * 1000),
          signal,
        );
      }
    }
  })();
  return { stop: () => controller.abort(), done };
}
