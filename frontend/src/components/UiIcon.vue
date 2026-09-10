<script setup>
defineProps({ name: String });
const paths = {
  menu: 'M4 6h16M4 12h16M4 18h16',
  history: 'M12 6v6l4 2',
  plus: 'M12 4v16M4 12h16',
  image: 'm4 18 5-6 4 4 3-4 5 6',
  edit: 'm15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14Z',
  account: 'M4 21v-2a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v2',
  settings: 'M4 7h16M4 17h16M8 4v6M16 14v6',
  close: 'm6 6 12 12M18 6 6 18',
  send: 'M12 20V4m-6 6 6-6 6 6',
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4',
};
</script>
<template>
  <svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <path v-if="paths[name]" :d="paths[name]" />
    <circle v-if="name === 'history'" cx="12" cy="12" r="9" />
    <template v-if="name === 'image'">
      <rect x="3" y="3" width="18" height="18" rx="3" />
      <circle cx="8" cy="8" r="1.5" />
    </template>
    <circle v-if="name === 'account'" cx="12" cy="6" r="4" />
    <template v-if="name === 'more'">
      <circle v-for="x in [5, 12, 19]" :key="x" :cx="x" cy="12" r="1" />
    </template>
  </svg>
</template>
