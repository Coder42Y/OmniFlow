<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import { api, query } from '../api.js';
import { useActions } from '../actions.js';
import ErrorNotice from './ErrorNotice.vue';
import MediaCard from './MediaCard.vue';
const emit = defineEmits(['select', 'refine', 'deleted']);
const items = ref([]),
  cursor = ref(null),
  kind = ref(''),
  selected = ref(null),
  versions = ref([]),
  versionCursor = ref(null),
  deleting = ref(false),
  receipt = ref(null);
const { busy, error, execute } = useActions();
const controller = new AbortController(),
  signal = controller.signal;
onBeforeUnmount(() => controller.abort());
async function load(more = false) {
  const page = await api.request(
    query('/artifacts', { kind: kind.value, cursor: more ? cursor.value : null }),
    { signal },
  );
  items.value = more ? [...items.value, ...page.items] : page.items;
  cursor.value = page.next_cursor;
}
function list(more = false) {
  return execute(() => load(more));
}
function open(item, more = false) {
  return execute(async () => {
    const page = await api.request(
      query(`/artifacts/${item.id}/versions`, { cursor: more ? versionCursor.value : null }),
      { signal },
    );
    selected.value = item;
    versions.value = more ? [...versions.value, ...page.items] : page.items;
    versionCursor.value = page.next_cursor;
    deleting.value = false;
  });
}
function remove() {
  const item = selected.value;
  return execute(async () => {
    receipt.value = await api.request(`/api/v1/artifacts/${item.id}`, { method: 'DELETE', signal });
    emit('deleted', item.id);
    selected.value = null;
    versions.value = [];
    deleting.value = false;
    await load();
  });
}
onMounted(() => list());
</script>
<template>
  <section class="stack">
    <p>个人作品库。只在你明确选择后，才将具体版本引用到当前对话。</p>
    <ErrorNotice :error="error" />
    <p v-if="receipt" role="status">
      在线访问已撤销，文件清理目标：{{
        new Date(receipt.purge_target_at).toLocaleString()
      }}。不提供备份恢复。
    </p>
    <template v-if="!selected">
      <label
        >作品类型<select v-model="kind" :disabled="busy" @change="list()">
          <option value="">全部</option>
          <option value="image">图片</option>
          <option value="video">视频</option>
          <option value="text">文案</option>
        </select></label
      >
      <p v-if="!items.length && !busy" class="empty">还没有保存的作品。</p>
      <button
        v-for="item in items"
        :key="item.id"
        class="list-item"
        :disabled="busy"
        @click="open(item)"
      >
        <span>{{ item.title }}</span
        ><small>{{ item.kind }} · {{ item.version_count }} 个版本</small>
      </button>
      <button v-if="cursor" :disabled="busy" @click="list(true)">加载更多作品</button>
    </template>
    <template v-else>
      <div class="split">
        <button
          @click="
            selected = null;
            versions = [];
          "
        >
          返回作品库
        </button>
        <h3>{{ selected.title }}</h3>
      </div>
      <MediaCard
        v-for="version in versions"
        :key="version.id"
        :version="version"
        @select="emit('select', $event)"
        @refine="emit('refine', $event)"
      />
      <button v-if="versionCursor" :disabled="busy" @click="open(selected, true)">
        加载更早版本
      </button>
      <button class="danger" :disabled="busy" @click="deleting = true">
        删除整个作品及所有版本
      </button>
      <div v-if="deleting" class="warning-box">
        <p>
          删除后立即撤销在线访问，不会删除已完成的派生视频。正在使用的作品不能删除。当前没有备份，此操作无法撤销。
        </p>
        <button class="danger" :disabled="busy" @click="remove">确认永久删除作品</button
        ><button @click="deleting = false">保留作品</button>
      </div>
    </template>
  </section>
</template>
