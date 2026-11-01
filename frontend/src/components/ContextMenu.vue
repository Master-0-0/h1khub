<script setup>
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { ctxState, hideContextMenu } from '../lib/ui'

const menuEl = ref(null)
const pos = ref({ x: 0, y: 0 })

function clamp() {
  const el = menuEl.value
  if (!el) return
  const { innerWidth, innerHeight } = window
  const rect = el.getBoundingClientRect()
  let x = ctxState.x
  let y = ctxState.y
  if (x + rect.width > innerWidth - 6) x = Math.max(6, innerWidth - rect.width - 6)
  if (y + rect.height > innerHeight - 6) y = Math.max(6, innerHeight - rect.height - 6)
  pos.value = { x, y }
}

watch(
  () => [ctxState.open, ctxState.x, ctxState.y, ctxState.items.length],
  async () => {
    if (!ctxState.open) return
    pos.value = { x: ctxState.x, y: ctxState.y }
    await new Promise((r) => requestAnimationFrame(r))
    clamp()
  },
  { immediate: true },
)

function onGlobalDown(event) {
  if (!ctxState.open) return
  if (menuEl.value && menuEl.value.contains(event.target)) return
  hideContextMenu()
}

function onKey(event) {
  if (ctxState.open && event.key === 'Escape') hideContextMenu()
}

function run(item) {
  if (item.disabled || item.sep) return
  hideContextMenu()
  item.action?.()
}

onMounted(() => {
  document.addEventListener('mousedown', onGlobalDown, true)
  document.addEventListener('keydown', onKey)
  window.addEventListener('blur', hideContextMenu)
  window.addEventListener('resize', hideContextMenu)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onGlobalDown, true)
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('blur', hideContextMenu)
  window.removeEventListener('resize', hideContextMenu)
})
</script>

<template>
  <div
    v-if="ctxState.open"
    ref="menuEl"
    class="ctx-menu"
    :style="{ left: pos.x + 'px', top: pos.y + 'px' }"
    @contextmenu.prevent
  >
    <template v-for="(item, index) in ctxState.items" :key="index">
      <div v-if="item.sep" class="ctx-sep" />
      <div
        v-else-if="!item.hidden"
        class="ctx-item"
        :class="{ danger: item.danger, disabled: item.disabled }"
        @click="run(item)"
      >
        <span class="ctx-icon">
          <component v-if="item.icon" :is="item.icon" :size="14" />
        </span>
        <span style="flex: 1">{{ item.label }}</span>
        <span v-if="item.shortcut" class="ctx-shortcut">{{ item.shortcut }}</span>
      </div>
    </template>
  </div>
</template>
