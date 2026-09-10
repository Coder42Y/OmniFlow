import { describe, it, expect, vi } from 'vitest';
import {
  createConversationState,
  applySnapshot,
  applyEvent,
  readSSE,
  StreamGap,
  followConversation,
} from '../src/events.js';
import { ApiError } from '../src/api.js';
import { snapshot, message, event, id, version, task } from './fixtures.js';

function streamed(parts) {
  return new Response(
    new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder();
        for (const part of parts) controller.enqueue(encoder.encode(part));
        controller.close();
      },
    }),
    { headers: { 'Content-Type': 'text/event-stream' } },
  );
}
describe('一致快照与事件恢复', () => {
  it('持久大序号去重，不转换成 Number；完整正文替换增量', () => {
    const state = createConversationState(),
      s = snapshot();
    s.last_event_id = '9007199254740992';
    applySnapshot(state, s);
    const m = { ...message(1, 'assistant'), content: '' };
    applyEvent(state, event('message.created', m, '9007199254740993'));
    const delta = event(
      'message.delta',
      { message_id: m.id, run_id: m.run_id, chunk_index: 1, delta: '你好🌊' },
      '9007199254740994',
    );
    applyEvent(state, delta);
    applyEvent(state, delta);
    expect(state.messages[0].content).toBe('你好🌊');
    applyEvent(state, event('message.updated', { ...m, content: '完整正文' }, '9007199254740995'));
    expect(state.messages[0].content).toBe('完整正文');
  });
  it('快照中已输出片段允许未知基点，之后必须连续；断档不前移游标', () => {
    const state = createConversationState(),
      s = snapshot(1);
    applySnapshot(state, s);
    const m = s.messages[1];
    applyEvent(
      state,
      event(
        'message.delta',
        { message_id: m.id, run_id: m.run_id, chunk_index: 12, delta: '新增' },
        '1',
      ),
    );
    expect(() =>
      applyEvent(
        state,
        event(
          'message.delta',
          { message_id: m.id, run_id: m.run_id, chunk_index: 14, delta: '遗漏' },
          '2',
        ),
      ),
    ).toThrow(StreamGap);
    expect(state.cursor).toBe('1');
    expect(state.messages[1].content).not.toContain('遗漏');
  });
  it('拒绝另一对话帧、错误 event_id、未知事件', () => {
    const state = createConversationState();
    applySnapshot(state, snapshot());
    const foreign = event('task.updated', task());
    foreign.data.conversation_id = id(99);
    expect(() => applyEvent(state, foreign)).toThrow(StreamGap);
    const bad = event('task.updated', task());
    bad.data.event_id = '2';
    expect(() => applyEvent(state, bad)).toThrow(StreamGap);
    expect(() => applyEvent(state, event('unknown', {}))).toThrow(StreamGap);
    expect(state.tasks).toHaveLength(0);
  });
  it('作品事件、任务实际状态与文字终态分离；缺失版本仍保留其余引用', () => {
    const state = createConversationState();
    applySnapshot(state, snapshot(1));
    applyEvent(state, event('task.updated', task('saving')));
    applyEvent(state, event('artifact.ready', version(), '2'));
    applyEvent(
      state,
      event(
        'message.updated',
        { ...message(2, 'assistant'), artifact_version_ids: [id(6), id(77)] },
        '3',
      ),
    );
    expect(state.tasks[0].status).toBe('saving');
    expect(state.versions[id(6)]).toEqual(version());
    expect(state.messages[1].artifact_version_ids).toHaveLength(2);
  });
  it('SSE 跨网络块、CRLF、注释、多行 data 与 Unicode 正确读取', async () => {
    const frames = [];
    await readSSE(
      streamed([
        ': heartbeat\r\n\r',
        '\nid: 1\r\nevent: note\r\ndata: {"text":\r\n',
        'data: "合成🌊"}\r\n\r\n',
      ]),
      (frame) => frames.push(frame),
      new AbortController().signal,
    );
    expect(frames).toEqual([{ event: 'note', id: '1', data: { text: '合成🌊' } }]);
  });
  it('登录撤销 control 不当作普通可重连事件', () => {
    const state = createConversationState();
    applySnapshot(state, snapshot());
    expect(() =>
      applyEvent(state, { event: 'control', data: { code: 'AUTH_REQUIRED', message: '撤销' } }),
    ).toThrow(ApiError);
  });
  it('410 重取快照；只订阅读取，不触发任何 POST', async () => {
    vi.useFakeTimers();
    const state = createConversationState(),
      auth = vi.fn();
    const request = vi
      .fn()
      .mockResolvedValueOnce(snapshot())
      .mockRejectedValueOnce(new ApiError({ code: 'EVENT_CURSOR_EXPIRED', status: 410 }))
      .mockResolvedValueOnce({ ...snapshot(), last_event_id: '20' })
      .mockResolvedValueOnce(
        streamed(['event: control\ndata: {"code":"AUTH_REQUIRED","message":"撤销"}\n\n']),
      );
    const feed = followConversation({ request }, state, id(2), { onAuth: auth });
    await vi.advanceTimersByTimeAsync(1100);
    await feed.done;
    expect(request.mock.calls.map((call) => call[0])).toEqual([
      `/api/v1/conversations/${id(2)}/snapshot`,
      `/api/v1/conversations/${id(2)}/events?after_event_id=0`,
      `/api/v1/conversations/${id(2)}/snapshot`,
      `/api/v1/conversations/${id(2)}/events?after_event_id=20`,
    ]);
    expect(auth).toHaveBeenCalledOnce();
    feed.stop();
    vi.useRealTimers();
  });
  it('切换会话 abort：忽略先前快照晚到，清理重连计时器', async () => {
    let resolve;
    const state = createConversationState(),
      request = vi.fn(
        () =>
          new Promise((r) => {
            resolve = r;
          }),
      );
    const feed = followConversation({ request }, state, id(2));
    feed.stop();
    resolve(snapshot(5));
    await feed.done;
    expect(state.messages).toHaveLength(0);
    expect(request).toHaveBeenCalledOnce();
  });
});
