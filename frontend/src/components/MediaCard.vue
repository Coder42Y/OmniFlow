<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue';
import { api, contentPath } from '../api.js';
import { engineName, mediaName, fileSize } from '../actions.js';
import UiIcon from './UiIcon.vue';
const props = defineProps({
  version: Object,
  selectable: { type: Boolean, default: true },
  selected: Boolean,
  confirmed: Boolean,
});
const emit = defineEmits(['select', 'refine', 'layout']);
const broken = ref(false),
  text = ref(''),
  loading = ref(false),
  mediaLoading = ref(true),
  attempt = ref(0);
const path = computed(() => contentPath(props.version));
let controller;
watch(
  () => props.version?.id,
  () => {
    controller?.abort();
    broken.value = false;
    text.value = '';
    loading.value = false;
    mediaLoading.value = true;
  },
);
onBeforeUnmount(() => controller?.abort());
function loaded() {
  mediaLoading.value = false;
  emit('layout');
}
function failed() {
  broken.value = true;
  mediaLoading.value = false;
  emit('layout');
}
function retryRead() {
  broken.value = false;
  mediaLoading.value = true;
  attempt.value++;
  if (props.version?.media_type === 'text/plain') readText();
}
async function readText() {
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  loading.value = true;
  try {
    const response = await api.request(path.value, { raw: true, signal });
    const result = await response.text();
    if (!signal.aborted) text.value = result;
  } catch (error) {
    if (!signal.aborted) failed();
  } finally {
    if (!signal.aborted) {
      loading.value = false;
      emit('layout');
    }
  }
}
</script>
<template>
  <figure
    class="media-card"
    :class="{ portrait: version?.height > version?.width, 'is-selected': selected }"
  >
    <template v-if="version && path && !broken">
      <a
        v-if="version.media_type.startsWith('image/')"
        class="media-preview"
        :href="path"
        target="_blank"
        rel="noopener noreferrer"
        aria-label="查看原图（新窗口）"
        :style="{ aspectRatio: `${version.width} / ${version.height}` }"
      >
        <img
          :key="attempt"
          :src="path"
          :width="version.width"
          :height="version.height"
          loading="lazy"
          alt="作品图片"
          @load="loaded"
          @error="failed"
        />
      </a>
      <video
        v-else-if="version.media_type === 'video/mp4'"
        :key="attempt"
        :src="path"
        :style="{ aspectRatio: `${version.width || 16} / ${version.height || 9}` }"
        controls
        playsinline
        preload="metadata"
        @loadedmetadata="loaded"
        @error="failed"
      ></video>
      <template v-else>
        <p v-if="text" class="plain-text">{{ text }}</p>
        <button v-else :disabled="loading" :aria-busy="loading" @click="readText">
          {{ loading ? '正在读取文案…' : '查看文案内容' }}
        </button>
      </template>
      <figcaption>
        <strong
          >版本 {{ version.version_number }} ·
          {{ mediaName(version.execution_engine, version.media_type) }}</strong
        >
        <small v-if="mediaLoading && version.media_type !== 'text/plain'" role="status"
          >媒体加载中…</small
        >
        <small
          >实际：<template v-if="version.width"
            >{{ version.width }} × {{ version.height }} · </template
          ><template v-if="version.duration_seconds != null"
            >{{ Number(version.duration_seconds.toFixed(2)) }} 秒 · </template
          >{{ fileSize(version.byte_size) }}</small
        >
        <span v-if="confirmed" class="status" data-status="completed">已确认视频首帧</span>
        <span v-else-if="selected" class="status">已选为参考</span>
        <details class="version-details">
          <summary>版本详情</summary>
          <p>来源：{{ engineName(version.execution_engine) }}</p>
          <p>版本编号：{{ version.id }}</p>
          <p>
            原始大小：{{ version.byte_size }} 字节<template v-if="version.duration_seconds != null"
              >；实际时长：{{ version.duration_seconds }} 秒</template
            >
          </p>
        </details>
      </figcaption>
      <div class="actions">
        <a :href="contentPath(version, true)" download><UiIcon name="download" />下载此版本</a>
        <button
          v-if="selectable && version.media_type.startsWith('image/')"
          :aria-pressed="selected"
          @click="emit('select', version)"
        >
          {{ selected ? '已选为参考' : '选为参考图' }}
        </button>
        <button v-if="selectable" @click="emit('refine', version)">基本精修</button>
      </div>
    </template>
    <div v-else-if="broken" class="media-unavailable">
      <p>此版本暂不可查看，可能是读取中断。其他作品不受影响。</p>
      <button @click="retryRead">重试读取</button>
    </div>
    <p v-else class="muted">此版本不可访问，其他作品不受影响。</p>
  </figure>
</template>
