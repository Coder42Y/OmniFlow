<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';

// 移植自已批准快照：固定左端短横线、4.5 格余弦波峰、420/31 弹簧、22px 增幅。
const props = defineProps({
  turns: { type: Array, default: () => [] },
  selected: { type: Number, default: 0 },
  available: { type: Number, default: 400 },
});
const emit = defineEmits(['jump', 'wheel']);
const rail = ref(),
  hoverIndex = ref(-1),
  hoverY = ref(null),
  lines = ref([]);
const pad = 12;
const spacing = computed(() =>
  Math.min(10, (Math.max(80, props.available - 40) - 24) / Math.max(1, props.turns.length - 1)),
);
const height = computed(() => pad * 2 + spacing.value * Math.max(0, props.turns.length - 1));
const preview = computed(() => props.turns[hoverIndex.value]);
const clamp = (n, a, b) => Math.min(b, Math.max(a, n));
let widths = [],
  velocities = [],
  frame = 0,
  lastTime = 0,
  dragging = false,
  disposed = false,
  reduced;
function animate() {
  if (!disposed && !frame) frame = requestAnimationFrame(tick);
}
function tick(time) {
  const dt = Math.min((time - lastTime) / 1000 || 1 / 60, 0.025);
  lastTime = time;
  let moving = false;
  for (let i = 0; i < props.turns.length; i++) {
    const distance =
      hoverY.value === null
        ? Infinity
        : Math.abs(pad + i * spacing.value - hoverY.value) / spacing.value;
    const influence = distance < 4.5 ? (1 + Math.cos((Math.PI * distance) / 4.5)) / 2 : 0;
    const target = 6 + 22 * influence;
    if (reduced?.matches) {
      widths[i] = target;
      velocities[i] = 0;
    } else {
      velocities[i] += (420 * (target - widths[i]) - 31 * velocities[i]) * dt;
      widths[i] += velocities[i] * dt;
    }
    if (Math.abs(target - widths[i]) > 0.025 || Math.abs(velocities[i]) > 0.025) moving = true;
    else {
      widths[i] = target;
      velocities[i] = 0;
    }
    if (lines.value[i]) {
      lines.value[i].style.width = widths[i].toFixed(2) + 'px';
      lines.value[i].style.backgroundColor =
        hoverY.value === null
          ? i === props.selected
            ? '#727b86'
            : '#c9cdd1'
          : `rgb(${Math.round(201 - 108 * influence)},${Math.round(205 - 103 * influence)},${Math.round(209 - 98 * influence)})`;
    }
  }
  frame = moving ? requestAnimationFrame(tick) : 0;
}
function hover(y) {
  hoverY.value = clamp(y, pad, pad + (props.turns.length - 1) * spacing.value);
  hoverIndex.value = clamp(
    Math.round((hoverY.value - pad) / spacing.value),
    0,
    props.turns.length - 1,
  );
  animate();
  return hoverIndex.value;
}
function clear() {
  hoverY.value = null;
  hoverIndex.value = -1;
  animate();
}
function pointer(event) {
  const index = hover(event.clientY - rail.value.getBoundingClientRect().top);
  if (dragging) emit('jump', index);
}
function down(event) {
  if (event.button !== 0) return;
  dragging = true;
  rail.value.focus({ preventScroll: true });
  rail.value.setPointerCapture(event.pointerId);
  pointer(event);
  event.preventDefault();
}
function up(event) {
  dragging = false;
  if (rail.value.hasPointerCapture(event.pointerId))
    rail.value.releasePointerCapture(event.pointerId);
  if (
    event.pointerType === 'touch' ||
    event.clientX < rail.value.getBoundingClientRect().left ||
    event.clientX > rail.value.getBoundingClientRect().right ||
    event.clientY < rail.value.getBoundingClientRect().top ||
    event.clientY > rail.value.getBoundingClientRect().bottom
  )
    clear();
}
function keyboard(event) {
  let next = props.selected;
  const offsets = { ArrowDown: 1, ArrowUp: -1, PageDown: 3, PageUp: -3 };
  if (event.key in offsets) next += offsets[event.key];
  else if (event.key === 'Home') next = 0;
  else if (event.key === 'End') next = props.turns.length - 1;
  else if (event.key === 'Escape') {
    rail.value.blur();
    clear();
    return;
  } else return;
  event.preventDefault();
  next = clamp(next, 0, props.turns.length - 1);
  emit('jump', next);
  hover(pad + next * spacing.value);
}
watch(
  () => [props.turns.length, spacing.value],
  async () => {
    widths = props.turns.map(() => 6);
    velocities = props.turns.map(() => 0);
    await nextTick();
    animate();
  },
  { immediate: true },
);
watch(() => props.selected, animate);
onMounted(() => {
  reduced = matchMedia('(prefers-reduced-motion: reduce)');
  reduced.addEventListener('change', animate);
});
onBeforeUnmount(() => {
  disposed = true;
  cancelAnimationFrame(frame);
  reduced?.removeEventListener('change', animate);
});
</script>

<template>
  <div v-if="turns.length" class="rail-wrap">
    <div
      ref="rail"
      class="rail"
      :style="{ height: height + 'px' }"
      tabindex="0"
      role="slider"
      aria-label="对话历史横线定位条"
      aria-orientation="vertical"
      aria-valuemin="1"
      :aria-valuemax="turns.length"
      :aria-valuenow="selected + 1"
      :aria-valuetext="`第 ${selected + 1} 轮：${turns[selected]?.question || ''}`"
      aria-controls="transcript"
      @pointerenter="pointer"
      @pointermove="pointer"
      @pointerleave="!dragging && clear()"
      @pointerdown="down"
      @pointerup="up"
      @pointercancel="
        dragging = false;
        clear();
      "
      @lostpointercapture="dragging = false"
      @focus="hover(pad + selected * spacing)"
      @blur="!dragging && clear()"
      @keydown="keyboard"
      @wheel.prevent="emit('wheel', $event.deltaY)"
    >
      <span
        v-for="(turn, i) in turns"
        :key="turn.id"
        :ref="(el) => (lines[i] = el)"
        class="rib"
        :class="{ current: i === selected }"
        :style="{ top: pad + i * spacing + 'px' }"
        aria-hidden="true"
      ></span>
    </div>
    <div
      class="rail-tip"
      :class="{ visible: preview }"
      :style="{ top: clamp(hoverY || 0, 35, Math.max(35, height - 35)) + 'px' }"
      aria-hidden="true"
    >
      <div class="tip-heading">对话 {{ hoverIndex + 1 }} / {{ turns.length }}</div>
      <div class="tip-content">{{ preview?.question }}</div>
      <div class="tip-answer">{{ preview?.answer }}</div>
    </div>
  </div>
</template>
