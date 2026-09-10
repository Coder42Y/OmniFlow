<script setup>
import { statusName, engineName } from '../actions.js';
import MediaCard from './MediaCard.vue';
defineProps({
  task: Object,
  versions: Object,
  busy: Boolean,
  shownVersions: { type: Array, default: () => [] },
  selectedId: String,
  confirmedId: String,
});
const emit = defineEmits(['cancel', 'recover', 'select', 'refine']);
</script>
<template>
  <article class="task-card">
    <div class="split">
      <strong>{{
        { image: '图片任务', ai_video: 'AI 视频任务', local_motion: '本地运镜任务' }[task.kind]
      }}</strong
      ><span class="status" :data-status="task.status">{{ statusName(task.status) }}</span>
    </div>
    <small v-if="task.kind === 'local_motion'">本地运镜 · 非 AI 动态生成</small>
    <details>
      <summary>任务详情</summary>
      <p>来源：{{ engineName(task.execution_engine) }}</p>
    </details>
    <p class="muted">
      请求：{{ task.requested_parameters.aspect_ratio
      }}<template v-if="task.requested_parameters.seconds">
        · {{ task.requested_parameters.seconds }} 秒</template
      ><template v-if="task.requested_parameters.size_tier">
        · {{ task.requested_parameters.size_tier }}</template
      >；实际规格以保存后的作品为准。
    </p>
    <p v-if="task.error" class="error-text">{{ task.error.message }}（{{ task.error.code }}）</p>
    <p v-if="['submission_unknown', 'needs_reconciliation'].includes(task.status)">
      结果尚未核清，不会自动再生成一次。
    </p>
    <div class="actions">
      <button v-if="task.can_cancel" :disabled="busy" @click="emit('cancel', task)">
        取消排队任务</button
      ><button v-if="task.can_recover" :disabled="busy" @click="emit('recover', task)">
        续查／恢复下载（不重新生成）
      </button>
    </div>
    <p v-if="task.output_version_ids.some((id) => shownVersions.includes(id))" class="muted">
      作品已展示在对应消息中。
    </p>
    <MediaCard
      v-for="id in task.output_version_ids.filter((id) => !shownVersions.includes(id))"
      :key="id"
      :version="versions[id]"
      :selected="selectedId === id"
      :confirmed="confirmedId === id"
      @select="emit('select', $event)"
      @refine="emit('refine', $event)"
    />
  </article>
</template>
