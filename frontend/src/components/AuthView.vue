<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import { api } from '../api.js';
import { useActions } from '../actions.js';
import ErrorNotice from './ErrorNotice.vue';
const props = defineProps({ initial: Object });
const emit = defineEmits(['login']);
const mode = ref(props.initial.mode),
  token = ref(props.initial.token),
  username = ref(''),
  password = ref(''),
  policy = ref(null),
  validated = ref(false),
  note = ref('');
const { busy, error, execute } = useActions();
props.initial.token = '';
const controller = new AbortController(),
  signal = controller.signal;
onBeforeUnmount(() => {
  controller.abort();
  token.value = '';
  password.value = '';
});
onMounted(() =>
  execute(async () => {
    policy.value = await api.request('/api/v1/auth/policy', { signal });
    await api.getCsrf();
  }),
);
function change(next) {
  mode.value = next;
  password.value = '';
  validated.value = false;
  note.value = '';
  error.value = null;
}
function validate() {
  return execute(async () => {
    const endpoint = mode.value === 'register' ? 'invitations' : 'password-resets';
    const result = await api.request(`/api/v1/auth/${endpoint}/validate`, {
      method: 'POST',
      body: { token: token.value },
      signal,
    });
    validated.value = result.valid;
    note.value =
      mode.value === 'register' && result.expires_at === null
        ? '链接有效，永不过期。'
        : `链接有效至 ${new Date(result.expires_at).toLocaleString()}`;
  });
}
function submit() {
  return execute(async () => {
    const secret = password.value;
    try {
      if (mode.value === 'reset') {
        await api.request('/api/v1/auth/password-resets/complete', {
          method: 'POST',
          body: { reset_token: token.value, new_password: secret },
          signal,
        });
        token.value = '';
        change('login');
        note.value = '密码已重置，旧登录已撤销，请重新登录。';
        api.clear();
        await api.getCsrf();
      } else {
        const body = { username: username.value, password: secret };
        if (mode.value === 'register') body.invite_token = token.value;
        const result = await api.request(`/api/v1/auth/${mode.value}`, {
          method: 'POST',
          body,
          signal,
        });
        api.setCsrf(result.csrf_token);
        token.value = '';
        emit('login', result.user);
      }
    } finally {
      password.value = '';
    }
  });
}
</script>
<template>
  <main class="auth-page">
    <section class="auth-card">
      <div class="brand">≋ OmniFlow</div>
      <h1>
        {{
          mode === 'login' ? '回到你的创作' : mode === 'register' ? '受邀加入工作台' : '重置密码'
        }}
      </h1>
      <p class="muted">独立对话，保留每一版想法。仅限受邀用户。</p>
      <ErrorNotice :error="error" />
      <form class="stack" @submit.prevent="submit">
        <template v-if="mode !== 'login'">
          <label
            >一次性链接凭据<input
              v-model="token"
              type="password"
              autocomplete="off"
              required
              @input="validated = false"
          /></label>
          <button type="button" :disabled="busy || !token" @click="validate">校验链接</button>
        </template>
        <label v-if="mode !== 'reset'"
          >用户名<input
            v-model="username"
            autocomplete="username"
            required
            maxlength="128"
            autocapitalize="none"
            spellcheck="false"
        /></label>
        <small v-if="mode === 'register' && policy"
          >用户名为3–32位英文字母、数字或下划线；不区分大小写，首尾空格会自动忽略。</small
        >
        <label
          >{{ mode === 'reset' ? '新密码' : '密码'
          }}<input
            v-model="password"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            required
            :minlength="mode === 'login' ? 1 : policy?.password_min_length"
            :maxlength="policy?.password_max_length"
        /></label>
        <small v-if="policy && mode !== 'login'"
          >密码 {{ policy.password_min_length }}–{{
            policy.password_max_length
          }}
          字符，不会截断或移除空格。</small
        >
        <p v-if="note" role="status">{{ note }}</p>
        <button
          class="primary"
          :aria-busy="busy"
          :disabled="busy || !policy || (mode !== 'login' && !validated)"
        >
          {{
            busy
              ? '正在处理…'
              : mode === 'login'
                ? '登录'
                : mode === 'register'
                  ? '注册并登录'
                  : '确认重置'
          }}
        </button>
      </form>
      <nav class="actions auth-modes" aria-label="其他账号操作">
        <button v-if="mode !== 'login'" @click="change('login')">返回登录</button>
        <button v-if="mode !== 'register'" @click="change('register')">受邀注册</button>
        <button v-if="mode !== 'reset'" @click="change('reset')">忘记密码</button>
      </nav>
      <p v-if="mode === 'reset'" class="muted">
        没有邮箱或短信找回。请联系管理员，人工核验后获取一次性重置链接。
      </p>
    </section>
  </main>
</template>
