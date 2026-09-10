<script setup>
import { reactive, ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import { api, actionKey, query, contentPath } from './api.js';
import { useActions, reasonName } from './actions.js';
import { createConversationState, followConversation, upsert } from './events.js';
import AuthView from './components/AuthView.vue';
import ErrorNotice from './components/ErrorNotice.vue';
import TranscriptView from './components/TranscriptView.vue';
import ModalShell from './components/ModalShell.vue';
import LibraryPanel from './components/LibraryPanel.vue';
import RefinePanel from './components/RefinePanel.vue';
import AdminPanel from './components/AdminPanel.vue';
import TaskCard from './components/TaskCard.vue';
import UiIcon from './components/UiIcon.vue';
const props = defineProps({ initial: Object });
const user = ref(null),
  booting = ref(true),
  capabilities = ref(null),
  conversations = ref([]),
  conversationCursor = ref(null);
const state = createConversationState();
const { busy, error, retry, execute, clear } = useActions();
// 查询和注销不能覆盖未确认写入的原参数／重试闭包，也不能被它锁住。
const { busy: reading, error: readError, execute: readOnly, clear: clearReads } = useActions();
const {
  busy: loggingOut,
  error: logoutError,
  execute: exitSession,
  clear: clearExit,
} = useActions();
const mobile = ref(window.matchMedia('(max-width: 620px)').matches);
const navOpen = ref(false),
  historyOpen = ref(false),
  panel = ref(null),
  refineVersion = ref(null),
  refineForm = ref(null),
  panelFocus = ref(null),
  transcript = ref(),
  title = ref(''),
  notice = ref('');
const drafts = reactive({}),
  emptyDraft = () => ({
    content: '',
    attachments: [],
    selected: null,
    confirmation: null,
    permission: 'requested_only',
  });
const draft = computed(() => (drafts[state.conversation?.id || 'new'] ||= emptyDraft()));
const cid = computed(() => state.conversation?.id);
const connectionTrouble = computed(() => /中断|暂停|不可访问/.test(state.connection));
const taskCursor = ref(null),
  runCursor = ref(null),
  file = ref();
let feed,
  view = new AbortController(),
  drawerFocus;

const promptChips = [
  '商业主图海报（9:16写实）',
  '电影级平滑运镜短视频（4秒）',
  '带货口播与FABE卖点文案',
  '极简性冷淡哑光工业风',
  '微距特写光影流转',
];
function appendPromptChip(chip) {
  if (!draft.value.content) draft.value.content = chip;
  else draft.value.content += `，${chip}`;
}
function fillPrompt(text) {
  draft.value.content = text;
}
function experienceAllInOne() {
  draft.value.content =
    '帮我生成一套黑曜石男士淡雅冷香的带货全案，主打24小时持久木质香与高冷商务气场，包含口播、海报与运镜短视频';
  if (!cid.value) {
    newConversation().then(() => {
      nextTick(() => send());
    });
  } else {
    send();
  }
}

const groupedConversations = computed(() => {
  const today = [];
  const previous7Days = [];
  const older = [];
  const now = Date.now();
  const oneDay = 24 * 60 * 60 * 1000;
  const sevenDays = 7 * oneDay;

  for (const c of conversations.value) {
    const timeVal = c.updated_at
      ? new Date(c.updated_at).getTime()
      : c.created_at
        ? new Date(c.created_at).getTime()
        : now;
    const diff = now - timeVal;
    if (diff < oneDay) {
      today.push(c);
    } else if (diff < sevenDays) {
      previous7Days.push(c);
    } else {
      older.push(c);
    }
  }
  return { today, previous7Days, older };
});

function quickRename(c, event) {
  event?.stopPropagation();
  const newTitle = window.prompt('请输入新对话标题：', c.title);
  if (!newTitle || !newTitle.trim() || newTitle.trim() === c.title) return;
  const id = c.id;
  execute(async () => {
    const item = await api.request(`/api/v1/conversations/${id}`, {
      method: 'PATCH',
      body: { title: newTitle.trim() },
    });
    upsert(conversations.value, item);
    if (cid.value === id) state.conversation = item;
  });
}

function quickDelete(c, event) {
  event?.stopPropagation();
  if (!window.confirm(`确定要删除对话“${c.title}”吗？作品仍将保留在作品库。`)) return;
  const id = c.id;
  execute(async () => {
    await api.request(`/api/v1/conversations/${id}`, { method: 'DELETE' });
    if (cid.value === id) {
      feed?.stop();
      view.abort();
      view = new AbortController();
      state.conversation = null;
      state.messages = [];
      state.runs = [];
      state.tasks = [];
      state.versions = {};
      window.history.replaceState(null, '', '/');
    }
    delete drafts[id];
    conversations.value = conversations.value.filter((item) => item.id !== id);
    notice.value = '对话已删除，作品仍保留在个人作品库。';
  });
}
watch(
  panel,
  (value, previous) => {
    if (value && !previous) panelFocus.value = drawerFocus || document.activeElement;
  },
  { flush: 'sync' },
);
const mobileQuery = window.matchMedia('(max-width: 620px)');
const resizeMobile = (event) => {
  mobile.value = event.matches;
  // 桌面允许两个独立侧栏；进入手机布局时不能留下两个重叠的模态抽屉。
  if (event.matches && historyOpen.value) navOpen.value = false;
};
mobileQuery.addEventListener('change', resizeMobile);
function toggleNav() {
  navOpen.value = !navOpen.value;
  if (mobile.value && navOpen.value) historyOpen.value = false;
}
function toggleHistory() {
  historyOpen.value = !historyOpen.value;
  if (mobile.value && historyOpen.value) navOpen.value = false;
}
watch([navOpen, historyOpen, mobile], async ([nav, history, isMobile], previous) => {
  if (!isMobile) {
    drawerFocus = null;
    return;
  }
  if (nav || history) {
    drawerFocus ||=
      previous[2] === false
        ? document.querySelector(`[aria-controls="${history ? 'history-nav' : 'main-nav'}"]`)
        : document.activeElement;
    await nextTick();
    if (!panel.value)
      document.querySelector(history ? '#history-nav button' : '#main-nav button')?.focus();
  } else {
    if (!panel.value && drawerFocus?.isConnected) drawerFocus.focus();
    drawerFocus = null;
  }
});
function logoutLocally() {
  feed?.stop();
  view.abort();
  view = new AbortController();
  api.clear();
  clear();
  clearReads();
  clearExit();
  user.value = null;
  capabilities.value = null;
  conversations.value = [];
  conversationCursor.value = null;
  window.history.replaceState(null, '', '/');
  props.initial.mode = 'login';
  props.initial.token = '';
  state.conversation = null;
  state.messages = [];
  state.runs = [];
  state.tasks = [];
  state.versions = {};
  state.cursor = '0';
  state.older = null;
  Object.keys(drafts).forEach((key) => delete drafts[key]);
  panel.value = null;
  refineVersion.value = null;
  notice.value = '';
  retry.value = null;
  navOpen.value = false;
  historyOpen.value = false;
  drawerFocus = null;
}
api.onUnauthorized(logoutLocally);
async function listConversations(more = false) {
  const result = await api.request(
    query('/conversations', { cursor: more ? conversationCursor.value : null }),
  );
  conversations.value = more ? [...conversations.value, ...result.items] : result.items;
  conversationCursor.value = result.next_cursor;
}
async function loadCapabilities() {
  capabilities.value = await api.request('/api/v1/capabilities');
}
function connect(id) {
  window.history.replaceState(null, '', '/?conversation=' + encodeURIComponent(id));
  feed?.stop();
  view.abort();
  view = new AbortController();
  state.conversation = conversations.value.find((c) => c.id === id) || { id, title: '创作对话' };
  state.messages = [];
  state.versions = {};
  state.tasks = [];
  state.runs = [];
  state.older = null;
  panel.value = null;
  notice.value = '';
  historyOpen.value = false;
  taskCursor.value = null;
  runCursor.value = null;
  feed = followConversation(api, state, id, {
    onAuth: logoutLocally,
    onError: (value) => {
      error.value = value;
    },
  });
}
async function signedIn(value) {
  user.value = value;
  props.initial.token = '';
  props.initial.mode = 'login';
  await execute(async () => {
    await api.getCsrf();
    await Promise.all([listConversations(), loadCapabilities()]);
    const selected = new URLSearchParams(window.location.search).get('conversation');
    if (selected && /^[0-9a-f-]{36}$/i.test(selected)) {
      if (!conversations.value.some((c) => c.id === selected))
        upsert(conversations.value, await api.request(`/api/v1/conversations/${selected}`));
      connect(selected);
    } else if (conversations.value.length) connect(conversations.value[0].id);
  });
}
onMounted(async () => {
  if (props.initial.mode !== 'login') {
    booting.value = false;
    return;
  }
  try {
    const value = await api.request('/api/v1/auth/me');
    await signedIn(value);
  } catch (e) {
    if (e.code !== 'AUTH_REQUIRED' && e.name !== 'AbortError') error.value = e;
  } finally {
    booting.value = false;
  }
});
onBeforeUnmount(() => {
  mobileQuery.removeEventListener('change', resizeMobile);
  feed?.stop();
  view.abort();
  api.clear();
  api.onUnauthorized(() => {});
});
function newConversation() {
  const key = actionKey();
  return execute(
    async () => {
      const item = await api.request('/api/v1/conversations', { method: 'POST', key, body: {} });
      upsert(conversations.value, item);
      connect(item.id);
    },
    { repeatable: true },
  );
}
function send() {
  if (!cid.value || (!draft.value.content.trim() && !draft.value.attachments.length) || retry.value)
    return;
  const id = cid.value,
    currentDraft = draft.value,
    key = actionKey(),
    content = currentDraft.content;
  const body = {
    client_message_id: actionKey(),
    content,
    attachment_version_ids: currentDraft.attachments.map((v) => v.id),
    generation_permission: currentDraft.permission,
  };
  if (currentDraft.selected) body.selected_version_id = currentDraft.selected.id;
  if (currentDraft.confirmation) body.reference_confirmation_id = currentDraft.confirmation.id;
  return execute(
    async () => {
      const result = await api.request(`/api/v1/conversations/${id}/messages`, {
        method: 'POST',
        key,
        body,
      });
      if (cid.value === id) {
        upsert(state.messages, result.message);
        state.messages.sort((a, b) => a.seq - b.seq);
        upsert(state.runs, result.run);
        notice.value = '消息已保存，文字轮次已排队；不表示生成完成。';
      }
      if (currentDraft.content === content) currentDraft.content = '';
      currentDraft.attachments = [];
      currentDraft.confirmation = null;
      currentDraft.selected = null;
    },
    { repeatable: true },
  );
}
function keydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!busy.value) send();
  }
}
function selectVersion(version) {
  if (!cid.value) {
    notice.value = '请先新建或打开一段对话，再选择具体版本。';
    return;
  }
  if (!version.media_type.startsWith('image/')) return;
  if (!draft.value.attachments.some((v) => v.id === version.id)) {
    if (
      draft.value.attachments.length >= (capabilities.value?.limits.max_message_attachments || 8)
    ) {
      notice.value = '已达到单次消息附件上限，请移除一张再选择。';
      return;
    }
    draft.value.attachments.push(version);
  }
  if (draft.value.selected?.id !== version.id) draft.value.confirmation = null;
  draft.value.selected = version;
  state.versions[version.id] = version;
  panel.value = null;
}
function removeAttachment(version) {
  draft.value.attachments = draft.value.attachments.filter((v) => v.id !== version.id);
  if (draft.value.selected?.id === version.id) {
    draft.value.selected = null;
    draft.value.confirmation = null;
  }
}
function confirmImage() {
  const id = cid.value,
    version = draft.value.selected,
    target = draft.value,
    key = actionKey();
  return execute(
    async () => {
      const result = await api.request(`/api/v1/conversations/${id}/reference-confirmations`, {
        method: 'POST',
        key,
        body: { version_id: version.id, purpose: 'video_first_frame' },
      });
      if (target.selected?.id === version.id) target.confirmation = result;
      notice.value = '已确认具体图片版本；新版不会替换这份确认。';
    },
    { repeatable: true },
  );
}
function upload(event) {
  const selectedFile = event.target.files[0];
  event.target.value = '';
  if (!selectedFile || !cid.value) return;
  if (
    !['image/png', 'image/jpeg', 'image/webp'].includes(selectedFile.type) ||
    selectedFile.size > capabilities.value.limits.max_upload_bytes
  ) {
    error.value = {
      detail: '请选择未超出单次上传大小的 JPEG、PNG 或 WebP 图片。服务端还会检查解码与像素。',
    };
    return;
  }
  const key = actionKey(),
    id = cid.value,
    target = draft.value;
  const body = new FormData();
  body.append('file', selectedFile);
  body.append('conversation_id', id);
  return execute(
    async () => {
      const result = await api.request('/api/v1/uploads', { method: 'POST', key, body });
      if (cid.value === id) selectVersion(result.version);
      else if (!target.attachments.some((v) => v.id === result.version.id))
        target.attachments.push(result.version);
      notice.value = '图片已保存为不可变版本，不是生成结果。';
    },
    { repeatable: true },
  );
}
function older() {
  const id = cid.value,
    signal = view.signal;
  return readOnly(async () => {
    const result = await api.request(
      query(`/conversations/${id}/messages`, { cursor: state.older }),
      { signal },
    );
    result.items.forEach((m) => upsert(state.messages, m));
    state.messages.sort((a, b) => a.seq - b.seq);
    state.older = result.next_cursor;
    await resolveVersions(
      result.items.flatMap((m) => [...m.attachment_version_ids, ...m.artifact_version_ids]),
      id,
      signal,
    );
  });
}
async function resolveVersions(ids, id, signal) {
  const missing = new Set(ids.filter((vid) => !state.versions[vid]));
  let cursor = null;
  if (!missing.size) return;
  do {
    const page = await api.request(
      query('/artifacts', { conversation_id: id, cursor, limit: 100 }),
      { signal },
    );
    for (const artifact of page.items) {
      let vc = null;
      do {
        const versions = await api.request(
          query(`/artifacts/${artifact.id}/versions`, { cursor: vc, limit: 100 }),
          { signal },
        );
        for (const v of versions.items)
          if (missing.has(v.id)) {
            state.versions[v.id] = v;
            missing.delete(v.id);
          }
        vc = versions.next_cursor;
      } while (vc && missing.size);
      if (!missing.size) return;
    }
    cursor = page.next_cursor;
  } while (cursor && missing.size);
}
async function loadTasks(more = false) {
  const id = cid.value,
    signal = view.signal;
  if (!id) return;
  const result = await api.request(
    query('/tasks', { conversation_id: id, cursor: more ? taskCursor.value : null }),
    { signal },
  );
  result.items.forEach((t) => upsert(state.tasks, t));
  taskCursor.value = result.next_cursor;
  await resolveVersions(
    result.items.flatMap((t) => t.output_version_ids),
    id,
    signal,
  );
}
function tasksPanel() {
  panel.value = 'tasks';
  return readOnly(() => loadTasks());
}
function taskAction(task, action) {
  return execute(async () => {
    try {
      const result = await api.request(`/api/v1/tasks/${task.id}/${action}`, { method: 'POST' });
      if (cid.value === result.conversation_id) upsert(state.tasks, result);
    } catch (e) {
      const result = await api.request(`/api/v1/tasks/${task.id}`);
      if (cid.value === result.conversation_id) upsert(state.tasks, result);
      throw e;
    }
  });
}
function stopRun(run) {
  return execute(async () => {
    const result = await api.request(`/api/v1/runs/${run.id}/cancel`, { method: 'POST' });
    if (cid.value === result.conversation_id) upsert(state.runs, result);
  });
}
function loadRuns() {
  return readOnly(async () => {
    const page = await api.request(
      query(`/conversations/${cid.value}/runs`, { cursor: runCursor.value }),
      { signal: view.signal },
    );
    page.items.forEach((r) => upsert(state.runs, r));
    runCursor.value = page.next_cursor;
  });
}
function refine(version = null) {
  if (!cid.value) {
    notice.value = '请先新建或打开对话。';
    return;
  }
  refineVersion.value = version;
  panel.value = 'refine';
}
function created(result) {
  if (result.version) {
    state.versions[result.version.id] = result.version;
    notice.value = '文案版本已保存。';
  } else {
    upsert(state.tasks, result);
    notice.value = '任务已持久受理，完成状态以实际保存为准。';
  }
  panel.value = null;
  refineVersion.value = null;
}
function saveText(content) {
  const key = actionKey(),
    body = { kind: 'text', title: '对话文案', content, conversation_id: cid.value };
  return execute(
    async () => {
      const result = await api.request('/api/v1/artifacts', { method: 'POST', key, body });
      state.versions[result.version.id] = result.version;
      notice.value = '已保存文案作品，没有再次调用模型。';
    },
    { repeatable: true },
  );
}
function deletedArtifact(aid) {
  for (const [id, version] of Object.entries(state.versions))
    if (version.artifact_id === aid) delete state.versions[id];
  for (const target of Object.values(drafts)) {
    target.attachments = target.attachments.filter((v) => v.artifact_id !== aid);
    if (target.selected?.artifact_id === aid) {
      target.selected = null;
      target.confirmation = null;
    }
  }
}
function rename() {
  const id = cid.value,
    body = { title: title.value };
  return execute(async () => {
    const item = await api.request(`/api/v1/conversations/${id}`, { method: 'PATCH', body });
    upsert(conversations.value, item);
    if (cid.value === id) state.conversation = item;
    panel.value = null;
  });
}
function deleteConversation() {
  const id = cid.value;
  return execute(async () => {
    await api.request(`/api/v1/conversations/${id}`, { method: 'DELETE' });
    feed?.stop();
    view.abort();
    view = new AbortController();
    delete drafts[id];
    conversations.value = conversations.value.filter((c) => c.id !== id);
    state.conversation = null;
    state.messages = [];
    state.runs = [];
    state.tasks = [];
    state.versions = {};
    panel.value = null;
    window.history.replaceState(null, '', '/');
    notice.value = '对话已删除，作品仍保留在个人作品库。';
  });
}
function logout() {
  return exitSession(async () => {
    await api.request('/api/v1/auth/logout', { method: 'POST' });
    logoutLocally();
  });
}
watch(
  () => state.conversation?.title,
  (value) => {
    if (value && cid.value) upsert(conversations.value, state.conversation);
  },
);
function closePanel() {
  panel.value = null;
  refineVersion.value = null;
}
function escape(event) {
  if (panel.value) return;
  if (event.key === 'Escape') {
    navOpen.value = false;
    historyOpen.value = false;
  }
  if (event.key === 'Tab' && mobile.value && (navOpen.value || historyOpen.value)) {
    const drawer = document.querySelector(historyOpen.value ? '#history-nav' : '#main-nav');
    const items = [...drawer.querySelectorAll('button:not(:disabled), a[href]')];
    const first = items[0],
      last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
}
</script>

<template>
  <div v-if="booting" class="auth-page" role="status">正在核对登录…</div>
  <template v-else-if="!user"
    ><ErrorNotice :error="error" /><AuthView :initial="initial" @login="signedIn"
  /></template>
  <div v-else class="shell" @keydown="escape">
    <button
      v-if="navOpen || historyOpen"
      class="drawer-scrim"
      aria-label="关闭侧栏抽屉"
      @click="
        navOpen = false;
        historyOpen = false;
      "
    ></button>
    <aside
      id="main-nav"
      class="sidebar"
      :class="{ expanded: navOpen }"
      :role="mobile && navOpen ? 'dialog' : undefined"
      :aria-modal="mobile && navOpen ? true : undefined"
      aria-label="主导航"
    >
      <div class="brand">
        <span class="brand-badge" aria-hidden="true">𝓥</span>
        <div class="brand-details nav-label">
          <div class="brand-name">OmniFlow <span class="brand-version">v3.1</span></div>
          <div class="brand-tagline">One Prompt, Omni Deliver</div>
        </div>
      </div>
      <button
        class="nav-item"
        aria-label="新建创作"
        :disabled="busy || !!retry"
        @click="newConversation"
      >
        <UiIcon name="plus" /><span class="nav-label">新建创作</span>
      </button>
      <button
        class="nav-item"
        aria-label="个人作品库"
        @click="
          panel = 'library';
          navOpen = false;
        "
      >
        <UiIcon name="image" /><span class="nav-label">素材与作品</span>
      </button>
      <button
        class="nav-item"
        aria-label="基本精修与参数创作"
        :disabled="!cid"
        @click="
          refine();
          navOpen = false;
        "
      >
        <UiIcon name="edit" /><span class="nav-label">基本精修</span>
      </button>
      <button
        class="nav-item"
        aria-label="账号"
        @click="
          panel = 'account';
          navOpen = false;
        "
      >
        <UiIcon name="account" /><span class="nav-label">{{ user.username }}</span>
      </button>
      <button
        v-if="user.role === 'admin'"
        class="nav-item"
        aria-label="管理员"
        @click="
          panel = 'admin';
          navOpen = false;
        "
      >
        <UiIcon name="settings" /><span class="nav-label">管理员</span>
      </button>
      <div class="sidebar-engine-card nav-label">
        <div class="engine-row">
          <span class="engine-indicator"></span>
          <span class="engine-title">Agnes AI 核心引擎</span>
        </div>
        <div class="engine-subtitle">Gemini 3.8 Flash 主控调度</div>
      </div>
      <p class="sidebar-note nav-label">每段对话<br />独立创作上下文</p>
    </aside>
    <aside
      v-if="historyOpen"
      id="history-nav"
      class="history-sidebar"
      :role="mobile ? 'dialog' : undefined"
      :aria-modal="mobile ? true : undefined"
      aria-label="对话历史"
    >
      <div class="split">
        <h2>最近的创作</h2>
        <button class="icon-button" aria-label="关闭历史" @click="historyOpen = false">
          <UiIcon name="close" />
        </button>
      </div>

      <!-- 分组会话列表：今天 -->
      <div v-if="groupedConversations.today.length" class="history-group">
        <div class="history-group-title">📌 今天</div>
        <button
          v-for="item in groupedConversations.today"
          :key="item.id"
          class="history-item"
          :class="{ selected: item.id === cid }"
          :aria-current="item.id === cid ? 'page' : undefined"
          :disabled="busy || !!retry"
          @click="connect(item.id)"
        >
          {{ item.title }}
        </button>
      </div>

      <!-- 分组会话列表：近 7 天 -->
      <div v-if="groupedConversations.previous7Days.length" class="history-group">
        <div class="history-group-title">🗓️ 近 7 天</div>
        <button
          v-for="item in groupedConversations.previous7Days"
          :key="item.id"
          class="history-item"
          :class="{ selected: item.id === cid }"
          :aria-current="item.id === cid ? 'page' : undefined"
          :disabled="busy || !!retry"
          @click="connect(item.id)"
        >
          {{ item.title }}
        </button>
      </div>

      <!-- 分组会话列表：更早 -->
      <div v-if="groupedConversations.older.length" class="history-group">
        <div class="history-group-title">📂 更早</div>
        <button
          v-for="item in groupedConversations.older"
          :key="item.id"
          class="history-item"
          :class="{ selected: item.id === cid }"
          :aria-current="item.id === cid ? 'page' : undefined"
          :disabled="busy || !!retry"
          @click="connect(item.id)"
        >
          {{ item.title }}
        </button>
      </div>

      <p v-if="!conversations.length">还没有对话。</p>
      <button
        v-if="conversationCursor"
        :disabled="reading"
        @click="readOnly(() => listConversations(true))"
      >
        加载更多对话
      </button>
    </aside>
    <main class="workspace">
      <header class="topbar">
        <div class="top-left">
          <button
            aria-label="展开或收起主导航"
            :aria-expanded="navOpen"
            aria-controls="main-nav"
            @click="toggleNav"
          >
            <UiIcon name="menu" />
          </button>
          <button
            aria-label="展开或收起对话历史"
            :aria-expanded="historyOpen"
            aria-controls="history-nav"
            @click="toggleHistory"
          >
            <UiIcon name="history" />
          </button>
          <span class="title">{{ state.conversation?.title || '新的想法' }}</span>
          <span class="top-agent-pill">
            <span class="top-agent-dot"></span>
            Gemini 3.8 Flash 主控 · Multi-Agent 架构
          </span>
        </div>
        <div class="top-right">
          <button
            type="button"
            class="quick-experience-btn"
            title="体验全案生成"
            :disabled="busy || !!retry"
            @click="experienceAllInOne"
          >
            <span>🚀</span>
            <span class="btn-text">体验全案</span>
          </button>
          <button :disabled="!cid" @click="tasksPanel">
            任务<span
              v-if="
                state.tasks.filter((t) => !['completed', 'failed', 'canceled'].includes(t.status))
                  .length
              "
            >
              ·
              {{
                state.tasks.filter((t) => !['completed', 'failed', 'canceled'].includes(t.status))
                  .length
              }}</span
            >
          </button>
          <button
            aria-label="对话设置"
            :disabled="!cid"
            @click="
              title = state.conversation.title;
              panel = 'conversation';
            "
          >
            <UiIcon name="more" />
          </button>
        </div>
      </header>
      <div class="connection-bar" :class="{ 'connection-trouble': connectionTrouble }">
        <span role="status">{{ cid ? state.connection : '新建对话不会调用模型' }}</span
        ><button
          v-if="cid && connectionTrouble"
          class="subtle"
          :disabled="busy || !!retry"
          @click="connect(cid)"
        >
          重新连接
        </button>
      </div>
      <ErrorNotice v-if="!panel" :error="error" :retry="retry" :busy="busy || loggingOut" />
      <ErrorNotice v-if="!panel" :error="readError" />
      <div v-if="retry" class="warning-box">
        <p>暂勿重复发送新动作；这里保留原编号以核对是否已受理。</p>
        <button
          @click="
            retry = null;
            error = null;
          "
        >
          放弃本次重试（不撤销已受理工作）
        </button>
      </div>
      <p v-if="notice" class="notice" role="status">
        {{ notice
        }}<button class="icon-button" aria-label="关闭提示" @click="notice = ''">
          <UiIcon name="close" />
        </button>
      </p>
      <TranscriptView
        :key="cid || 'empty'"
        ref="transcript"
        :state="state"
        :selected-id="draft.selected?.id"
        :confirmed-id="draft.confirmation?.version_id"
        :busy="busy"
        @older="older"
        @select="selectVersion"
        @refine="refine"
        @save-text="saveText"
        @stop="stopRun"
        @cancel-task="taskAction($event, 'cancel')"
        @recover-task="taskAction($event, 'recover')"
        @fill-prompt="fillPrompt"
      />
      <footer class="composer-area">
        <div v-if="cid && !busy && !retry" class="prompt-chips-bar" aria-label="快捷灵感标签">
          <span class="chips-label">灵感快捷选：</span>
          <div class="chips-scroll">
            <button
              v-for="(chip, cIdx) in promptChips"
              :key="cIdx"
              type="button"
              class="chip-btn"
              @click="appendPromptChip(chip)"
            >
              + {{ chip }}
            </button>
          </div>
        </div>
        <form class="composer" @submit.prevent="send">
          <div v-if="draft.attachments.length" class="attachment-list">
            <span v-for="v in draft.attachments" :key="v.id"
              ><button
                type="button"
                class="attachment-pick"
                :aria-pressed="draft.selected?.id === v.id"
                :aria-label="`选中图片版本 ${v.version_number}，编号 ${v.id}`"
                :disabled="busy || !!retry"
                @click="
                  draft.selected = v;
                  draft.confirmation = null;
                "
              >
                <img :src="contentPath(v)" alt="参考图缩略图" />v{{ v.version_number }}</button
              ><button
                type="button"
                :disabled="busy || !!retry"
                aria-label="移除参考图"
                @click="removeAttachment(v)"
              >
                <UiIcon name="close" /></button
            ></span>
          </div>
          <p v-if="draft.selected" class="reference-state">
            已选版本 {{ draft.selected.version_number }} · {{ draft.selected.id.slice(-6)
            }}<button type="button" :disabled="busy || !!retry" @click="confirmImage">
              {{ draft.confirmation ? '已确认视频首帧' : '确认用这张做视频' }}
            </button>
          </p>
          <textarea
            v-model="draft.content"
            rows="2"
            aria-label="创作需求"
            placeholder="继续描述你的想法……"
            :maxlength="capabilities?.limits.max_message_chars"
            :disabled="busy || !!retry || !cid"
            @keydown="keydown"
          ></textarea>
          <div class="composer-tools">
            <div class="actions">
              <button
                type="button"
                aria-label="上传参考图"
                :disabled="
                  !cid ||
                  busy ||
                  !!retry ||
                  !capabilities ||
                  draft.attachments.length >= capabilities.limits.max_message_attachments
                "
                @click="file.click()"
              >
                <UiIcon name="plus" /></button
              ><input
                ref="file"
                class="sr-only"
                tabindex="-1"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                @change="upload"
              /><button type="button" @click="panel = 'library'">素材</button>
              <details>
                <summary>创作设置</summary>
                <label class="check"
                  ><input
                    type="checkbox"
                    :checked="draft.permission === 'discuss_only'"
                    :disabled="busy || !!retry"
                    @change="
                      draft.permission = $event.target.checked ? 'discuss_only' : 'requested_only'
                    "
                  />仅讨论，不创建媒体</label
                >
                <p>
                  {{ capabilities?.text.model || '正在读取型号' }} ·
                  {{ reasonName(capabilities?.text.availability.reason) || '可用' }}
                </p>
                <p>只执行明确要求；服务不可用时保留真实失败状态，不自动收费回退。</p>
                <button type="button" :disabled="reading" @click="readOnly(loadCapabilities)">
                  刷新能力
                </button>
              </details>
            </div>
            <button
              v-if="!cid"
              type="button"
              class="primary"
              :disabled="busy || !!retry"
              @click="newConversation"
            >
              新建对话</button
            ><button
              v-else
              class="send"
              aria-label="发送消息"
              :aria-busy="busy"
              aria-describedby="composer-help"
              :disabled="busy || !!retry || (!draft.content.trim() && !draft.attachments.length)"
            >
              <UiIcon name="send" />
            </button>
          </div>
        </form>
        <p id="composer-help" class="footnote">
          <span class="desktop-shortcut">Enter 发送 · Shift+Enter 换行 · </span
          >{{
            busy
              ? '正在提交…'
              : retry
                ? '请先核对原请求'
                : !draft.content.trim() && !draft.attachments.length
                  ? '输入需求或添加参考图后发送'
                  : '任务完成以文件保存为准'
          }}
        </p>
      </footer>
    </main>
    <ModalShell
      v-if="panel"
      :fixed-body="panel === 'refine'"
      :close-blocked="panel === 'refine' && !!refineForm?.pending"
      :return-focus="panelFocus"
      :title="
        {
          library: '素材与作品',
          refine: '基本精修',
          admin: '管理员',
          account: '账号',
          tasks: '真实任务状态',
          conversation: '对话设置',
        }[panel]
      "
      @close="closePanel"
    >
      <ErrorNotice :error="error" :retry="retry" :busy="busy || loggingOut" />
      <ErrorNotice :error="readError" />
      <LibraryPanel
        v-if="panel === 'library'"
        @select="selectVersion"
        @refine="refine"
        @deleted="deletedArtifact"
      />
      <RefinePanel
        v-if="panel === 'refine'"
        ref="refineForm"
        :version="refineVersion"
        :cid="cid"
        :capabilities="capabilities"
        @created="created"
        @dismiss="closePanel"
      />
      <AdminPanel v-if="panel === 'admin' && user.role === 'admin'" />
      <section v-if="panel === 'account'" class="stack">
        <p>账号：{{ user.username }} · {{ user.role === 'admin' ? '管理员' : '创作者' }}</p>
        <p>网站登录与 Google 登录独立。修改密码请联系管理员人工核验；退出网站不修改提供方授权。</p>
        <p>当前不备份，不承诺误删或磁盘损坏后的恢复。</p>
        <p>
          退出会丢弃本页草稿和未确认请求的重试信息，但不会取消已受理的工作；再次登录后请查看任务与消息状态。
        </p>
        <ErrorNotice :error="logoutError" />
        <button :disabled="loggingOut" :aria-busy="loggingOut" @click="logout">退出登录</button>
      </section>
      <section v-if="panel === 'tasks'" class="stack">
        <p>关页面不会取消工作。停止文字不等于取消媒体；不展示虚构百分比。</p>
        <button :disabled="reading" @click="readOnly(() => loadTasks())">刷新任务状态</button
        ><TaskCard
          v-for="task in state.tasks"
          :key="task.id"
          :task="task"
          :versions="state.versions"
          :busy="busy"
          @cancel="taskAction($event, 'cancel')"
          @recover="taskAction($event, 'recover')"
          @select="selectVersion"
          @refine="refine"
        />
        <p v-if="!state.tasks.length">暂无媒体任务。</p>
        <button v-if="taskCursor" :disabled="reading" @click="readOnly(() => loadTasks(true))">
          加载更多任务</button
        ><button :disabled="reading" @click="loadRuns">
          {{ runCursor ? '加载更早文字轮次' : '查看文字轮次历史' }}
        </button>
      </section>
      <section v-if="panel === 'conversation'" class="stack">
        <form class="stack" @submit.prevent="rename">
          <label>对话标题<input v-model="title" maxlength="120" required /></label
          ><button :disabled="busy || !title.trim()">保存标题</button>
        </form>
        <div class="warning-box">
          <p>删除对话不删除作品。存在未结束文字或媒体工作时会拒绝删除。当前没有备份。</p>
          <button
            class="danger"
            :disabled="busy"
            @click="
              panel = 'conversation';
              deleteConversation();
            "
          >
            确认删除这段对话（保留作品）
          </button>
        </div>
      </section>
    </ModalShell>
  </div>
</template>
