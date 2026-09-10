<script setup>
defineProps({ error: Object, retry: Function, busy: Boolean });
</script>
<template>
  <div v-if="error" class="error-notice" role="alert">
    <p>{{ error.detail || error.message || '操作未完成，请刷新状态。' }}</p>
    <details v-if="error.code" class="version-details">
      <summary>错误详情</summary>
      <p>
        {{ error.code
        }}<template v-if="error.request_id"> · 追踪编号 {{ error.request_id }}</template>
      </p>
    </details>
    <p v-if="error.retryAfter">请至少等待 {{ error.retryAfter }} 秒后再尝试。</p>
    <button v-if="retry" type="button" :disabled="busy" @click="retry()">重试同一动作</button>
  </div>
</template>
