<script setup>
import { computed } from 'vue'
import {
  ChevronRight, CircleHelp, FolderOpen, ListTodo, Maximize2, Minus, Moon, Search, Settings, ShieldCheck, Square, Sun, X,
} from 'lucide-vue-next'
import { activeTab, assetMap, openTab, saveSettings, state, t, toast } from '../lib/store'
import { confirmDialog } from '../lib/ui'

const crumbs = computed(() => {
  const tab = activeTab.value
  if (!tab) return [{ label: '工作台' }]
  const list = [{ label: '工作台' }]
  if (tab.assetId && assetMap.value.get(tab.assetId)) list.push({ label: assetMap.value.get(tab.assetId).name })
  if (tab.kind === 'ai') list.push({ label: t('AI 助手', 'AI assistant') })
  else if (tab.title && tab.title !== list[list.length - 1]?.label) list.push({ label: tab.title })
  return list
})

const isDark = computed(() => (state.settings.appearance?.theme || 'dark') === 'dark')

async function toggleTheme() {
  const next = isDark.value ? 'light' : 'dark'
  try {
    await saveSettings({ appearance: { theme: next } })
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen()
  else document.documentElement.requestFullscreen?.().catch(() => {})
}

async function closeWindow() {
  const ok = await confirmDialog({
    title: t('退出 H1KHub', 'Exit H1KHub'),
    message: t('确定要关闭当前窗口吗？正在运行的终端会话将被终止。', 'Close this window? Running terminal sessions will be terminated.'),
    danger: true,
    okText: t('退出', 'Exit'),
  })
  if (!ok) return
  window.close()
  setTimeout(() => toast(t('浏览器模式无法直接关闭窗口，请直接关闭标签页。', 'The browser cannot close this tab automatically.'), 'info'), 200)
}

function openLocalFiles() {
  openTab({ kind: 'localfiles', title: t('本地文件', 'Local files'), icon: 'folder', key: 'localfiles' })
}

function showAbout() {
  state.aboutOpen = true
}
</script>

<template>
  <header class="titlebar">
    <div class="brand">
      <div class="brand-mark"><span class="mark-glyph">H1K</span></div>
      <span class="brand-name">H1KHub</span>
    </div>

    <div class="crumbs">
      <template v-for="(crumb, index) in crumbs" :key="index">
        <div v-if="index > 0" class="crumb-sep"><ChevronRight :size="13" /></div>
        <div class="crumb" :class="{ current: index === crumbs.length - 1 }">
          {{ crumb.label }}
        </div>
      </template>
    </div>

    <div class="titlebar-spacer" />

    <button class="icon-btn" :title="t('全局搜索  Ctrl+K', 'Global search  Ctrl+K')" @click="state.searchOpen = true">
      <Search :size="15" />
    </button>
    <button class="icon-btn" :title="t('本地文件', 'Local files')" @click="openLocalFiles">
      <FolderOpen :size="15" />
    </button>
    <button class="icon-btn" :title="t('后台任务', 'Background tasks')" @click="state.taskPanelOpen = true">
      <ListTodo :size="15" />
    </button>
    <button class="icon-btn" :title="isDark ? t('切换到亮色主题', 'Light theme') : t('切换到暗色主题', 'Dark theme')" @click="toggleTheme">
      <Sun v-if="isDark" :size="15" />
      <Moon v-else :size="15" />
    </button>
    <button class="icon-btn" :title="t('设置  Ctrl+,', 'Settings  Ctrl+,')" @click="state.settingsOpen = true">
      <Settings :size="15" />
    </button>
    <button class="icon-btn" :title="t('关于 H1KHub', 'About')" @click="showAbout">
      <CircleHelp :size="15" />
    </button>

    <div class="member-chip" :title="t('本版本为纯本地运行，不含云账号', 'Local-only build without cloud accounts')" @click="toast(t('H1KHub 为本地优先实现，不包含云账号与会员体系', 'H1KHub is local-first and has no cloud account or membership system'), 'info', 4600)">
      <ShieldCheck :size="13" />
      <span>{{ t('本地模式', 'Local mode') }}</span>
    </div>

    <div class="win-controls">
      <button class="win-btn" :title="t('最小化', 'Minimize')" @click="toast(t('浏览器模式不支持最小化窗口', 'Minimize is unavailable in browser mode'), 'info')">
        <Minus :size="14" />
      </button>
      <button class="win-btn" :title="t('最大化', 'Maximize')" @click="toggleFullscreen">
        <Maximize2 :size="13" />
      </button>
      <button class="win-btn danger" :title="t('关闭', 'Close')" @click="closeWindow">
        <X :size="15" />
      </button>
    </div>
  </header>
</template>
