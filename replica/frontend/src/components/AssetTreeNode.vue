<script setup>
import { computed } from 'vue'
import { ChevronDown, ChevronRight, Loader2, Star } from 'lucide-vue-next'
import AssetIcon from './AssetIcon.vue'
import { TYPE_INFO, t, typeInfo } from '../lib/store'

const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
  expanded: { type: Object, required: true },
  selectedId: { type: String, default: null },
  searchMode: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'open', 'toggle', 'menu', 'drop-asset'])

function onDragStart(event) {
  event.dataTransfer?.setData('text/h1khub-asset', props.node.id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}
function onDragOver(event) {
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  event.preventDefault()
}
function onDrop(event) {
  event.preventDefault()
  const id = event.dataTransfer?.getData('text/h1khub-asset') || ''
  if (!id || id === props.node.id) return
  const isDir = props.node.typeName === 'Dir'
  emit('drop-asset', {
    id,
    targetId: props.node.id,
    parentId: isDir ? props.node.id : (props.node.parentId || null),
    beforeId: isDir ? null : props.node.id,
  })
}

const info = computed(() => typeInfo(props.node.type))
const hasChildren = computed(() => props.node.children?.length > 0 || props.node.typeName === 'Dir')
const isOpen = computed(() => !!props.expanded[props.node.id])
const isSelected = computed(() => props.selectedId === props.node.id)
const isConnected = computed(() => !!props.node._connected)
</script>

<template>
  <div>
    <div
      class="tree-row"
      :class="{ selected: isSelected }"
      :style="{ paddingLeft: `${8 + (searchMode ? 0 : depth * 14)}px` }"
      :title="node.alias ? `${node.alias} (${node.name})` : node.name"
      draggable="true"
      @dragstart="onDragStart"
      @dragover="onDragOver"
      @drop="onDrop"
      @click="emit('select', node)"
      @dblclick="emit('open', node)"
      @contextmenu="emit('menu', $event, node)"
    >
      <div class="tree-caret" @click.stop="emit('toggle', node)">
        <ChevronDown v-if="hasChildren && isOpen" :size="13" />
        <ChevronRight v-else-if="hasChildren" :size="13" />
      </div>
      <div class="tree-icon">
        <AssetIcon :type-name="node.typeName" :color="node.color || info.color" :size="15" />
      </div>
      <div class="tree-name">
        {{ node.alias || node.name }}
        <span v-if="searchMode" class="tree-sub">{{ node.typeName }}</span>
      </div>
      <span v-if="node.favorite" class="tree-dot" style="background: var(--orange)" />
      <Loader2 v-if="node._loading" :size="12" class="tool-spin" style="color: var(--text-3)" />
      <span v-if="isConnected" class="dot" />
      <div v-if="!searchMode" class="tree-actions">
        <button class="icon-btn sm" :title="t('新建连接', 'New connection')" @click.stop="emit('menu', $event, node)">⋯</button>
      </div>
    </div>
    <template v-if="isOpen">
      <AssetTreeNode
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :depth="depth + 1"
        :expanded="expanded"
        :selected-id="selectedId"
        @select="(n) => emit('select', n)"
        @open="(n) => emit('open', n)"
        @toggle="(n) => emit('toggle', n)"
        @menu="(e, n) => emit('menu', e, n)"
        @drop-asset="(payload) => emit('drop-asset', payload)"
      />
    </template>
  </div>
</template>
