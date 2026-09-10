<script setup>
import { computed, ref, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import WaveRail from './WaveRail.vue';
import MediaCard from './MediaCard.vue';
import TaskCard from './TaskCard.vue';
import { statusName } from '../actions.js';
import { renderMarkdown } from '../markdown.js';

const props = defineProps({
  state: Object,
  busy: Boolean,
  selectedId: String,
  confirmedId: String,
});
const shownVersions = computed(() =>
  props.state.messages.flatMap((m) => [...m.attachment_version_ids, ...m.artifact_version_ids]),
);
const emit = defineEmits([
  'older',
  'select',
  'refine',
  'save-text',
  'stop',
  'cancel-task',
  'recover-task',
  'fill-prompt',
]);
const scroller = ref(),
  content = ref(),
  selected = ref(0),
  atBottom = ref(true),
  height = ref(400),
  copiedId = ref(null);

let copyTimer;
function copyMessage(id, text) {
  if (!text) return;
  navigator.clipboard
    .writeText(text)
    .then(() => {
      copiedId.value = id;
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => {
        copiedId.value = null;
      }, 2000);
    })
    .catch(() => {});
}

const promptPillOptions = [
  {
    icon: '⚡',
    title: '全案创作：男士淡雅冷香',
    desc: '主打木质冷香，包含口播文案、商业海报与短视频',
    text: '帮我生成一套黑曜石男士淡雅冷香的带货全案，主打24小时持久木质香与高冷商务气场，包含口播、海报与运镜短视频',
  },
  {
    icon: '💄',
    title: '短视频：小红书爆款美妆',
    desc: '聚焦水光唇釉爆闪质感与特写镜头',
    text: '为一款微醺肉桂色水光唇釉策划一组高光特写海报与运镜视频，突出玻璃唇釉晶莹剔透感',
  },
  {
    icon: '⌚',
    title: '视觉海报：极简智能手表',
    desc: '深邃哑光黑钛金属质感，冷光氛围',
    text: '生成一张极简主义智能商务手表的商业海报，哑光钛金属表壳，深色背景，微冷光勾勒边缘',
  },
  {
    icon: '🌿',
    title: '种草脚本：有机绿茶护肤',
    desc: '突出自然原生态与抗氧舒缓修护',
    text: '为高山有机绿茶鲜萃爽肤水写一段15秒小红书种草口播，突出晨间醒肤与舒缓修护',
  },
];

const turns = computed(() =>
  props.state.messages
    .filter((m) => m.role === 'user')
    .map((m) => ({
      id: m.id,
      question: m.content || '参考图',
      answer:
        props.state.messages.find((a) => a.role === 'assistant' && a.run_id === m.run_id)
          ?.content || '',
    })),
);
let observer,
  anchor = null,
  updating = false;
function rememberAnchor() {
  const root = scroller.value;
  if (!root) return;
  const top = root.getBoundingClientRect().top;
  const nodes = [...root.querySelectorAll('[data-message]')];
  const item = nodes.find((el) => el.getBoundingClientRect().bottom > top);
  anchor = item ? { id: item.dataset.message, y: item.getBoundingClientRect().top } : null;
}
function onScroll() {
  const root = scroller.value;
  if (!root) return;
  atBottom.value = root.scrollHeight - root.scrollTop - root.clientHeight < 70;
  const top = root.getBoundingClientRect().top;
  let index = 0;
  turns.value.forEach((turn, i) => {
    const el = document.getElementById('message-' + turn.id);
    if (el && el.getBoundingClientRect().top <= top + 50) index = i;
  });
  if (root.scrollHeight - root.scrollTop - root.clientHeight < 5)
    index = Math.max(0, turns.value.length - 1);
  selected.value = index;
  if (!updating) rememberAnchor();
}
function layout() {
  const root = scroller.value;
  if (!root) return;
  updating = true;
  if (atBottom.value) root.scrollTop = root.scrollHeight;
  else if (anchor) {
    const el = document.getElementById('message-' + anchor.id);
    if (el) root.scrollTop += el.getBoundingClientRect().top - anchor.y;
  }
  height.value = root.clientHeight;
  updating = false;
  onScroll();
}
function latest() {
  atBottom.value = true;
  scroller.value.scrollTop = scroller.value.scrollHeight;
  onScroll();
}
function jump(index) {
  const node = document.getElementById('message-' + turns.value[index]?.id);
  if (!node) return;
  scroller.value.scrollTop +=
    node.getBoundingClientRect().top - scroller.value.getBoundingClientRect().top - 26;
  selected.value = index;
  // 浏览器可能已将目标位置夹到滚动底部。不能强制显示「回到最新」：
  // 它占据布局高度，会让迟到的 scroll/ResizeObserver 把末轮标记退回上一轮。
  // 与普通滚动使用同一距离判定，保留旧消息锚点而不制造一次额外的高度变化。
  atBottom.value =
    scroller.value.scrollHeight - scroller.value.scrollTop - scroller.value.clientHeight < 70;
  rememberAnchor();
  node.classList.remove('landing');
  void node.offsetWidth;
  node.classList.add('landing');
}
watch(
  () =>
    props.state.messages
      .map((m) => `${m.id}:${m.content.length}:${m.artifact_version_ids.join(',')}`)
      .join('|'),
  async () => {
    await nextTick();
    layout();
  },
);
onMounted(() => {
  observer = new ResizeObserver(layout);
  observer.observe(scroller.value);
  observer.observe(content.value);
  layout();
});
onBeforeUnmount(() => observer?.disconnect());
defineExpose({ latest });
</script>
<template>
  <section class="stage" aria-label="对话与历史定位">
    <div
      id="transcript"
      ref="scroller"
      class="transcript"
      role="region"
      tabindex="0"
      aria-label="创作对话记录"
      @scroll.passive="onScroll"
    >
      <div ref="content" class="messages">
        <button v-if="state.older" :disabled="busy" @click="emit('older')">加载更早消息</button>
        <div v-if="!state.messages.length" class="empty">
          <div class="empty-header">
            <div class="empty-icon">⚡</div>
            <h1>把想法变成作品</h1>
            <p>好商品，值得被更多人看见。咨询、文案、图片或视频，从一句需求开始。</p>
            <p class="empty-subnote">没有参考图的视频，先确认视觉稿；本地运镜会明确标注。</p>
          </div>
          <div class="quick-prompts-grid" aria-label="高频场景灵感推荐">
            <button
              v-for="(p, idx) in promptPillOptions"
              :key="idx"
              type="button"
              class="quick-prompt-card"
              @click="emit('fill-prompt', p.text)"
            >
              <span class="prompt-icon">{{ p.icon }}</span>
              <div class="prompt-info">
                <div class="prompt-title">{{ p.title }}</div>
                <div class="prompt-desc">{{ p.desc }}</div>
              </div>
            </button>
          </div>
        </div>
        <article
          v-for="message in state.messages"
          :id="'message-' + message.id"
          :key="message.id"
          class="turn"
          :data-message="message.id"
        >
          <div :class="message.role === 'user' ? 'user-row' : 'assistant-row'">
            <div class="message-avatar" :class="message.role" aria-hidden="true">
              {{ message.role === 'user' ? '我' : '⚡' }}
            </div>
            <div :class="message.role === 'user' ? 'user-message' : 'assistant-message'">
              <span class="sr-only">{{ message.role === 'user' ? '你' : '助手' }}：</span>
              <div
                v-if="message.role === 'assistant' && message.content"
                class="plain-text chat-markdown-content"
                v-html="renderMarkdown(message.content)"
              ></div>
              <p v-else class="plain-text">
                {{ message.content || (message.status === 'queued' ? '等待处理…' : '') }}
              </p>
              <div v-if="message.role === 'assistant' && message.content" class="message-meta-bar">
                <span class="model-badge">Gemini 3.8 Flash</span>
                <button
                  type="button"
                  class="copy-btn subtle"
                  title="复制全文"
                  @click="copyMessage(message.id, message.content)"
                >
                  {{ copiedId === message.id ? '已复制 ✓' : '复制全文' }}
                </button>
              </div>
            </div>
          </div>
          <small v-if="message.status !== 'completed'" class="muted">{{
            statusName(message.status)
          }}</small>
          <button
            v-if="message.role === 'assistant' && message.content"
            class="subtle save-text"
            @click="emit('save-text', message.content)"
          >
            保存为文案作品
          </button>
          <MediaCard
            v-for="id in [
              ...new Set([...message.attachment_version_ids, ...message.artifact_version_ids]),
            ]"
            :key="id"
            :version="state.versions[id]"
            :selected="selectedId === id"
            :confirmed="confirmedId === id"
            @layout="layout"
            @select="emit('select', $event)"
            @refine="emit('refine', $event)"
          />
        </article>
        <section v-if="state.tasks.length" class="stack" aria-label="对话媒体任务">
          <TaskCard
            v-for="task in state.tasks"
            :key="task.id"
            :task="task"
            :versions="state.versions"
            :shown-versions="shownVersions"
            :selected-id="selectedId"
            :confirmed-id="confirmedId"
            :busy="busy"
            @cancel="emit('cancel-task', $event)"
            @recover="emit('recover-task', $event)"
            @select="emit('select', $event)"
            @refine="emit('refine', $event)"
          />
        </section>
        <section v-if="state.runs.length" class="run-list" aria-label="文字轮次状态">
          <div v-for="run in state.runs" :key="run.id" class="run-row">
            <span>文字轮次 · {{ statusName(run.status) }}</span
            ><small v-if="run.error">{{ run.error.message }}（{{ run.error.code }}）</small
            ><button
              v-if="['queued', 'running'].includes(run.status)"
              :disabled="busy"
              @click="emit('stop', run)"
            >
              停止此轮文字
            </button>
          </div>
          <p class="muted">停止文字只阻止继续回复及新增动作，不会取消已受理的媒体任务。</p>
        </section>
      </div>
    </div>
    <WaveRail
      :turns="turns"
      :selected="selected"
      :available="height"
      @jump="jump"
      @wheel="scroller.scrollTop += $event"
    />
    <button v-if="!atBottom" class="back-latest" @click="latest">↓ 回到最新</button>
  </section>
</template>
