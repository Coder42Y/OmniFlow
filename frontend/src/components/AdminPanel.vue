<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import { api, query } from '../api.js';
import { useActions, statusName } from '../actions.js';
import ErrorNotice from './ErrorNotice.vue';
const { busy, error, execute } = useActions();
const tab = ref('users'),
  items = ref([]),
  cursor = ref(null),
  policy = ref(null),
  issued = ref(''),
  target = ref(null),
  method = ref('trusted_existing_contact'),
  verified = ref(false),
  neverExpires = ref(false);
const fields = {
  text_enabled: '文字',
  image_enabled: '图片',
  ai_video_enabled: 'AI 视频',
  local_motion_enabled: '本地运镜',
};
const controller = new AbortController(),
  signal = controller.signal;
onBeforeUnmount(() => {
  controller.abort();
  issued.value = '';
});
async function load(more = false) {
  if (tab.value === 'generation-policy')
    policy.value = await api.request('/api/v1/admin/generation-policy', { signal });
  else {
    const page = await api.request(
      query(`/admin/${tab.value}`, { cursor: more ? cursor.value : null }),
      { signal },
    );
    items.value = more ? [...items.value, ...page.items] : page.items;
    cursor.value = page.next_cursor;
  }
}
function switchTab() {
  items.value = [];
  cursor.value = null;
  target.value = null;
  issued.value = '';
  return execute(() => load());
}
function issueInvite() {
  issued.value = '';
  return execute(async () => {
    const result = await api.request('/api/v1/admin/invitations', {
      method: 'POST',
      body: neverExpires.value ? { expires_in_seconds: null } : {},
      signal,
    });
    issued.value = result.invite_url;
    await load();
  }); // 签发绝不自动重试，也不提供重放按钮。
}
function revoke(invite) {
  return execute(async () => {
    await api.request(`/api/v1/admin/invitations/${invite.id}/revoke`, { method: 'POST', signal });
    await load();
  });
}
function status(user) {
  return execute(async () => {
    await api.request(`/api/v1/admin/users/${user.id}`, {
      method: 'PATCH',
      body: { status: user.status === 'active' ? 'disabled' : 'active' },
      signal,
    });
    target.value = null;
    await load();
  });
}
function reset() {
  if (!verified.value) return;
  issued.value = '';
  return execute(async () => {
    const result = await api.request(
      `/api/v1/admin/users/${target.value.user.id}/password-resets`,
      { method: 'POST', body: { verification_method: method.value }, signal },
    );
    issued.value = result.reset_url;
    target.value = null;
    verified.value = false;
  });
}
function toggle(key) {
  return execute(async () => {
    policy.value = await api.request('/api/v1/admin/generation-policy', {
      method: 'PATCH',
      body: { [key]: !policy.value[key] },
      signal,
    });
  });
}
onMounted(() => execute(() => load()));
</script>
<template>
  <section class="stack">
    <p>仅管理账号元数据和故障概况，不提供用户私密聊天或作品入口。</p>
    <label
      >管理内容<select v-model="tab" :disabled="busy" @change="switchTab">
        <option value="users">用户账号</option>
        <option value="invitations">邀请</option>
        <option value="tasks">任务故障概况</option>
        <option value="generation-policy">新增生成开关</option>
      </select></label
    >
    <ErrorNotice :error="error" />
    <div v-if="issued" class="warning-box">
      <label
        >仅显示本次签发链接，请通过可信联系渠道交付<input
          :value="issued"
          readonly
          autocomplete="off"
          aria-label="本次一次性链接" /></label
      ><button @click="issued = ''">隐藏链接</button>
    </div>
    <p v-if="['users', 'invitations'].includes(tab)" class="muted">
      签发请求不自动重试。响应丢失时先核对／撤销邀请；重发密码重置会使旧重置链接失效。
    </p>
    <label v-if="tab === 'invitations'" class="check">
      <input v-model="neverExpires" type="checkbox" :disabled="busy" />
      永不过期（注册一次即失效，可随时撤销）
    </label>
    <button v-if="tab === 'invitations'" :disabled="busy" @click="issueInvite">
      签发一次性邀请
    </button>
    <template v-if="tab === 'generation-policy' && policy">
      <p>
        关闭只暂停新增和未提交工作，已受理的仍继续核对和保存。恢复不覆盖提供方限制，也不启用收费回退。
      </p>
      <div v-for="(label, key) in fields" :key="key" class="split">
        <span>{{ label }}：{{ policy[key] ? '允许新增' : '本地暂停' }}</span
        ><button :disabled="busy" @click="toggle(key)">
          {{ policy[key] ? '暂停' : '恢复' }}{{ label }}
        </button>
      </div>
    </template>
    <article v-for="item in items" :key="item.id" class="list-item">
      <template v-if="tab === 'users'"
        ><span>{{ item.username }} · {{ item.role }} · {{ statusName(item.status) }}</span>
        <div class="actions">
          <button :disabled="busy" @click="target = { action: 'status', user: item }">
            {{ item.status === 'active' ? '停用' : '启用' }}账号</button
          ><button
            :disabled="busy"
            @click="
              target = { action: 'reset', user: item };
              verified = false;
            "
          >
            人工核验并重置
          </button>
        </div></template
      >
      <template v-else-if="tab === 'invitations'"
        ><span>{{ item.id }} · {{ statusName(item.status) }}</span
        ><small>{{
          item.expires_at === null
            ? '永不过期'
            : `到期 ${new Date(item.expires_at).toLocaleString()}`
        }}</small
        ><button v-if="item.status === 'active'" :disabled="busy" @click="revoke(item)">
          撤销邀请
        </button></template
      >
      <template v-else-if="tab === 'tasks'"
        ><span>{{ item.kind }} · {{ statusName(item.status) }}</span
        ><small>任务 {{ item.id }} · 用户 {{ item.user_id }}</small
        ><span>{{ item.error_code || '暂无错误' }}</span></template
      >
    </article>
    <div v-if="target" class="warning-box">
      <p>账号：{{ target.user.username }}</p>
      <template v-if="target.action === 'status'"
        ><p>停用将撤销登录，暂停未提交工作；不会删除作品或假称已受理的工作全部取消。</p>
        <button :disabled="busy" @click="status(target.user)">
          确认{{ target.user.status === 'active' ? '停用' : '启用' }}
        </button></template
      >
      <template v-else
        ><label
          >核验方式<select v-model="method">
            <option value="trusted_existing_contact">已有可信联系渠道</option>
            <option value="in_person">当面核验</option>
          </select></label
        ><label class="check"
          ><input v-model="verified" type="checkbox" />我已实际核验本人身份</label
        ><button :disabled="busy || !verified" @click="reset">签发一次性重置链接</button></template
      >
      <button @click="target = null">返回</button>
    </div>
    <button
      v-if="cursor && tab !== 'generation-policy'"
      :disabled="busy"
      @click="execute(() => load(true))"
    >
      加载更多
    </button>
    <button :disabled="busy" @click="execute(() => load())">刷新管理状态</button>
  </section>
</template>
