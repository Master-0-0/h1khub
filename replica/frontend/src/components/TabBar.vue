<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { ChevronDown, CopyX, Plus, X } from 'lucide-vue-next'
import AssetIcon from './AssetIcon.vue'
import {
  activateTab, canSplit, closePane, closeTab, newLocalTerminal, paneTabs, splitPane, state, t, toast,
} from '../lib/store'
import { showContextMenu } from '../lib/ui'

const props = defineProps({ pane: { type: Object, default: null } })

const scroller = ref(null)
const kindIcon = {
  terminal: 'square-terminal',
  db: 'database',
  redis: 'database-zap',
  docker: 'container',
  ai: 'sparkles',
  map: 'network',
  settings: 'settings',
}

const myTabs = computed(() => (props.pane ? paneTabs(props.pane.id) : state.tabs))
const activeId = computed(() => (props.pane ? props.pane.activeTabId : state.activeTabId))

// 分屏后标签栏变窄，保证当前活动标签始终可见
watch(activeId, async () => {
  await nextTick()
  scroller.value?.querySelector('.tab.active')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
})
const splitLimit = computed(() => state.panes.length >= 4)

function split(direction) {
  const res = splitPane(direction, { tab: props.pane ? (myTabs.value.find((x) => x.id === props.pane.activeTabId) || myTabs.value[0]) : null })
  if (!res.ok) toast(t('最多支持 4 个分屏', 'At most 4 panes'), 'info')
}

function openInNewWindow(tab) {
  if (!tab?.assetId) return
  const url = `${window.location.origin}/?asset=${encodeURIComponent(tab.assetId)}&standalone=1`
  const win = window.open(url, '_blank', 'width=1280,height=860')
  if (!win) toast(t('新窗口被浏览器拦截，请允许弹出窗口', 'Popup blocked by the browser'), 'error')
}

function onWheel(event) {
  if (!scroller.value) return
  if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
    scroller.value.scrollLeft += event.deltaY
    event.preventDefault()
  }
}

function openList(event) {
  const items = myTabs.value.length
    ? [
        { label: t('打开的标签', 'Open tabs'), disabled: true },
        ...myTabs.value.map((tab) => ({
          label: tab.title,
          shortcut: tab.id === activeId.value ? '●' : '',
          action: () => activateTab(tab.id, props.pane?.id),
        })),
        { sep: true },
        {
          label: t('关闭全部标签', 'Close all tabs'),
          danger: true,
          action: async () => {
            for (const tab of [...myTabs.value]) await closeTab(tab.id)
          },
        },
      ]
    : [{ label: t('暂无标签', 'No open tabs'), disabled: true }]
  showContextMenu(event, items)
}

function onTabContext(event, tab) {
  showContextMenu(event, [
    { label: t('关闭', 'Close'), action: () => closeTab(tab.id) },
    { label: t('关闭其他', 'Close others'), action: async () => {
      for (const item of [...myTabs.value]) if (item.id !== tab.id) await closeTab(item.id)
      activateTab(tab.id, props.pane?.id)
    } },
    { label: t('关闭右侧', 'Close to the right'), action: async () => {
      const idx = myTabs.value.findIndex((x) => x.id === tab.id)
      for (const item of [...myTabs.value].slice(idx + 1)) await closeTab(item.id)
    } },
    { sep: true },
    {
      label: t('在新窗口打开', 'Open in new window'),
      disabled: !tab.assetId,
      action: () => openInNewWindow(tab),
    },
    { label: t('垂直分屏', 'Split vertical'), disabled: splitLimit.value, action: () => split('vertical') },
    { label: t('水平分屏', 'Split horizontal'), disabled: splitLimit.value, action: () => split('horizontal') },
    ...(state.panes.length > 1 && props.pane
      ? [{ label: t('关闭分屏', 'Close pane'), action: () => closePane(props.pane.id) }]
      : []),
    { sep: true },
    { label: t('重新打开', 'Reopen'), disabled: true },
  ])
}

async function closeAll() {
  for (const tab of [...myTabs.value]) await closeTab(tab.id)
}

const hasTabs = computed(() => myTabs.value.length > 0)
</script>

<template>
  <div class="tabbar">
    <button class="tab-more" @click="openList">
      <span>{{ t('列表', 'Tabs') }}</span>
      <ChevronDown :size="13" />
    </button>
    <div ref="scroller" style="display: flex; align-items: stretch; overflow-x: auto; scrollbar-width: none" @wheel="onWheel">
      <div
        v-for="tab in myTabs"
        :key="tab.id"
        class="tab"
        :class="{ active: tab.id === activeId }"
        :title="tab.title"
        @click="activateTab(tab.id, pane?.id)"
        @mousedown.middle.prevent="closeTab(tab.id)"
        @contextmenu="onTabContext($event, tab)"
      >
        <span class="tab-icon">
          <AssetIcon :icon="kindIcon[tab.kind] || 'terminal-square'" :size="14" :color="tab.id === activeId ? 'var(--accent)' : 'currentColor'" />
        </span>
        <span class="tab-title">{{ tab.title }}</span>
        <span v-if="tab.status === 'busy'" class="dot busy" style="margin: 0 2px" />
        <button class="tab-close" :title="t('关闭标签', 'Close tab')" @click.stop="closeTab(tab.id)">
          <X :size="12" />
        </button>
      </div>
    </div>
    <div class="tabbar-tools">
      <button class="icon-btn sm" :title="t('新建本地终端', 'New local terminal')" @click="newLocalTerminal(pane?.id)">
        <Plus :size="15" />
      </button>
      <button class="icon-btn sm" :title="t('关闭全部', 'Close all')" :disabled="!hasTabs" @click="closeAll">
        <CopyX :size="14" />
      </button>
    </div>
  </div>
</template>
