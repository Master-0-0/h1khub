<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Loader2, RefreshCw, X } from 'lucide-vue-next'
import AboutDialog from './components/AboutDialog.vue'
import AssetDialog from './components/AssetDialog.vue'
import AssetPanel from './components/AssetPanel.vue'
import BatchExecDialog from './components/BatchExecDialog.vue'
import ChromeProxyDialog from './components/ChromeProxyDialog.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import ConnectionPicker from './components/ConnectionPicker.vue'
import ContextMenu from './components/ContextMenu.vue'
import GlobalSearch from './components/GlobalSearch.vue'
import IconRail from './components/IconRail.vue'
import PromptDialog from './components/PromptDialog.vue'
import LockScreen from './components/LockScreen.vue'
import SettingsDialog from './components/SettingsDialog.vue'
import TaskPanel from './components/TaskPanel.vue'
import TabBar from './components/TabBar.vue'
import TitleBar from './components/TitleBar.vue'
import ToastHost from './components/ToastHost.vue'
import WelcomeView from './components/WelcomeView.vue'
import AiChat from './views/AiChat.vue'
import AssetMapView from './views/AssetMapView.vue'
import DbView from './views/DbView.vue'
import DockerView from './views/DockerView.vue'
import FilesView from './views/FilesView.vue'
import TunnelView from './views/TunnelView.vue'
import RedisView from './views/RedisView.vue'
import TerminalView from './views/TerminalView.vue'
import VncView from './views/VncView.vue'
import LocalFilesView from './views/LocalFilesView.vue'
import {
  activateTab, bootstrap, closePane, closeTab, focusPane, focusPaneById, loadHistory, newLocalTerminal,
  paneActiveTab, refreshLockStatus, resizePanes, scheduleAutoUpdateCheck, sendLockActivity, splitPane, state, t, toast,
} from './lib/store'

const booting = ref(true)
const views = {
  terminal: TerminalView,
  vnc: VncView,
  localfiles: LocalFilesView,
  db: DbView,
  redis: RedisView,
  docker: DockerView,
  files: FilesView,
  tunnel: TunnelView,
  ai: AiChat,
  map: AssetMapView,
}
const viewComponent = computed(() => (state.activeTabId ? views[state.tabs.find((x) => x.id === state.activeTabId)?.kind] : null))
const activeTab = computed(() => state.tabs.find((x) => x.id === state.activeTabId) || null)

function viewForPane(pane) {
  const tab = paneActiveTab(pane.id)
  return tab ? views[tab.kind] || null : null
}

function tabForPane(pane) {
  return paneActiveTab(pane.id)
}

function paneStyle(pane) {
  const n = state.panes.length || 1
  return { flex: `${pane.size || 1 / n} 1 0` }
}

function doSplit(direction) {
  if (!state.activeTabId) {
    toast(t('请先打开一个标签再分屏', 'Open a tab before splitting'), 'info')
    return
  }
  const res = splitPane(direction)
  if (!res.ok) toast(t('最多支持 4 个分屏', 'At most 4 panes'), 'info')
}

function closePaneById(paneId) {
  closePane(paneId)
}

function startPaneResize(index, event) {
  const row = state.splitDir !== 'horizontal'
  const rect = event.currentTarget.parentElement.getBoundingClientRect()
  const total = row ? rect.width : rect.height
  if (!total) return
  const start = row ? event.clientX : event.clientY
  const base = { a: state.panes[index].size, b: state.panes[index + 1].size }
  const move = (e) => {
    const delta = ((row ? e.clientX : e.clientY) - start) / total
    state.panes[index].size = base.a
    state.panes[index + 1].size = base.b
    resizePanes(index, delta)
  }
  const up = () => {
    document.removeEventListener('mousemove', move)
    document.removeEventListener('mouseup', up)
    document.body.style.cursor = ''
  }
  document.body.style.cursor = row ? 'col-resize' : 'row-resize'
  document.addEventListener('mousemove', move)
  document.addEventListener('mouseup', up)
}

async function start() {
  booting.value = true
  try {
    await bootstrap()
    // #91: when update.autoCheck is on, run exactly one delayed read-only
    // check after bootstrap.  Never poll in the background.
    scheduleAutoUpdateCheck()
  } catch (err) {
    state.bootError = String(err.message || err)
  } finally {
    booting.value = false
    document.getElementById('boot')?.remove()
  }
}

function matchHotkey(event, spec) {
  if (!spec) return false
  const parts = String(spec).split('+').map((x) => x.trim().toLowerCase()).filter(Boolean)
  if (!parts.length) return false
  const key = parts.pop()
  const needCtrl = parts.includes('ctrl') || parts.includes('cmd') || parts.includes('meta')
  const needShift = parts.includes('shift')
  const needAlt = parts.includes('alt')
  const ctrl = event.ctrlKey || event.metaKey
  if (needCtrl !== ctrl || needShift !== event.shiftKey || needAlt !== event.altKey) return false
  const pressed = event.key.toLowerCase()
  return pressed === key
}

function onKeydown(event) {
  if (state.lock.locked) return
  const tag = (event.target?.tagName || '').toLowerCase()
  const inInput = ['input', 'textarea', 'select'].includes(tag) || event.target?.isContentEditable
  const hk = state.settings.general?.hotkeys || {}
  const actions = [
    ['search', () => { state.searchOpen = true }],
    ['settings', () => { state.settingsOpen = true }],
    ['localTerminal', () => newLocalTerminal()],
    ['closeTab', () => { if (state.activeTabId && !inInput) closeTab(state.activeTabId) }],
    ['history', () => { state.panelVisible = true; state.sideMode = 'history'; loadHistory() }],
    ['togglePanel', () => { state.panelVisible = !state.panelVisible }],
    ['splitVertical', () => doSplit('vertical')],
    ['splitHorizontal', () => doSplit('horizontal')],
    ['moveLeft', () => { if (!inInput) focusPane('left') }],
    ['moveRight', () => { if (!inInput) focusPane('right') }],
    ['moveUp', () => { if (!inInput) focusPane('up') }],
    ['moveDown', () => { if (!inInput) focusPane('down') }],
  ]
  for (const [name, run] of actions) {
    if (matchHotkey(event, hk[name])) {
      event.preventDefault()
      run()
      return
    }
  }
  if (event.altKey && /^[1-9]$/.test(event.key)) {
    const tab = state.tabs[Number(event.key) - 1]
    if (tab) {
      event.preventDefault()
      activateTab(tab.id)
    }
  }
}

function startPanelResize(event) {
  const startX = event.clientX
  const startW = state.panelWidth || 292
  const move = (e) => {
    state.panelWidth = Math.min(Math.max(startW + (e.clientX - startX), 220), 520)
  }
  const up = () => {
    document.removeEventListener('mousemove', move)
    document.removeEventListener('mouseup', up)
  }
  document.addEventListener('mousemove', move)
  document.addEventListener('mouseup', up)
}

let lockTimer = null
let lastActivityAt = 0

function markActivity() {
  if (state.lock.locked || !state.lock.enabled) return
  const now = Date.now()
  if (now - lastActivityAt < 15000) return
  lastActivityAt = now
  sendLockActivity(true)
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') refreshLockStatus()
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('keydown', markActivity, true)
  window.addEventListener('mousedown', markActivity, true)
  window.addEventListener('scroll', markActivity, true)
  document.addEventListener('visibilitychange', onVisibilityChange)
  // Server-side idle expiry is polled; status checks never count as activity,
  // so background polling cannot keep an idle session unlocked.  Polling while
  // locked also lets a tab pick up an unlock performed in another tab.
  lockTimer = setInterval(() => {
    refreshLockStatus()
  }, 5000)
  start()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('keydown', markActivity, true)
  window.removeEventListener('mousedown', markActivity, true)
  window.removeEventListener('scroll', markActivity, true)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  if (lockTimer) clearInterval(lockTimer)
})
</script>

<template>
  <div class="app" :class="{ 'app-locked': state.lock.locked }">
    <template v-if="!booting && !state.bootError">
      <TitleBar />
      <div class="app-body" :inert="state.lock.locked || undefined">
        <IconRail />
        <template v-if="state.panelVisible">
          <AssetPanel :style="{ width: (state.panelWidth || 292) + 'px' }" />
          <div class="split-handle" style="width: 4px" @mousedown.prevent="startPanelResize" />
        </template>
        <main class="workspace" :class="{ panes: state.panes.length > 1, [state.splitDir]: state.panes.length > 1 }">
          <template v-for="(pane, index) in state.panes" :key="pane.id">
            <div
              v-if="index > 0"
              class="split-handle pane-handle"
              :class="[state.splitDir, { dragging: false }]"
              :title="t('拖动调整分屏比例', 'Drag to resize panes')"
              @mousedown.prevent="startPaneResize(index - 1, $event)"
            />
            <div
              class="pane"
              :class="{ focused: pane.id === state.activePaneId, single: state.panes.length === 1 }"
              :data-pane-id="pane.id"
              :style="paneStyle(pane)"
              @mousedown="focusPaneById(pane.id)"
            >
              <TabBar :pane="pane" />
              <div class="pane-body">
                <KeepAlive :max="16">
                  <component
                    :is="viewForPane(pane)"
                    v-if="tabForPane(pane) && viewForPane(pane)"
                    :key="tabForPane(pane).id"
                    :tab="tabForPane(pane)"
                  />
                  <WelcomeView v-else :key="'welcome-' + pane.id" />
                </KeepAlive>
              </div>
              <button
                v-if="state.panes.length > 1"
                class="pane-close"
                :title="t('关闭分屏', 'Close pane')"
                @click.stop="closePaneById(pane.id)"
              >
                <X :size="12" />
              </button>
            </div>
          </template>
        </main>
      </div>
    </template>

    <div v-else-if="booting" class="empty-state" style="height: 100vh">
      <div class="home-logo" style="width: 54px; height: 54px; border-radius: 16px">
        <Loader2 :size="26" class="tool-spin" />
      </div>
      <div class="big">{{ t('正在加载本地工作区…', 'Loading local workspace…') }}</div>
      <div class="tiny">{{ t('读取资产、设置与锁屏状态', 'Loading assets, settings and lock state') }}</div>
    </div>

    <div v-else class="empty-state" style="height: 100vh">
      <div class="home-logo" style="width: 54px; height: 54px; border-radius: 16px">
        <RefreshCw :size="26" />
      </div>
      <div class="big">{{ t('无法连接本地 H1KHub 服务', 'Cannot reach the local H1KHub service') }}</div>
      <div class="alert error" style="max-width: 560px; text-align: left">{{ state.bootError }}</div>
      <button class="btn primary" @click="start">
        <Loader2 v-if="booting" :size="14" class="tool-spin" />
        {{ t('重试', 'Retry') }}
      </button>
      <div class="tiny">{{ t('请确认后端进程仍在运行：python3 backend/app.py', 'Make sure the backend is running: python3 backend/app.py') }}</div>
    </div>

    <AssetDialog />
    <SettingsDialog />
    <TaskPanel />
    <ChromeProxyDialog />
    <ConnectionPicker />
    <BatchExecDialog />
    <GlobalSearch />
    <AboutDialog />
    <ContextMenu />
    <ConfirmDialog />
    <PromptDialog />
    <LockScreen v-if="!booting && state.lock.locked" />
    <ToastHost />
  </div>
</template>
