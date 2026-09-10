<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue';
import { api, actionKey, contentPath } from '../api.js';
import { useActions, reasonName } from '../actions.js';
import ErrorNotice from './ErrorNotice.vue';
const props = defineProps({ version: Object, cid: String, capabilities: Object });
const emit = defineEmits(['created', 'dismiss']);
const controller = new AbortController(),
  signal = controller.signal;
onBeforeUnmount(() => controller.abort());
const kind = ref(
  props.version?.media_type === 'text/plain'
    ? 'text'
    : props.version?.media_type === 'video/mp4'
      ? 'ai_video'
      : 'image',
);
const prompt = ref(''),
  title = ref('文案作品'),
  ratio = ref(''),
  size = ref(''),
  seconds = ref(null),
  mode = ref('keyframe'),
  motion = ref('dolly_in'),
  confirmation = ref(null);
const { busy, error, retry, execute, clear } = useActions();
// 未确认的写入必须保留原闭包与幂等键，不能通过关闭窗口无意间丢弃。
const pending = computed(() => busy.value || !!retry.value);
defineExpose({ pending });
function abandon() {
  if (busy.value || !retry.value) return;
  clear();
  emit('dismiss');
}
const image = computed(() =>
  props.version?.media_type.startsWith('image/') ? props.version : null,
);
const cap = computed(() => props.capabilities?.[kind.value]);
const available = computed(
  () =>
    kind.value === 'text' ||
    (kind.value === 'local_motion' ? cap.value?.available : cap.value?.availability?.available),
);
const reason = computed(() =>
  reasonName(kind.value === 'local_motion' ? cap.value?.reason : cap.value?.availability?.reason),
);
const ratios = computed(() =>
  kind.value === 'local_motion' ? ['9:16', '16:9'] : cap.value?.ratios || [],
);
function defaults() {
  ratio.value = '';
  size.value = '';
  seconds.value = null;
  confirmation.value = null;
  if (!image.value) mode.value = 'keyframe';
}
watch(kind, defaults);
onMounted(() =>
  execute(async () => {
    if (kind.value === 'text' && props.version) {
      const response = await api.request(contentPath(props.version), { raw: true, signal });
      prompt.value = await response.text();
    } else if (props.version?.source_task_id) {
      const task = await api.request(`/api/v1/tasks/${props.version.source_task_id}`, { signal });
      if (task.conversation_id === props.cid) {
        const old = task.requested_parameters;
        prompt.value = old.prompt || '';
        ratio.value = old.aspect_ratio;
        size.value = old.size_tier || '';
        seconds.value = old.seconds ?? null;
        // 只继承规格，不把旧视频作为参考图，也不隐式继承图片确认。
      }
    }
  }),
);
function confirmImage() {
  const key = actionKey(),
    version = image.value;
  return execute(
    async () => {
      confirmation.value = await api.request(
        `/api/v1/conversations/${props.cid}/reference-confirmations`,
        {
          method: 'POST',
          key,
          signal,
          body: { version_id: version.id, purpose: 'video_first_frame' },
        },
      );
    },
    { repeatable: true },
  );
}
const missing = computed(() => {
  const items = [];
  if (!props.cid) items.push('先打开一段对话');
  if (!available.value) items.push(reason.value || '等待能力信息');
  if (kind.value === 'text') {
    if (!title.value.trim()) items.push('作品标题');
    if (!prompt.value.trim()) items.push('文案内容');
    return items;
  }
  if (!ratios.value.includes(ratio.value)) items.push('画幅');
  if (kind.value !== 'local_motion' && !prompt.value.trim()) items.push('修改／创作要求');
  if (kind.value === 'image') {
    if (!cap.value?.size_tiers.includes(size.value)) items.push('尺寸档位');
    if (image.value && !cap.value?.reference_editing_enabled) items.push('参考图编辑尚未开放');
  } else if (kind.value === 'local_motion') {
    if (!image.value) items.push('选择图片版本');
    if (!(seconds.value >= 1 && seconds.value <= 30)) items.push('1–30 秒时长');
  } else {
    if (!(
      seconds.value >= cap.value?.min_seconds &&
      seconds.value <= cap.value?.max_seconds &&
      Number.isInteger(seconds.value)
    ))
      items.push(`${cap.value?.min_seconds ?? 4}–${cap.value?.max_seconds ?? 12} 秒整数时长`);
    if (!cap.value?.modes.includes(mode.value)) items.push('可用的视频路径');
    if (
      mode.value === 'keyframe' &&
      !(image.value && confirmation.value && confirmation.value.version_id === image.value.id)
    )
      items.push(image.value ? '确认此图片首帧' : '选择并确认图片首帧');
  }
  return items;
});
const valid = computed(() => missing.value.length === 0);
function submit() {
  if (!valid.value) return;
  const key = actionKey();
  let path = '/api/v1/tasks',
    body;
  if (kind.value === 'text') {
    body = { content: prompt.value, conversation_id: props.cid };
    if (props.version?.media_type === 'text/plain') {
      path = `/api/v1/artifacts/${props.version.artifact_id}/text-versions`;
      body.base_version_id = props.version.id;
    } else {
      path = '/api/v1/artifacts';
      body.kind = 'text';
      body.title = title.value;
    }
  } else {
    body = { kind: kind.value, conversation_id: props.cid, aspect_ratio: ratio.value };
    if (kind.value === 'image')
      Object.assign(body, {
        prompt: prompt.value,
        size_tier: size.value,
        reference_version_ids: image.value ? [image.value.id] : [],
      });
    if (kind.value === 'ai_video') {
      Object.assign(body, {
        prompt: prompt.value,
        mode: mode.value,
        size_tier: '720P',
        seconds: seconds.value,
      });
      if (mode.value === 'keyframe') body.reference_confirmation_id = confirmation.value.id;
    }
    if (kind.value === 'local_motion')
      Object.assign(body, {
        image_version_id: image.value.id,
        motion_type: motion.value,
        seconds: seconds.value,
      });
    if (
      (kind.value === 'image' && image.value) ||
      (kind.value === 'ai_video' && props.version?.media_type === 'video/mp4')
    ) {
      body.target_artifact_id = props.version.artifact_id;
      body.base_version_id = props.version.id;
    }
  }
  return execute(
    async () => {
      const result = await api.request(path, { method: 'POST', body, key, signal });
      emit('created', result);
    },
    { repeatable: true },
  );
}
</script>
<template>
  <form class="refine-form" :aria-busy="busy" @submit.prevent="submit">
    <div class="refine-scroll stack">
      <div class="version-summary">
        <img v-if="image" :src="contentPath(image)" alt="选中图片缩略图" />
        <div>
          <strong>{{ version ? `选中作品 · v${version.version_number}` : '新的创作' }}</strong
          ><small>另存新版，原版保留</small>
        </div>
      </div>
      <ErrorNotice :error="error" :retry="retry" :busy="busy" />
      <div v-if="retry" class="warning-box">
        <p>
          请求可能已受理。重试使用原编号，不会重复创建同一任务；放弃只关闭此窗口，不撤销已受理工作，请先到任务列表核对。
        </p>
        <button type="button" :disabled="busy" @click="abandon">
          放弃重试并关闭（不撤销工作）
        </button>
      </div>
      <fieldset :disabled="busy || !!retry">
        <label
          >创作方式<select v-model="kind">
            <option value="text">保存／精修文案（不调用模型）</option>
            <option value="image">图片／参考图精修</option>
            <option value="ai_video">AI 视频</option>
            <option value="local_motion">本地快速运镜（非 AI）</option>
          </select></label
        >
        <p v-if="!available" class="error-text">
          当前不可新增：{{ reason || '尚未获得能力信息' }}。不会切换收费模型。
        </p>
        <label v-if="kind === 'text'"
          >作品标题<input v-model="title" required maxlength="120"
        /></label>
        <label v-if="kind !== 'local_motion'"
          >{{ kind === 'text' ? '文案内容' : '修改／创作要求'
          }}<textarea
            v-model="prompt"
            rows="5"
            required
            :maxlength="kind === 'text' ? 50000 : 4000"
          ></textarea>
        </label>
        <template v-if="kind !== 'text'">
          <label
            >画幅<select v-model="ratio" required>
              <option value="" disabled>请选择</option>
              <option v-for="item in ratios" :key="item">{{ item }}</option>
            </select></label
          >
          <label v-if="kind === 'image'"
            >尺寸档位<select v-model="size" required>
              <option value="" disabled>请选择</option>
              <option v-for="item in cap?.size_tiers" :key="item">{{ item }}</option>
            </select></label
          >
          <p v-if="kind === 'image' && image && !cap?.reference_editing_enabled" class="error-text">
            参考图编辑尚未开放，不能绕过能力限制。
          </p>
          <template v-if="kind === 'ai_video'">
            <label
              >视频路径<select v-model="mode">
                <option v-for="item in cap?.modes" :key="item" :value="item">
                  {{
                    item === 'keyframe'
                      ? '已确认图片作为首帧'
                      : '明确直接文生视频（不保证严格保真）'
                  }}
                </option>
              </select></label
            >
            <p v-if="mode === 'keyframe' && !image">
              请先生成或上传视觉稿，再选定图片版本并确认。旧视频不能作为 Flash 参考输入。
            </p>
            <button v-if="mode === 'keyframe' && image" type="button" @click="confirmImage">
              {{ confirmation ? '已确认此版本作为视频首帧' : '确认：用这张图片版本做视频' }}
            </button>
          </template>
          <label v-if="kind !== 'image'"
            >请求时长（秒）<input
              v-model.number="seconds"
              type="number"
              required
              :min="kind === 'local_motion' ? 1 : cap?.min_seconds"
              :max="kind === 'local_motion' ? 30 : cap?.max_seconds"
              :step="kind === 'local_motion' ? 'any' : 1"
          /></label>
          <label v-if="kind === 'local_motion'"
            >运镜方式<select v-model="motion">
              <option value="dolly_in">缓慢推进</option>
              <option value="pan_left">向左平移</option>
              <option value="pan_right">向右平移</option>
              <option value="dynamic_float">轻微漂浮</option>
            </select></label
          >
          <p v-if="kind === 'local_motion'">
            本地运镜只移动静态图片，不是 AI 动态生成。{{ image ? '' : '请先选择一张图片版本。' }}
          </p>
        </template>
      </fieldset>
      <details class="version-details">
        <summary>版本与生成说明</summary>
        <p v-if="version">版本编号：{{ version.id }}</p>
        <p>
          图片新版不会替换旧视频的参考图。提交只表示持久受理，文件保存成功才会显示完成。冲突时重新选择当前版本，不自动换编号重做。
        </p>
      </details>
    </div>
    <footer class="refine-footer">
      <p id="refine-requirements" class="field-help" role="status">
        {{
          busy
            ? '正在处理，请稍候；保留窗口以核对受理结果…'
            : retry
              ? '请先核对原请求，使用上方重试入口。'
              : missing.length
                ? `还需：${missing.join('、')}`
                : '已填齐，可以提交。'
        }}
      </p>
      <button
        class="primary"
        :aria-busy="busy"
        :disabled="busy || !!retry || !valid"
        aria-describedby="refine-requirements"
      >
        {{ busy ? '正在处理…' : kind === 'text' ? '保存文案版本' : '提交一份任务' }}
      </button>
    </footer>
  </form>
</template>
