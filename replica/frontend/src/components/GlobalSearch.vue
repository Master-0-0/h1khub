<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import {
  Container, CornerDownLeft, Database, Plus, Search, Server, Settings, Sparkles, SquareTerminal, X,
} from 'lucide-vue-next'
import AssetIcon from './AssetIcon.vue'
import {
  activateTab, newLocalTerminal, openAi, openAsset, openDockerDefault, openTab, state, t, typeInfo,
} from '../lib/store'

const query = ref('')
const inputEl = ref(null)
const activeIndex = ref(0)

const commands = computed(() => [
  { label: t('新建 SSH 连接', 'New SSH connection'), sub: t('命令', 'Command'), icon: Server, run: () => (state.searchOpen = false, state.assetDialog = { open: true, typeName: 'SSH', parentId: null, asset: null }) },
  { label: t('新建数据库连接', 'New database connection'), sub: t('命令', 'Command'), icon: Database, run: () => (state.searchOpen = false, state.assetDialog = { open: true, typeName: 'DB', parentId: null, asset: null }) },
  { label: t('打开本地终端', 'Open local terminal'), sub: t('命令', 'Command'), icon: SquareTerminal, run: () => (state.searchOpen = false, newLocalTerminal()) },
  { label: t('打开 Docker', 'Open Docker'), sub: t('命令', 'Command'), icon: Container, run: () => (state.searchOpen = false, openDockerDefault()) },
  { label: t('打开 AI 助手', 'Open AI assistant'), sub: t('命令', 'Command'), icon: Sparkles, run: () => (state.searchOpen = false, openAi()) },
  { label: t('打开设置', 'Open settings'), sub: t('命令', 'Command'), icon: Settings, run: () => (state.searchOpen = false, state.settingsOpen = true) },
])

const results = computed(() => {
  const q = query.value.trim().toLowerCase()
  const list = []
  const assetItems = state.assets
    .filter((a) => {
      if (!q) return true
      const hay = `${a.name} ${a.typeName} ${a.config?.host || ''} ${a.config?.database || ''} ${a.config?.username || ''}`.toLowerCase()
      return hay.includes(q)
    })
    .slice(0, 40)
    .map((a) => ({
      label: a.name,
      sub: `${typeInfo(a.type).label}${a.config?.host ? ' · ' + a.config.host + ':' + (a.config.port || '') : ''}`,
      icon: null,
      typeName: a.typeName,
      color: typeInfo(a.type).color,
      isAsset: true,
      run: () => {
        state.searchOpen = false
        state.selectedAssetId = a.id
        openAsset(a)
      },
    }))
  if (assetItems.length) list.push({ group: t('资产', 'Assets'), items: assetItems })
  const cmdItems = commands.value.filter((c) => !q || c.label.toLowerCase().includes(q) || c.sub.toLowerCase().includes(q))
  if (cmdItems.length) list.push({ group: t('命令', 'Commands'), items: cmdItems })
  const tabItems = state.tabs
    .filter((tb) => !q || tb.title.toLowerCase().includes(q))
    .map((tb) => ({
      label: tb.title,
      sub: t('已打开的标签', 'Open tab'),
      icon: SquareTerminal,
      run: () => {
        state.searchOpen = false
        activateTab(tb.id)
      },
    }))
  if (tabItems.length) list.push({ group: t('标签页', 'Tabs'), items: tabItems })
  return list
})

const flat = computed(() => results.value.flatMap((group) => group.items))

watch(query, () => (activeIndex.value = 0))

watch(
  () => state.searchOpen,
  async (open) => {
    if (open) {
      query.value = ''
      activeIndex.value = 0
      await nextTick()
      inputEl.value?.focus()
    }
  },
)

function onKey(event) {
  if (event.key === 'Escape') {
    state.searchOpen = false
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    activeIndex.value = Math.min(activeIndex.value + 1, flat.value.length - 1)
    scrollActive()
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    activeIndex.value = Math.max(activeIndex.value - 1, 0)
    scrollActive()
  } else if (event.key === 'Enter') {
    const item = flat.value[activeIndex.value]
    if (item) item.run()
  }
}

function scrollActive() {
  nextTick(() => {
    document.querySelector('.gs-item.active')?.scrollIntoView({ block: 'nearest' })
  })
}

let counter = -1
function indexOf(item) {
  return flat.value.indexOf(item)
}
</script>

<template>
  <div v-if="state.searchOpen" class="modal-mask" style="align-items: flex-start; padding-top: 10vh" @mousedown.self="state.searchOpen = false">
    <div class="modal" style="width: 620px" @keydown="onKey">
      <div style="display: flex; align-items: center; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--border)">
        <Search :size="16" style="color: var(--text-3)" />
        <input
          ref="inputEl"
          v-model="query"
          class="input"
          style="border: 0; background: transparent; height: 28px; font-size: 14px; padding: 0"
          :placeholder="t('搜索资产、标签或命令…', 'Search assets, tabs or commands…')"
        />
        <span class="kbd">Esc</span>
      </div>
      <div style="max-height: 55vh; overflow: auto; padding: 6px">
        <template v-for="group in results" :key="group.group">
          <div class="ctx-label" style="padding: 6px 10px">{{ group.group }}</div>
          <div
            v-for="item in group.items"
            :key="item.label + indexOf(item)"
            class="gs-item"
            :class="{ active: indexOf(item) === activeIndex }"
            style="display: flex; align-items: center; gap: 9px; padding: 8px 10px; border-radius: 6px; cursor: default"
            :style="indexOf(item) === activeIndex ? 'background: var(--accent-weak)' : ''"
            @mouseenter="activeIndex = indexOf(item)"
            @click="item.run()"
          >
            <AssetIcon v-if="item.isAsset" :type-name="item.typeName" :size="15" :color="item.color" />
            <component v-else :is="item.icon" :size="15" style="color: var(--text-2)" />
            <div style="flex: 1; min-width: 0">
              <div class="ellipsis">{{ item.label }}</div>
            </div>
            <span class="tiny">{{ item.sub }}</span>
          </div>
        </template>
        <div v-if="!flat.length" class="center-hint" style="padding: 30px">{{ t('没有结果', 'No results') }}</div>
      </div>
      <div style="display: flex; gap: 12px; padding: 8px 14px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 11px">
        <span><span class="kbd">↑</span> <span class="kbd">↓</span> {{ t('选择', 'Select') }}</span>
        <span><span class="kbd">Enter</span> {{ t('打开', 'Open') }}</span>
        <span><CornerDownLeft :size="11" /></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gs-item.active { background: var(--accent-weak); }
</style>
