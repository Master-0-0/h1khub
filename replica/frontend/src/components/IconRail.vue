<script setup>
import { computed } from 'vue'
import {
  Clock3, CircleHelp, Container, Database, DatabaseZap, FolderClosed, Network, Settings, Sparkles, SquareTerminal,
} from 'lucide-vue-next'
import {
  activeTab, newLocalTerminal, openAi, openAssetMap, openDbDefault, openDockerDefault, openHistory,
  openRedisDefault, state, t,
} from '../lib/store'

const props = defineProps({})

const railActive = computed(() => {
  if (state.panelVisible && state.sideMode === 'history') return 'history'
  if (state.panelVisible && state.sideMode === 'assets') return 'assets'
  const kind = activeTab.value?.kind
  if (kind === 'ai') return 'ai'
  if (kind === 'map') return 'map'
  if (kind === 'docker') return 'docker'
  if (kind === 'db') return 'db'
  if (kind === 'redis') return 'redis'
  if (kind === 'terminal') return 'terminal'
  return ''
})

function toggleAssets() {
  if (state.panelVisible && state.sideMode === 'assets') {
    state.panelVisible = false
  } else {
    state.panelVisible = true
    state.sideMode = 'assets'
  }
}

function showHistory() {
  if (state.panelVisible && state.sideMode === 'history') state.panelVisible = false
  else openHistory()
}
</script>

<template>
  <nav class="rail">
    <button class="rail-btn" :class="{ active: railActive === 'ai' }" :title="t('AI 助手', 'AI assistant')" @click="openAi">
      <Sparkles :size="18" />
    </button>
    <button class="rail-btn" :class="{ active: railActive === 'assets' }" :title="t('资产列表', 'Assets')" @click="toggleAssets">
      <FolderClosed :size="18" />
    </button>
    <button class="rail-btn" :class="{ active: railActive === 'map' }" :title="t('资产地图', 'Asset map')" @click="openAssetMap">
      <Network :size="18" />
    </button>
    <button class="rail-btn" :class="{ active: railActive === 'history' }" :title="t('最近历史', 'Recent history')" @click="showHistory">
      <Clock3 :size="18" />
    </button>
    <button class="rail-btn" :class="{ active: railActive === 'docker' }" :title="t('Docker', 'Docker')" @click="openDockerDefault">
      <Container :size="18" />
    </button>
    <button class="rail-btn" :class="{ active: railActive === 'db' }" :title="t('数据库', 'Databases')" @click="openDbDefault">
      <Database :size="18" />
    </button>
    <button class="rail-btn" :class="{ active: railActive === 'redis' }" :title="t('Redis', 'Redis')" @click="openRedisDefault">
      <DatabaseZap :size="18" />
    </button>
    <button class="rail-btn" :class="{ active: railActive === 'terminal' }" :title="t('本地终端', 'Local terminal')" @click="newLocalTerminal">
      <SquareTerminal :size="18" />
    </button>

    <div class="rail-spacer" />
    <div class="rail-divider" />
    <button class="rail-btn" :title="t('设置', 'Settings')" @click="state.settingsOpen = true">
      <Settings :size="18" />
    </button>
    <button class="rail-btn" :title="t('帮助与关于', 'Help & about')" @click="state.aboutOpen = true">
      <CircleHelp :size="18" />
    </button>
  </nav>
</template>
