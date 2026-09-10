import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { api, ApiError } from '../src/api.js';
import RefinePanel from '../src/components/RefinePanel.vue';
import MediaCard from '../src/components/MediaCard.vue';
import TaskCard from '../src/components/TaskCard.vue';
import AuthView from '../src/components/AuthView.vue';
import AdminPanel from '../src/components/AdminPanel.vue';
import LibraryPanel from '../src/components/LibraryPanel.vue';
import { capabilities, version, task, id, user, policy, artifact } from './fixtures.js';
const wrappers = [];
const render = (component, options) => {
  const wrapper = mount(component, options);
  wrappers.push(wrapper);
  return wrapper;
};
afterEach(() => {
  wrappers.forEach((w) => w.unmount());
  wrappers.length = 0;
  api.clear();
});
const button = (wrapper, text) => wrapper.findAll('button').find((b) => b.text().includes(text));
const refine = (props) =>
  render(RefinePanel, {
    props: { cid: id(2), capabilities: structuredClone(capabilities), ...props },
  });
const configureVideo = async (wrapper) => {
  await wrapper.findAll('select')[0].setValue('ai_video');
  await wrapper.find('textarea').setValue('固定镜头轻轻摆动');
  await wrapper.findAll('select')[1].setValue('9:16');
  await wrapper.find('input[type=number]').setValue(4);
};

describe('精修与明确图片确认', () => {
  it('列出全部缺少项，编号放详情，不更改确认规则', async () => {
    const wrapper = refine({ version: version() });
    await flushPromises();
    expect(wrapper.find('.version-summary').text()).toContain('v1');
    expect(wrapper.find('.version-summary').text()).not.toContain(id(6));
    expect(wrapper.find('.version-details').text()).toContain(id(6));
    expect(wrapper.find('#refine-requirements').text()).toContain('画幅');
    expect(wrapper.find('#refine-requirements').text()).toContain('尺寸档位');
    expect(wrapper.find('#refine-requirements').text()).toContain('修改／创作要求');
    await configureVideo(wrapper);
    expect(wrapper.find('#refine-requirements').text()).toBe('还需：确认此图片首帧');
  });
  it('没有图时不能误将两个 undefined 当作已确认；明确文生视频才放行', async () => {
    const request = vi.spyOn(api, 'request').mockResolvedValue(task());
    const wrapper = refine();
    await configureVideo(wrapper);
    expect(button(wrapper, '提交一份任务').attributes('disabled')).toBeDefined();
    expect(wrapper.text()).toContain('先生成或上传视觉稿');
    await wrapper.findAll('select')[2].setValue('text');
    await wrapper.find('form').trigger('submit');
    await flushPromises();
    const [, options] = request.mock.calls[0];
    expect(options.body).toMatchObject({
      kind: 'ai_video',
      mode: 'text',
      seconds: 4,
      size_tier: '720P',
    });
    expect(options.body).not.toHaveProperty('reference_confirmation_id');
  });
  it('明确点击确认后 keyframe 使用精确版本与返回标识，不把图片当视频目标', async () => {
    const request = vi
      .spyOn(api, 'request')
      .mockImplementation(async (path) =>
        path.includes('reference-confirmations')
          ? { id: id(7), version_id: id(6), conversation_id: id(2), purpose: 'video_first_frame' }
          : task(),
      );
    const wrapper = refine({ version: version() });
    await configureVideo(wrapper);
    expect(button(wrapper, '提交一份任务').attributes('disabled')).toBeDefined();
    await button(wrapper, '确认：用这张').trigger('click');
    await flushPromises();
    expect(button(wrapper, '提交一份任务').attributes('disabled')).toBeUndefined();
    await wrapper.find('form').trigger('submit');
    await flushPromises();
    expect(request.mock.calls[0][1].body).toEqual({
      version_id: id(6),
      purpose: 'video_first_frame',
    });
    expect(request.mock.calls[1][1].body).toMatchObject({
      reference_confirmation_id: id(7),
      kind: 'ai_video',
    });
    expect(request.mock.calls[1][1].body).not.toHaveProperty('target_artifact_id');
  });
  it('图片精修成对绑定目标和基础版本；不发送任意 model', async () => {
    const request = vi.spyOn(api, 'request').mockResolvedValue(task());
    const wrapper = refine({ version: version() });
    await wrapper.find('textarea').setValue('更冷一点');
    await wrapper.findAll('select')[1].setValue('1:1');
    await wrapper.findAll('select')[2].setValue('1K');
    await wrapper.find('form').trigger('submit');
    await flushPromises();
    expect(request.mock.calls[0][1].body).toEqual({
      kind: 'image',
      conversation_id: id(2),
      prompt: '更冷一点',
      aspect_ratio: '1:1',
      size_tier: '1K',
      reference_version_ids: [id(6)],
      target_artifact_id: id(5),
      base_version_id: id(6),
    });
  });
  it('能力未开放／尺寸未明确不能提交，也不降级为无参考生成', async () => {
    const cap = structuredClone(capabilities);
    cap.image.reference_editing_enabled = false;
    const request = vi.spyOn(api, 'request');
    const wrapper = refine({ version: version(), capabilities: cap });
    await wrapper.find('textarea').setValue('更冷一点');
    await wrapper.findAll('select')[1].setValue('1:1');
    await wrapper.findAll('select')[2].setValue('1K');
    await wrapper.find('form').trigger('submit');
    expect(request).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('参考图编辑尚未开放');
  });
  it('响应丢失重试相同任务参数和键，不换编号', async () => {
    const request = vi
      .spyOn(api, 'request')
      .mockRejectedValueOnce(new ApiError({ code: 'NETWORK_ERROR', status: 0, retryable: true }))
      .mockResolvedValueOnce(task());
    const wrapper = refine();
    await configureVideo(wrapper);
    await wrapper.findAll('select')[2].setValue('text');
    await wrapper.find('form').trigger('submit');
    await flushPromises();
    expect(wrapper.find('fieldset').attributes('disabled')).toBeDefined();
    await button(wrapper, '重试同一动作').trigger('click');
    await flushPromises();
    expect(request.mock.calls[0][1].key).toBe(request.mock.calls[1][1].key);
    expect(request.mock.calls[0][1].body).toEqual(request.mock.calls[1][1].body);
  });
  it('本地运镜明确非AI且只接受图片版本', async () => {
    const request = vi.spyOn(api, 'request').mockResolvedValue(task());
    const wrapper = refine({ version: version() });
    await wrapper.findAll('select')[0].setValue('local_motion');
    await wrapper.findAll('select')[1].setValue('16:9');
    await wrapper.find('input[type=number]').setValue(2.5);
    await wrapper.find('form').trigger('submit');
    await flushPromises();
    expect(request.mock.calls[0][1].body).toEqual({
      kind: 'local_motion',
      conversation_id: id(2),
      aspect_ratio: '16:9',
      image_version_id: id(6),
      motion_type: 'dolly_in',
      seconds: 2.5,
    });
    expect(wrapper.text()).toContain('不是 AI 动态生成');
  });
});

describe('媒体与真实状态', () => {
  it('卡片只四舍五入实际时长，原始值与型号仍可查；选中不等于已确认', async () => {
    const wrapper = render(MediaCard, {
      props: {
        version: {
          ...version('agnes-video-2.5-flash'),
          duration_seconds: 4.458333,
          byte_size: 1048576,
        },
        selected: true,
      },
    });
    expect(wrapper.find('figcaption > small').text()).toBe('媒体加载中…');
    await wrapper.find('video').trigger('loadedmetadata');
    expect(wrapper.find('figcaption > small').text()).toContain('4.46 秒 · 1 MB');
    expect(wrapper.find('.version-details').text()).toContain('4.458333 秒');
    expect(wrapper.find('.version-details').text()).toContain('agnes-video-2.5-flash');
    expect(wrapper.text()).toContain('已选为参考');
    expect(wrapper.text()).not.toContain('已确认视频首帧');
    await wrapper.setProps({ confirmed: true });
    expect(wrapper.text()).toContain('已确认视频首帧');
  });
  it('已在消息中显示的作品不重复大图，任务事实仍保留', () => {
    const wrapper = render(TaskCard, {
      props: {
        task: { ...task('completed'), output_version_ids: [id(6)] },
        versions: { [id(6)]: version() },
        shownVersions: [id(6)],
      },
    });
    expect(wrapper.text()).toContain('已完成');
    expect(wrapper.text()).toContain('作品已展示在对应消息中');
    expect(wrapper.findComponent(MediaCard).exists()).toBe(false);
  });
  it('实际尺寸时长独立，视频不自动播放、有 controls 与 playsinline', () => {
    const wrapper = render(MediaCard, { props: { version: version('local-ffmpeg') } });
    const video = wrapper.find('video');
    expect(video.attributes('autoplay')).toBeUndefined();
    expect(video.attributes('controls')).toBeDefined();
    expect(video.attributes('playsinline')).toBeDefined();
    expect(wrapper.text()).toContain('非 AI 动态生成');
    expect(wrapper.text()).toContain('1.25 秒');
  });
  it('UI-05：读取失败不推断已删除，不用占位图冒充成果，允许只重试读取', async () => {
    const wrapper = render(MediaCard, { props: { version: version() } });
    await wrapper.find('img').trigger('error');
    expect(wrapper.find('a').exists()).toBe(false);
    expect(wrapper.text()).toContain('暂不可查看');
    expect(wrapper.text()).not.toContain('已删除');
    expect(wrapper.find('img').exists()).toBe(false);
    await button(wrapper, '重试读取').trigger('click');
    expect(wrapper.find('img').exists()).toBe(true);
    expect(wrapper.find('img').attributes('src')).toBe(version().content_url);
  });
  it('文案以文本渲染，不能执行用户 HTML', async () => {
    vi.spyOn(api, 'request').mockResolvedValue(new Response('<img src=x onerror=alert(1)>'));
    const wrapper = render(MediaCard, {
      props: { version: { ...version(), media_type: 'text/plain' } },
    });
    await button(wrapper, '查看文案').trigger('click');
    await flushPromises();
    expect(wrapper.find('img').exists()).toBe(false);
    expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>');
  });
  it.each(['submitting', 'running', 'completed', 'submission_unknown', 'needs_reconciliation'])(
    '%s 不提供取消已提交／自动重新生成',
    (status) => {
      const wrapper = render(TaskCard, { props: { task: task(status), versions: {} } });
      expect(button(wrapper, '取消排队')).toBeUndefined();
      expect(button(wrapper, '重新生成')).toBeUndefined();
      expect(wrapper.find('progress').exists()).toBe(false);
    },
  );
  it('恢复仅在 can_recover 且标明不重新生成', async () => {
    const wrapper = render(TaskCard, { props: { task: task('saving'), versions: {} } });
    await button(wrapper, '恢复下载').trigger('click');
    expect(wrapper.emitted('recover')).toHaveLength(1);
  });
});

describe('账号、管理员与版本库', () => {
  it('永久邀请校验显示永不过期而不是1970年', async () => {
    vi.spyOn(api, 'getCsrf').mockResolvedValue();
    vi.spyOn(api, 'request').mockImplementation(async (path) =>
      path.endsWith('/policy') ? policy : { valid: true, expires_at: null },
    );
    const wrapper = render(AuthView, {
      props: { initial: { mode: 'register', token: 'synthetic_only_token' } },
    });
    await flushPromises();
    await button(wrapper, '校验链接').trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('链接有效，永不过期。');
    expect(wrapper.text()).not.toContain('1970');
  });
  it('管理员可显式签发永久邀请并正确显示空到期时间', async () => {
    const request = vi.spyOn(api, 'request').mockImplementation(async (path, options) => {
      if (options?.method === 'POST')
        return { invite_url: 'https://localhost/register#token=synthetic' };
      return {
        items: path.includes('invitations')
          ? [{ id: id(30), status: 'active', expires_at: null }]
          : [],
        next_cursor: null,
      };
    });
    const wrapper = render(AdminPanel);
    await flushPromises();
    await wrapper.find('select').setValue('invitations');
    await flushPromises();
    expect(wrapper.text()).toContain('永不过期');
    expect(wrapper.text()).not.toContain('1970');
    await wrapper.find('input[type=checkbox]').setValue(true);
    await button(wrapper, '签发一次性邀请').trigger('click');
    await flushPromises();
    expect(
      request.mock.calls.some(
        ([path, options]) =>
          path.endsWith('/invitations') &&
          options?.method === 'POST' &&
          options.body.expires_in_seconds === null,
      ),
    ).toBe(true);
  });
  it.each(['register', 'reset'])('%s使用服务端六位密码下限，不在前端硬编码旧值', async (mode) => {
    vi.spyOn(api, 'getCsrf').mockResolvedValue();
    vi.spyOn(api, 'request').mockResolvedValue({ ...policy, password_min_length: 6 });
    const wrapper = render(AuthView, { props: { initial: { mode, token: '' } } });
    await flushPromises();
    const input = wrapper.find('input[autocomplete=new-password]');
    expect(input.attributes('minlength')).toBe('6');
    expect(input.attributes('maxlength')).toBe(String(policy.password_max_length));
    expect(wrapper.text()).toMatch(/密码\s+6–/);
  });
  it('注册读取真实策略与校验token；密码不trim，响应后清空', async () => {
    vi.spyOn(api, 'getCsrf').mockResolvedValue();
    const request = vi
      .spyOn(api, 'request')
      .mockImplementation(async (path) =>
        path.endsWith('/policy')
          ? policy
          : path.endsWith('/validate')
            ? { valid: true, expires_at: '2026-09-09T08:00:00Z' }
            : { user, csrf_token: 'new-csrf' },
      );
    const wrapper = render(AuthView, {
      props: { initial: { mode: 'register', token: 'synthetic_only_token' } },
    });
    await flushPromises();
    expect(wrapper.find('input[autocomplete=new-password]').attributes('minlength')).toBe('14');
    expect(wrapper.text()).toContain('用户名为3–32位英文字母、数字或下划线');
    expect(wrapper.text()).not.toContain(policy.username_pattern);
    await button(wrapper, '校验链接').trigger('click');
    await flushPromises();
    await wrapper.find('input[autocomplete=username]').setValue('SYNTHETIC');
    await wrapper.find('input[autocomplete=new-password]').setValue('  synthetic password  ');
    await wrapper.find('form').trigger('submit');
    await flushPromises();
    expect(request.mock.calls.at(-1)[1].body.password).toBe('  synthetic password  ');
    expect(wrapper.find('input[autocomplete=new-password]').element.value).toBe('');
    expect(wrapper.emitted('login')[0][0]).toEqual(user);
  });
  it('管理员只有元数据路由；人工核验前不能签发重置，失败不重试', async () => {
    const request = vi
      .spyOn(api, 'request')
      .mockResolvedValue({ items: [user], next_cursor: null });
    const wrapper = render(AdminPanel);
    await flushPromises();
    await button(wrapper, '人工核验并重置').trigger('click');
    expect(button(wrapper, '签发一次性重置').attributes('disabled')).toBeDefined();
    await wrapper.find('input[type=checkbox]').setValue(true);
    request.mockRejectedValueOnce(
      new ApiError({ code: 'NETWORK_ERROR', status: 0, retryable: true }),
    );
    await button(wrapper, '签发一次性重置').trigger('click');
    await flushPromises();
    expect(request.mock.calls.at(-1)[0]).toBe(`/api/v1/admin/users/${user.id}/password-resets`);
    expect(button(wrapper, '重试同一动作')).toBeUndefined();
    expect(request.mock.calls.every(([path]) => path.startsWith('/api/v1/admin/'))).toBe(true);
  });
  it('管理员暂停只发送一个布尔开关，恢复不覆盖提供方条件', async () => {
    const request = vi
      .spyOn(api, 'request')
      .mockResolvedValueOnce({ items: [], next_cursor: null })
      .mockResolvedValue({
        text_enabled: true,
        image_enabled: true,
        ai_video_enabled: false,
        local_motion_enabled: true,
      });
    const wrapper = render(AdminPanel);
    await flushPromises();
    await wrapper.find('select').setValue('generation-policy');
    await flushPromises();
    await button(wrapper, '暂停图片').trigger('click');
    await flushPromises();
    expect(request.mock.calls.at(-1)[1].body).toEqual({ image_enabled: false });
    expect(wrapper.text()).toContain('恢复不覆盖提供方限制');
  });
  it('作品分页、具体旧版本选择与删除二次说明', async () => {
    const request = vi
      .spyOn(api, 'request')
      .mockResolvedValueOnce({ items: [artifact()], next_cursor: 'opaque-page' })
      .mockResolvedValueOnce({ items: [version()], next_cursor: null })
      .mockResolvedValueOnce({
        artifact_id: id(5),
        status: 'access_revoked',
        purge_target_at: '2026-09-09T08:00:00Z',
      })
      .mockResolvedValue({ items: [], next_cursor: null });
    const wrapper = render(LibraryPanel);
    await flushPromises();
    expect(button(wrapper, '加载更多作品')).toBeDefined();
    await button(wrapper, '合成视觉稿').trigger('click');
    await flushPromises();
    await button(wrapper, '选为参考图').trigger('click');
    expect(wrapper.emitted('select')[0][0].id).toBe(id(6));
    await button(wrapper, '删除整个作品').trigger('click');
    expect(request).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain('无法撤销');
    await button(wrapper, '确认永久删除').trigger('click');
    await flushPromises();
    expect(request.mock.calls[2][1].method).toBe('DELETE');
    expect(wrapper.emitted('deleted')[0]).toEqual([id(5)]);
  });
});
