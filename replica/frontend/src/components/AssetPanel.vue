<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import {
  Chrome, Download, MoreHorizontal, Network, Plus, RefreshCw, Search, Square, Upload, Users, X,
} from 'lucide-vue-next'
import AssetTreeNode from './AssetTreeNode.vue'
import { api, getToken } from '../lib/api'
import {
  assetTree, createAsset, deleteAsset, loadHistory, openAsset, openAssetDialog, openAssetInNewPane,
  openAssetMap, refreshAssets, refreshChromeProxy, state, t, toast, updateAsset,
} from '../lib/store'
import { confirmDialog, promptDialog, showContextMenu } from '../lib/ui'
import { formatTime } from '../lib/format'

const search = ref('')
const searchOpen = ref(false)
const fileInput = ref(null)
const chromeStarting = ref('')

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return []
  return state.assets
    .filter((a) => {
      const hay = `${a.name} ${a.alias || ''} ${a.typeName} ${a.config?.host || ''} ${a.config?.database || ''} ${a.config?.file || ''}`.toLowerCase()
      return hay.includes(q)
    })
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
})

const expanded = computed(() => state.expanded)

async function handleDrop(payload) {
  if (!payload?.id) return
  try {
    await api.post(`/api/assets/${payload.id}/move`, { parentId: payload.parentId, beforeId: payload.beforeId })
    await refreshAssets()
    toast(t('已移动资产', 'Asset moved'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function select(node) {
  state.selectedAssetId = node.id
}

function toggle(node) {
  state.expanded[node.id] = !state.expanded[node.id]
}

async function openMenu(event, node) {
  // preventDefault must run synchronously, before the menu data refresh below.
  event.preventDefault?.()
  event.stopPropagation?.()
  state.selectedAssetId = node.id
  if (node.typeName === 'SSH') {
    // Refresh to decide whether to show the "stop" entry. Bound the wait so a
    // slow backend can never make the context menu feel unresponsive.
    await Promise.race([
      refreshChromeProxy(),
      new Promise((resolve) => setTimeout(resolve, 400)),
    ])
  }
  const proxySessions = node.typeName === 'SSH'
    ? (state.chromeProxy?.sessions || []).filter((item) => item.assetId === node.id)
    : []
  const info = { label: t('打开', 'Open'), disabled: node.typeName === 'Dir', action: () => openAsset(node) }
  const items = [
    info,
    { label: t('同屏打开（新分屏）', 'Open in new pane'), disabled: node.typeName === 'Dir', action: () => openAssetInNewPane(node) },
    ...(node.typeName === 'SSH'
      ? [
          {
            label: t('通过服务器代理 Chrome', 'Proxy Chrome via server'),
            icon: Chrome,
            disabled: chromeStarting.value === node.id,
            action: () => launchChromeProxy(node),
          },
          ...(proxySessions.length
            ? [{
                label: t('停止通过服务器代理的 Chrome', 'Stop proxied Chrome'),
                icon: Square,
                action: () => stopChromeProxy(node),
              }]
            : []),
        ]
      : []),
    { label: t('新建连接', 'New connection'), action: () => (state.pickerOpen = true, state.pickerParentId = node.typeName === 'Dir' ? node.id : node.parentId) },
    { label: t('新建子目录', 'New folder'), action: () => createFolder(node.id) },
    { label: t('编辑', 'Edit'), action: () => openAssetDialog(node.typeName, node.parentId, node) },
    { label: t('复制', 'Duplicate'), action: async () => { await api.post(`/api/assets/${node.id}/duplicate`); await refreshAssets(); toast(t('已复制资产', 'Asset duplicated'), 'success') } },
    { label: node.favorite ? t('取消收藏', 'Unfavorite') : t('收藏', 'Favorite'), action: async () => { await updateAsset(node.id, { favorite: !node.favorite }); toast(t('已更新', 'Updated'), 'success') } },
    { sep: true },
    { label: t('导出该资产', 'Export asset'), action: () => exportAssets([node.id]) },
    { label: t('删除', 'Delete'), danger: true, action: () => removeAsset(node) },
  ]
  showContextMenu(event, items)
}

async function launchChromeProxy(node) {
  if (chromeStarting.value) return
  chromeStarting.value = node.id
  const label = node.alias || node.name
  toast(
    t('请确保已经安装 Chrome，且未开启其他代理软件！！！', 'Make sure Chrome is installed and no other proxy tool is active!'),
    'info',
    5000,
  )
  try {
    const res = await api.post('/api/chrome-proxy/start', { assetId: node.id })
    const session = res.session || {}
    toast(
      t(
        `已通过「${label}」启动 Chrome，SOCKS5 ${session.host || '127.0.0.1'}:${session.port}`,
        `Chrome launched via "${label}" (SOCKS5 ${session.host || '127.0.0.1'}:${session.port})`,
      ),
      'success',
      7000,
    )
  } catch (err) {
    toast(String(err.message || err), 'error', 9000)
  } finally {
    chromeStarting.value = ''
    await refreshChromeProxy()
  }
}

async function stopChromeProxy(node) {
  const running = (state.chromeProxy?.sessions || []).filter((item) => item.assetId === node.id)
  if (!running.length) {
    toast(t('该资产没有运行中的 Chrome 代理', 'This asset has no running Chrome proxy'), 'info')
    await refreshChromeProxy()
    return
  }
  const label = node.alias || node.name
  try {
    for (const session of running) {
      await api.post(`/api/chrome-proxy/${session.id}/stop`)
    }
    toast(
      t(`已停止「${label}」的 Chrome 代理`, `Stopped the Chrome proxy for "${label}"`),
      'success',
    )
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    await refreshChromeProxy()
  }
}

async function removeAsset(node) {
  const ok = await confirmDialog({
    title: t('删除资产', 'Delete asset'),
    message: t(`确定删除「${node.name}」及其所有子资产吗？此操作不可撤销。`, `Delete "${node.name}" and all children? This cannot be undone.`),
    danger: true,
    okText: t('删除', 'Delete'),
  })
  if (!ok) return
  try {
    await deleteAsset(node.id)
    toast(t('已删除', 'Deleted'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function createFolder(parentId = null) {
  const name = await promptDialog({
    title: t('新建目录', 'New folder'),
    message: t('目录名称', 'Folder name'),
    placeholder: t('例如：生产环境', 'e.g. Production'),
  })
  if (!name) return
  try {
    await createAsset({ typeName: 'Dir', name, parentId })
    if (parentId) state.expanded[parentId] = true
    toast(t('目录已创建', 'Folder created'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function newConnection(parentId = null) {
  state.pickerParentId = parentId
  state.pickerOpen = true
}

function exportAssets(ids) {
  const token = getToken()
  const qs = ids && ids.length ? `?ids=${encodeURIComponent(ids.join(','))}&token=${encodeURIComponent(token)}` : `?token=${encodeURIComponent(token)}`
  const a = document.createElement('a')
  a.href = `/api/assets-export${qs}`
  a.download = 'h1khub-assets.json'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

function pickImport() {
  fileInput.value?.click()
}

async function onImport(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  try {
    const payload = JSON.parse(await file.text())
    const res = await api.post('/api/assets-import', { payload, mode: 'merge' })
    await refreshAssets()
    toast(t(`已导入 ${res.imported} 个资产`, `Imported ${res.imported} assets`), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function moreMenu(event) {
  showContextMenu(event, [
    { label: t('新建目录', 'New folder'), action: () => createFolder(null) },
    { label: t('新建连接', 'New connection'), action: () => newConnection(null) },
    { sep: true },
    { label: t('资产地图', 'Asset map'), icon: Network, action: () => openAssetMap() },
    { label: t('Chrome 代理（查看/停止）…', 'Chrome proxy (view / stop)…'), icon: Chrome, action: () => { state.chromeProxyOpen = true } },
    { sep: true },
    { label: t('导入资产', 'Import assets'), action: pickImport },
    { label: t('导出全部资产', 'Export all assets'), action: () => exportAssets(null) },
    { label: t('刷新', 'Refresh'), action: () => refreshAssets() },
    { sep: true },
    { label: t('批量执行（多主机）', 'Batch execution'), action: () => { state.batchOpen = true } },
  ])
}

async function refresh() {
  try {
    await refreshAssets()
    if (state.sideMode === 'history') await loadHistory()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function historyMenu(event, item) {
  showContextMenu(event, [
    {
      label: t('打开', 'Open'),
      action: () => {
        const asset = state.assets.find((a) => a.id === item.asset_id)
        if (asset) openAsset(asset)
        else toast(t('资产已被删除', 'Asset no longer exists'), 'info')
      },
    },
    { label: t('复制名称', 'Copy name'), action: () => navigator.clipboard?.writeText(item.title) },
  ])
}

watch(() => state.sideMode, (v) => { if (v === 'history') loadHistory() })
onMounted(() => { if (state.sideMode === 'history') loadHistory() })
</script>

<template>
  <aside class="asset-panel">
    <div class="panel-head">
      <span class="panel-title">{{ state.sideMode === 'history' ? t('最近历史', 'Recent history') : t('资产列表', 'Assets') }}</span>
      <div class="panel-actions">
        <button class="icon-btn sm" :title="t('搜索', 'Search')" @click="searchOpen = !searchOpen; search = ''">
          <Search :size="14" />
        </button>
        <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="refresh">
          <RefreshCw :size="14" />
        </button>
        <button v-if="state.sideMode === 'assets'" class="icon-btn sm" :title="t('导入', 'Import')" @click="pickImport">
          <Upload :size="14" />
        </button>
        <button v-if="state.sideMode === 'assets'" class="icon-btn sm" :title="t('导出', 'Export')" @click="exportAssets(null)">
          <Download :size="14" />
        </button>
        <button v-if="state.sideMode === 'assets'" class="icon-btn sm" :title="t('更多', 'More')" @click="moreMenu">
          <MoreHorizontal :size="15" />
        </button>
        <button class="icon-btn sm" :title="t('收起面板', 'Hide panel')" @click="state.panelVisible = false">
          <X :size="14" />
        </button>
      </div>
    </div>

    <div v-if="searchOpen" class="panel-search">
      <div class="searchbox">
        <span class="icon"><Search :size="13" /></span>
        <input v-model="search" class="input" :placeholder="t('搜索资产名称 / 主机 / 数据库', 'Search name / host / database')" autofocus />
      </div>
    </div>

    <template v-if="state.sideMode === 'assets'">
      <div class="tree">
        <template v-if="search.trim()">
          <AssetTreeNode
            v-for="node in filtered"
            :key="node.id"
            :node="{ ...node, children: [] }"
            :expanded="expanded"
            :selected-id="state.selectedAssetId"
            search-mode
            @select="select"
            @open="openAsset"
            @toggle="toggle"
            @menu="openMenu"
            @drop-asset="handleDrop"
          />
          <div v-if="!filtered.length" class="center-hint" style="padding: 30px 0; font-size: 12px">
            {{ t('没有匹配的资产', 'No assets found') }}
          </div>
        </template>
        <template v-else>
          <AssetTreeNode
            v-for="node in assetTree"
            :key="node.id"
            :node="node"
            :expanded="expanded"
            :selected-id="state.selectedAssetId"
            @select="select"
            @open="openAsset"
            @toggle="toggle"
            @menu="openMenu"
            @drop-asset="handleDrop"
          />
          <div v-if="!assetTree.length" class="empty-state" style="padding: 40px 16px; text-align: center; font-size: 12px">
            <div>{{ t('还没有资产', 'No assets yet') }}</div>
            <button class="btn sm" @click="newConnection(null)">{{ t('新建连接', 'New connection') }}</button>
          </div>
        </template>
      </div>
      <div class="panel-foot">
        <button class="btn ghost sm" style="flex: 1; justify-content: flex-start" @click="newConnection(null)">
          <Plus :size="14" /> {{ t('新建连接', 'New connection') }}
        </button>
        <button class="icon-btn sm" :title="t('批量执行', 'Batch execution')" @click="state.batchOpen = true"><Users :size="14" /></button>
        <button class="icon-btn sm" :title="t('新建目录', 'New folder')" @click="createFolder(null)">＋</button>
      </div>
    </template>

    <template v-else>
      <div class="tree">
        <div v-if="!state.history.length" class="center-hint" style="padding: 40px 12px; font-size: 12px">
          {{ t('暂无历史记录', 'No history yet') }}
        </div>
        <div
          v-for="item in state.history"
          :key="item.id"
          class="tree-row"
          style="height: 40px; align-items: flex-start; padding-top: 5px"
          @contextmenu="historyMenu($event, item)"
          @dblclick="() => { const a = state.assets.find((x) => x.id === item.asset_id); if (a) openAsset(a) }"
        >
          <div class="tree-icon" style="margin-top: 3px">
            <span class="dot" :class="{ off: item.kind !== 'open' }" />
          </div>
          <div style="flex: 1; min-width: 0">
            <div class="ellipsis" style="font-size: 12.5px">{{ item.title }}</div>
            <div class="tiny">{{ item.detail || 'open' }} · {{ formatTime(item.created_at) }}</div>
          </div>
        </div>
      </div>
      <div class="panel-foot">
        <button class="btn ghost sm" style="flex: 1; justify-content: flex-start" @click="loadHistory()">
          <RefreshCw :size="14" /> {{ t('刷新历史', 'Refresh') }}
        </button>
        <button class="btn ghost sm" @click="state.sideMode = 'assets'">{{ t('返回资产', 'Back to assets') }}</button>
      </div>
    </template>

    <input ref="fileInput" type="file" accept="application/json,.json" class="hidden" @change="onImport" />
  </aside>
</template>
