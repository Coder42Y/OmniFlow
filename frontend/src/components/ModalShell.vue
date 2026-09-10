<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue';
import UiIcon from './UiIcon.vue';
const props = defineProps({
  title: String,
  fixedBody: Boolean,
  returnFocus: Object,
  closeBlocked: Boolean,
});
const emit = defineEmits(['close']);
const dialog = ref();
function close() {
  if (!props.closeBlocked) emit('close');
}
let previous;
onMounted(() => {
  previous = props.returnFocus || document.activeElement;
  dialog.value.showModal();
});
function trapFocus(event) {
  if (event.key !== 'Tab') return;
  const items = [
    ...dialog.value.querySelectorAll(
      'button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary,a[href],[tabindex="0"]',
    ),
  ].filter((el) => !el.matches(':disabled') && el.getClientRects().length);
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
onBeforeUnmount(() => {
  const shouldRestore =
    dialog.value?.contains(document.activeElement) || document.activeElement === document.body;
  dialog.value?.close();
  if (shouldRestore && previous?.isConnected) previous.focus({ preventScroll: true });
});
</script>
<template>
  <dialog
    ref="dialog"
    class="modal"
    :class="{ 'fixed-body': fixedBody }"
    aria-labelledby="modal-title"
    @cancel.prevent="close"
    @keydown="trapFocus"
  >
    <header class="modal-header">
      <h2 id="modal-title">{{ title }}</h2>
      <button
        class="icon-button"
        type="button"
        aria-label="关闭窗口"
        :disabled="closeBlocked"
        @click="close"
      >
        <UiIcon name="close" />
      </button>
    </header>
    <div class="modal-body"><slot /></div>
  </dialog>
</template>
