<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  AlertTriangle, Copy, Crosshair, Eye, EyeOff, ExternalLink, Maximize2, Network, Pencil,
  RefreshCw, Search, X, ZoomIn, ZoomOut,
} from 'lucide-vue-next'
import AssetIcon from '../components/AssetIcon.vue'
import { api } from '../lib/api'
import {
  openAsset, openAssetDialog, refreshAssets, state, t, toast, TYPE_INFO,
} from '../lib/store'
import { showContextMenu } from '../lib/ui'

const props = defineProps({ tab: { type: Object, required: true } })

const NODE_W = 196
const NODE_H = 60
const GAP_X = 118
const GAP_Y = 24
const ISO_GAP_X = 56
const ISO_GAP_Y = 20
const PAD = 44
const GROUP_ORDER = { ssh: 0, db: 1, redis: 2, docker: 3, tunnel: 4, endpoint: 5 }
const GROUP_COLORS = {
  ssh: '#7f9cf5',
  db: '#5aa9e6',
  redis: '#e05d5d',
  docker: '#4c9aff',
  tunnel: '#37c2b1',
  endpoint: '#b07cff',
}
const GROUP_LABELS = {
  ssh: ['SSH', 'SSH'],
  db: ['数据库', 'Databases'],
  redis: ['Redis', 'Redis'],
  docker: ['Docker', 'Docker'],
  tunnel: ['隧道', 'Tunnels'],
  endpoint: ['其他连接', 'Endpoints'],
}

const uid = String(props.tab.id || 'map').replace(/[^a-zA-Z0-9_-]/g, '')

const graph = ref({ nodes: [], edges: [], cycles: [], stats: {} })
const loading = ref(false)
const error = ref('')
const query = ref('')
const typeFilter = ref('all')
const onlyConnected = ref(false)
const selectedId = ref('')
const hoverId = ref('')
const wrapEl = ref(null)
const svgEl = ref(null)
const size = reactive({ w: 960, h: 620 })
const view = reactive({ zoom: 1, x: 0, y: 0 })

let observer = null
let fitted = false
let panState = null
let watchTimer = null
const panning = ref(false)

const nodes = computed(() => graph.value.nodes || [])
const edges = computed(() => graph.value.edges || [])
const cycles = computed(() => graph.value.cycles || [])
const stats = computed(() => graph.value.stats || {})
const nodeById = computed(() => new Map(nodes.value.map((node) => [node.id, node])))
const selected = computed(() => nodeById.value.get(selectedId.value) || null)
const connectedIds = computed(
  () => new Set(nodes.value.filter((node) => node.inbound + node.outbound > 0).map((node) => node.id)),
)

const highlightedIds = computed(() => {
  if (!selectedId.value) return null
  const adjacency = new Map()
  for (const edge of edges.value) {
    if (!adjacency.has(edge.from)) adjacency.set(edge.from, [])
    if (!adjacency.has(edge.to)) adjacency.set(edge.to, [])
    adjacency.get(edge.from).push(edge.to)
    adjacency.get(edge.to).push(edge.from)
  }
  const seen = new Set([selectedId.value])
  const queue = [selectedId.value]
  while (queue.length) {
    const id = queue.shift()
    for (const next of adjacency.get(id) || []) {
      if (!seen.has(next)) {
        seen.add(next)
        queue.push(next)
      }
    }
  }
  return seen
})

const displayedNodes = computed(() =>
  nodes.value.filter((node) => layout.value.pos.has(node.id)),
)

const assetSignature = computed(() =>
  state.assets.map((asset) => `${asset.id}:${asset.updatedAt || ''}`).join('|'),
)

watch(assetSignature, () => {
  clearTimeout(watchTimer)
  watchTimer = setTimeout(() => {
    if (!loading.value) loadMap()
  }, 300)
})

const layout = computed(() => {
  const includeIsolated = !onlyConnected.value
  const connected = nodes.value.filter((node) => connectedIds.value.has(node.id))
  const isolated = includeIsolated
    ? nodes.value.filter((node) => !connectedIds.value.has(node.id))
    : []
  const byLayer = new Map()
  for (const node of connected) {
    const layer = Number(node.layer) || 0
    if (!byLayer.has(layer)) byLayer.set(layer, [])
    byLayer.get(layer).push(node)
  }
  const layers = [...byLayer.keys()].sort((a, b) => a - b)
  const pos = new Map()
  const sortByType = (a, b) =>
    (GROUP_ORDER[a.group] ?? 9) - (GROUP_ORDER[b.group] ?? 9)
    || a.name.localeCompare(b.name, 'zh-Hans-CN')
  const maxRows = Math.max(1, ...layers.map((layer) => byLayer.get(layer).length))
  const topologyHeight = maxRows * NODE_H + (maxRows - 1) * GAP_Y
  let x = PAD
  for (const layer of layers) {
    const list = [...byLayer.get(layer)]
    const parentY = (node) => {
      const values = []
      for (const edge of edges.value) {
        if (edge.to !== node.id) continue
        const parent = pos.get(edge.from)
        if (parent) values.push(parent.y)
      }
      return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : Number.MAX_SAFE_INTEGER
    }
    list.sort((a, b) => {
      const pa = parentY(a)
      const pb = parentY(b)
      if (pa !== pb) return pa - pb
      return sortByType(a, b)
    })
    const height = list.length * NODE_H + (list.length - 1) * GAP_Y
    const top = PAD + (topologyHeight - height) / 2
    list.forEach((node, index) => {
      pos.set(node.id, { x, y: top + index * (NODE_H + GAP_Y) })
    })
    x += NODE_W + GAP_X
  }
  let contentWidth = Math.max(PAD * 2 + NODE_W, x - GAP_X + PAD)
  let contentHeight = PAD * 2 + topologyHeight
  let isolatedLabel = null
  if (isolated.length) {
    const available = Math.max(size.w, 860)
    const columns = Math.max(
      1,
      Math.min(4, Math.floor((available - PAD * 2 + ISO_GAP_X) / (NODE_W + ISO_GAP_X))),
    )
    const rows = Math.ceil(isolated.length / columns)
    const top = PAD + topologyHeight + 92
    const sorted = [...isolated].sort(sortByType)
    sorted.forEach((node, index) => {
      const column = index % columns
      const row = Math.floor(index / columns)
      pos.set(node.id, {
        x: PAD + column * (NODE_W + ISO_GAP_X),
        y: top + row * (NODE_H + ISO_GAP_Y),
      })
    })
    isolatedLabel = { x: PAD, y: top - 20, count: isolated.length }
    contentWidth = Math.max(
      contentWidth,
      PAD * 2 + columns * NODE_W + (columns - 1) * ISO_GAP_X,
    )
    contentHeight = top + rows * NODE_H + (rows - 1) * ISO_GAP_Y + PAD
  }
  return {
    pos,
    layers,
    contentWidth: Math.max(1, contentWidth),
    contentHeight: Math.max(1, contentHeight),
    isolatedLabel,
  }
})

const edgeGeoms = computed(() => {
  const out = new Map()
  for (const edge of edges.value) {
    const source = layout.value.pos.get(edge.from)
    const target = layout.value.pos.get(edge.to)
    if (!source || !target) continue
    const forward = target.x >= source.x
    const sx = forward ? source.x + NODE_W : source.x
    const tx = forward ? target.x : target.x + NODE_W
    const sy = source.y + NODE_H / 2
    const ty = target.y + NODE_H / 2
    const curve = Math.max(54, Math.abs(tx - sx) * 0.5)
    const c1 = forward ? sx + curve : sx - curve
    const c2 = forward ? tx - curve : tx + curve
    out.set(edge.id, {
      d: `M ${sx} ${sy} C ${c1} ${sy}, ${c2} ${ty}, ${tx} ${ty}`,
      mx: (sx + tx) / 2,
      my: (sy + ty) / 2,
    })
  }
  return out
})

const filters = computed(() => [
  { value: 'all', label: t('全部类型', 'All types') },
  ...Object.entries(GROUP_LABELS).map(([value, [zh, en]]) => ({ value, label: t(zh, en) })),
])

function matches(node) {
  const text = query.value.trim().toLowerCase()
  if (!text) return true
  return [
    node.name,
    node.typeName,
    node.host,
    node.username,
    node.database,
    (node.folder || []).join('/'),
  ].join(' ').toLowerCase().includes(text)
}

function nodeDimmed(node) {
  if (typeFilter.value !== 'all' && node.group !== typeFilter.value) return true
  if (!matches(node)) return true
  if (highlightedIds.value && !highlightedIds.value.has(node.id)) return true
  return false
}

function edgeDimmed(edge) {
  const source = nodeById.value.get(edge.from)
  const target = nodeById.value.get(edge.to)
  if (!source || !target) return true
  if (!layout.value.pos.has(edge.from) || !layout.value.pos.has(edge.to)) return true
  if ((typeFilter.value !== 'all' && source.group !== typeFilter.value && target.group !== typeFilter.value)) return true
  if (query.value.trim() && !matches(source) && !matches(target)) return true
  if (highlightedIds.value && !(highlightedIds.value.has(edge.from) && highlightedIds.value.has(edge.to))) return true
  return false
}

function edgeActive(edge) {
  if (selectedId.value && highlightedIds.value?.has(edge.from) && highlightedIds.value?.has(edge.to)) return true
  return hoverId.value === edge.from || hoverId.value === edge.to
}

function groupColor(node) {
  return GROUP_COLORS[node.group] || GROUP_COLORS.endpoint
}

function typeLabel(node) {
  const info = TYPE_INFO[node.typeName]
  return info ? t(info.label, info.en) : node.typeName
}

function nodeSub(node) {
  if (node.typeName === 'SSHTunnel') return `${node.tunnelType || 'local'} · ${node.host || '—'}`
  if (node.typeName === 'DB') return `${node.database || 'database'} · ${node.host || '—'}:${node.port || '—'}`
  if (node.typeName === 'SSH') return `${node.username || 'user'}@${node.host || '—'}:${node.port || '22'}`
  return `${node.host || '—'}:${node.port || '—'}`
}

function routeFor(node) {
  if (!node) return []
  if (node.typeName === 'SSH') {
    return [...(node.chain || [])].reverse().concat(node.id)
  }
  if (node.sshAssetId) {
    const target = nodeById.value.get(node.sshAssetId)
    if (!target) return []
    return [...(target.chain || [])].reverse().concat(target.id)
  }
  return []
}

const selectedRoute = computed(() => routeFor(selected.value))
const outgoing = computed(() =>
  selected.value ? edges.value.filter((edge) => edge.from === selected.value.id) : [],
)
const incoming = computed(() =>
  selected.value ? edges.value.filter((edge) => edge.to === selected.value.id) : [],
)

function routeName(id) {
  return nodeById.value.get(id)?.name || id
}

function cycleText(cycle) {
  const names = cycle.names || cycle.ids?.map(routeName) || []
  return [...names, names[0]].filter(Boolean).join(' → ')
}

function selectNode(id) {
  selectedId.value = id
  nextTick(() => {
    if (highlightedIds.value) fitToComponent(highlightedIds.value)
  })
}

function fitToComponent(ids) {
  if (!ids || !ids.size) return
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const id of ids) {
    const point = layout.value.pos.get(id)
    if (!point) continue
    minX = Math.min(minX, point.x)
    minY = Math.min(minY, point.y)
    maxX = Math.max(maxX, point.x + NODE_W)
    maxY = Math.max(maxY, point.y + NODE_H)
  }
  if (!Number.isFinite(minX)) return
  const width = Math.max(1, maxX - minX)
  const height = Math.max(1, maxY - minY)
  const availableWidth = size.w - (selected.value ? 306 : 0)
  const padding = 46
  const zoom = clamp(
    Math.min((availableWidth - padding * 2) / width, (size.h - padding * 2) / height),
    0.28,
    1.25,
  )
  view.zoom = zoom
  view.x = (availableWidth - width * zoom) / 2 - minX * zoom
  view.y = (size.h - height * zoom) / 2 - minY * zoom
}

function clearSelection() {
  selectedId.value = ''
}

function nodeAsset(node) {
  return state.assets.find((asset) => asset.id === node.id)
}

function openNode(node) {
  const asset = nodeAsset(node)
  if (!asset) {
    toast(t('资产不存在或已删除', 'Asset no longer exists'), 'error')
    return
  }
  openAsset(asset)
}

function editNode(node) {
  const asset = nodeAsset(node)
  if (!asset) {
    toast(t('资产不存在或已删除', 'Asset no longer exists'), 'error')
    return
  }
  openAssetDialog(asset.typeName, asset.parentId, asset)
}

function copyText(text, label) {
  navigator.clipboard?.writeText(text)
  toast(`${label}${t('已复制', ' copied')}`, 'success')
}

function nodeMenu(event, node) {
  selectedId.value = node.id
  showContextMenu(event, [
    { label: t('打开资产', 'Open asset'), action: () => openNode(node) },
    { label: t('编辑资产', 'Edit asset'), action: () => editNode(node) },
    { sep: true },
    { label: t('居中显示', 'Center in view'), action: () => focusNode(node.id) },
    { label: t('复制名称', 'Copy name'), action: () => copyText(node.name, t('名称', 'Name')) },
    { label: t('复制资产 ID', 'Copy asset ID'), action: () => copyText(node.id, 'ID') },
  ])
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

function fit() {
  if (!nodes.value.length) {
    view.zoom = 1
    view.x = 0
    view.y = 0
    return
  }
  const content = layout.value
  const padding = 56
  const availableWidth = size.w - (selected.value ? 306 : 0)
  const zoom = clamp(
    Math.min(
      (availableWidth - padding * 2) / content.contentWidth,
      (size.h - padding * 2) / content.contentHeight,
    ),
    0.18,
    1.45,
  )
  view.zoom = zoom
  view.x = (availableWidth - content.contentWidth * zoom) / 2
  view.y = (size.h - content.contentHeight * zoom) / 2
}

function zoomBy(factor) {
  const mx = size.w / 2
  const my = size.h / 2
  const wx = (mx - view.x) / view.zoom
  const wy = (my - view.y) / view.zoom
  const next = clamp(view.zoom * factor, 0.18, 2.6)
  view.x = mx - wx * next
  view.y = my - wy * next
  view.zoom = next
}

function onWheel(event) {
  if (!svgEl.value) return
  const rect = svgEl.value.getBoundingClientRect()
  const mx = event.clientX - rect.left
  const my = event.clientY - rect.top
  const wx = (mx - view.x) / view.zoom
  const wy = (my - view.y) / view.zoom
  const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12
  const next = clamp(view.zoom * factor, 0.18, 2.6)
  view.x = mx - wx * next
  view.y = my - wy * next
  view.zoom = next
}

function focusNode(id) {
  const point = layout.value.pos.get(id)
  if (!point) return
  const availableWidth = size.w - (selected.value ? 306 : 0)
  view.zoom = Math.max(view.zoom, 0.75)
  view.x = availableWidth / 2 - (point.x + NODE_W / 2) * view.zoom
  view.y = size.h / 2 - (point.y + NODE_H / 2) * view.zoom
}

function beginPan(event) {
  if (event.button !== 0) return
  panState = {
    startX: event.clientX,
    startY: event.clientY,
    originX: view.x,
    originY: view.y,
    moved: false,
  }
  const move = (e) => {
    if (!panState) return
    const dx = e.clientX - panState.startX
    const dy = e.clientY - panState.startY
    if (Math.abs(dx) + Math.abs(dy) > 3) {
      panState.moved = true
      panning.value = true
    }
    view.x = panState.originX + dx
    view.y = panState.originY + dy
  }
  const up = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', up)
    panState = null
    panning.value = false
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', up)
}

async function toggleOnlyConnected() {
  onlyConnected.value = !onlyConnected.value
  await nextTick()
  fit()
}

async function loadMap() {
  loading.value = true
  error.value = ''
  try {
    graph.value = (await api.get('/api/asset-map')) || { nodes: [], edges: [], cycles: [], stats: {} }
    await nextTick()
    if (!fitted) {
      fit()
      fitted = true
    }
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

async function refresh() {
  loading.value = true
  try {
    await refreshAssets()
  } catch (err) {
    error.value = String(err.message || err)
  }
  await loadMap()
}

function onResize() {
  const element = wrapEl.value
  if (!element) return
  size.w = Math.max(320, element.clientWidth)
  size.h = Math.max(260, element.clientHeight)
  if (nodes.value.length && !fitted) {
    fit()
    fitted = true
  }
}

function onKeydown(event) {
  if (event.key !== 'Escape' || state.activeTabId !== props.tab.id) return
  if (document.querySelector('.ctx-menu')) return
  selectedId.value = ''
}

onMounted(async () => {
  onResize()
  observer = new ResizeObserver(onResize)
  if (wrapEl.value) observer.observe(wrapEl.value)
  window.addEventListener('keydown', onKeydown)
  await loadMap()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  clearTimeout(watchTimer)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="view map-view">
    <div class="toolbar map-toolbar">
      <Network :size="15" style="color: var(--accent)" />
      <b style="font-size: 12.5px">{{ t('资产地图', 'Asset map') }}</b>
      <span class="badge">{{ stats.nodes || 0 }} {{ t('节点', 'nodes') }}</span>
      <span class="badge">{{ stats.edges || 0 }} {{ t('连线', 'links') }}</span>
      <span v-if="stats.maxDepth" class="badge accent">{{ stats.maxDepth }} {{ t('层跳板', 'hops') }}</span>
      <span v-if="stats.cycles" class="badge danger">{{ stats.cycles }} {{ t('循环', 'cycles') }}</span>
      <span class="grow" style="flex: 1" />
      <label class="map-search">
        <Search :size="13" />
        <input v-model="query" class="input" :placeholder="t('搜索名称 / 主机 / 类型', 'Search name / host / type')" />
      </label>
      <select v-model="typeFilter" class="select map-filter">
        <option v-for="item in filters" :key="item.value" :value="item.value">{{ item.label }}</option>
      </select>
      <button class="btn sm map-toggle" :class="{ on: !onlyConnected }" :title="t('显示/隐藏未连线资产', 'Show / hide isolated assets')" @click="toggleOnlyConnected">
        <Eye v-if="!onlyConnected" :size="13" /><EyeOff v-else :size="13" />
        {{ onlyConnected ? t('仅拓扑', 'Topology') : t('含孤立', 'With isolated') }}
      </button>
      <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="refresh">
        <RefreshCw :size="14" :class="{ 'tool-spin': loading }" />
      </button>
      <button class="icon-btn sm" :title="t('适应窗口', 'Fit to view')" @click="fit"><Maximize2 :size="14" /></button>
      <button class="icon-btn sm" :title="t('缩小', 'Zoom out')" @click="zoomBy(1 / 1.15)"><ZoomOut :size="14" /></button>
      <button class="icon-btn sm" :title="t('放大', 'Zoom in')" @click="zoomBy(1.15)"><ZoomIn :size="14" /></button>
    </div>

    <div ref="wrapEl" class="map-wrap">
      <div v-if="error" class="alert error map-error">{{ error }}</div>

      <svg
        ref="svgEl"
        class="map-svg"
        :class="{ panning }"
        :viewBox="`0 0 ${size.w} ${size.h}`"
        @wheel.prevent="onWheel"
      >
        <defs>
          <marker
            v-for="kind in ['jump', 'ssh', 'cycle']"
            :id="`${uid}-arrow-${kind}`"
            :key="kind"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path
              d="M0,0 L10,5 L0,10 z"
              :fill="kind === 'jump' ? '#7f9cf5' : kind === 'ssh' ? '#37c2b1' : '#e05d5d'"
            />
          </marker>
        </defs>

        <rect
          class="map-bg"
          x="0"
          y="0"
          :width="size.w"
          :height="size.h"
          @mousedown="beginPan"
          @click="clearSelection"
        />

        <g :transform="`translate(${view.x} ${view.y}) scale(${view.zoom})`">
          <g class="map-edges">
            <g v-for="edge in edges" :key="edge.id">
              <template v-if="edgeGeoms.get(edge.id)">
                <path
                  :d="edgeGeoms.get(edge.id).d"
                  class="map-edge"
                  :class="[edge.kind, { cycle: edge.cycle, dim: edgeDimmed(edge), active: edgeActive(edge) }]"
                  :marker-end="`url(#${uid}-arrow-${edge.cycle ? 'cycle' : edge.kind})`"
                />
                <text
                  v-if="edge.kind === 'jump' || edgeActive(edge) || edge.cycle"
                  :x="edgeGeoms.get(edge.id).mx"
                  :y="edgeGeoms.get(edge.id).my - 7"
                  class="map-edge-label"
                  :class="{ dim: edgeDimmed(edge), cycle: edge.cycle }"
                >{{ edge.label }}</text>
              </template>
            </g>
          </g>

          <g v-if="layout.isolatedLabel" class="map-section">
            <text :x="layout.isolatedLabel.x" :y="layout.isolatedLabel.y" class="map-section-label">
              {{ t('未连线资产', 'Isolated assets') }} · {{ layout.isolatedLabel.count }}
            </text>
          </g>

          <g class="map-nodes">
            <g
              v-for="node in displayedNodes"
              :key="node.id"
              :transform="`translate(${layout.pos.get(node.id).x} ${layout.pos.get(node.id).y})`"
              class="map-node-wrap"
              @mouseenter="hoverId = node.id"
              @mouseleave="hoverId = ''"
            >
              <foreignObject :width="NODE_W" :height="NODE_H" style="overflow: visible">
                <div
                  xmlns="http://www.w3.org/1999/xhtml"
                  class="map-node"
                  :class="{
                    dim: nodeDimmed(node),
                    selected: selectedId === node.id,
                    cycle: node.cycle,
                    isolated: !connectedIds.has(node.id),
                  }"
                  :style="{ '--node-color': groupColor(node) }"
                  :data-node-id="node.id"
                  :title="`${node.name}\n${typeLabel(node)} · ${nodeSub(node)}`"
                  @click.stop="selectNode(node.id)"
                  @dblclick.stop.prevent="openNode(node)"
                  @contextmenu.prevent.stop="nodeMenu($event, node)"
                >
                  <span class="map-node-icon"><AssetIcon :type-name="node.typeName" :size="16" /></span>
                  <span class="map-node-main">
                    <span class="map-node-name">{{ node.name }}</span>
                    <span class="map-node-sub mono">{{ nodeSub(node) }}</span>
                  </span>
                  <span v-if="node.cycle" class="map-node-flag danger" :title="t('循环依赖', 'Cycle')">!</span>
                  <span v-else-if="node.depth" class="map-node-flag" :title="t('跳板层数', 'Jump depth')">{{ node.depth }}</span>
                </div>
              </foreignObject>
            </g>
          </g>
        </g>
      </svg>

      <div v-if="!loading && !nodes.length" class="empty-state map-empty">
        <Network :size="26" style="opacity: 0.4" />
        <div class="big">{{ t('还没有可绘制的连接资产', 'No connection assets to draw') }}</div>
        <div class="tiny">{{ t('新建 SSH、数据库、Redis、Docker 或隧道资产后会自动出现在地图上。', 'Create SSH, database, Redis, Docker or tunnel assets to populate the map.') }}</div>
      </div>

      <div v-if="cycles.length" class="map-alert">
        <AlertTriangle :size="14" />
        <div>
          <b>{{ t('检测到循环依赖', 'Cyclic dependencies detected') }}</b>
          <div v-for="(cycle, index) in cycles" :key="index" class="mono tiny">{{ cycleText(cycle) }}</div>
        </div>
      </div>

      <div class="map-legend">
        <span v-for="(color, key) in GROUP_COLORS" :key="key" class="legend-item">
          <i :style="{ background: color }" />{{ t(GROUP_LABELS[key][0], GROUP_LABELS[key][1]) }}
        </span>
        <span class="legend-item"><i class="line jump" />{{ t('跳板', 'Jump') }}</span>
        <span class="legend-item"><i class="line ssh" />{{ t('SSH 依赖', 'SSH') }}</span>
      </div>

      <aside v-if="selected" class="map-detail">
        <div class="detail-head">
          <span class="detail-icon" :style="{ color: groupColor(selected) }">
            <AssetIcon :type-name="selected.typeName" :size="17" />
          </span>
          <div class="detail-title">
            <b>{{ selected.name }}</b>
            <span class="tiny">{{ typeLabel(selected) }}</span>
          </div>
          <button class="icon-btn sm" @click="clearSelection"><X :size="13" /></button>
        </div>

        <div class="detail-rows">
          <div class="detail-row">
            <span>{{ t('地址', 'Address') }}</span>
            <span class="mono">{{ selected.host || '—' }}:{{ selected.port || '—' }}</span>
          </div>
          <div class="detail-row" v-if="selected.username">
            <span>{{ t('用户名', 'Username') }}</span>
            <span class="mono">{{ selected.username }}</span>
          </div>
          <div class="detail-row" v-if="selected.database">
            <span>{{ t('数据库', 'Database') }}</span>
            <span class="mono">{{ selected.database }}</span>
          </div>
          <div class="detail-row">
            <span>{{ t('目录', 'Folder') }}</span>
            <span>{{ (selected.folder || []).join(' / ') || t('根目录', 'Root') }}</span>
          </div>
          <div class="detail-row">
            <span>{{ t('关系', 'Relations') }}</span>
            <span>{{ incoming.length }} {{ t('入', 'in') }} / {{ outgoing.length }} {{ t('出', 'out') }}</span>
          </div>
        </div>

        <div v-if="selectedRoute.length" class="detail-section">
          <div class="detail-label">{{ t('跳板链（由外到内）', 'Jump chain (outermost first)') }}</div>
          <div class="chain-flow">
            <template v-for="(id, index) in selectedRoute" :key="id">
              <button class="chain-chip" :class="{ current: id === selected.id }" @click="selectNode(id)">{{ routeName(id) }}</button>
              <span v-if="index < selectedRoute.length - 1" class="chain-arrow">→</span>
            </template>
          </div>
        </div>

        <div v-if="selected.missing?.length" class="alert error" style="margin-top: 10px; font-size: 11px">
          {{ t('有引用指向不存在的资产：', 'References point to missing assets: ') }}
          {{ selected.missing.map((item) => `${item.field}=${item.assetId}`).join(', ') }}
        </div>

        <div class="detail-section" v-if="outgoing.length">
          <div class="detail-label">{{ t('依赖', 'Depends on') }}</div>
          <button v-for="edge in outgoing" :key="edge.id" class="relation" @click="selectNode(edge.to)">
            <span class="badge">{{ edge.label }}</span>{{ routeName(edge.to) }}
          </button>
        </div>
        <div class="detail-section" v-if="incoming.length">
          <div class="detail-label">{{ t('被引用', 'Referenced by') }}</div>
          <button v-for="edge in incoming" :key="edge.id" class="relation" @click="selectNode(edge.from)">
            <span class="badge">{{ edge.label }}</span>{{ routeName(edge.from) }}
          </button>
        </div>

        <div class="detail-actions">
          <button class="btn sm" @click="focusNode(selected.id)"><Crosshair :size="13" /> {{ t('居中', 'Center') }}</button>
          <button class="btn sm" @click="openNode(selected)"><ExternalLink :size="13" /> {{ t('打开', 'Open') }}</button>
          <button class="btn sm primary" @click="editNode(selected)"><Pencil :size="13" /> {{ t('编辑', 'Edit') }}</button>
          <button class="btn sm ghost" @click="copyText(selected.id, 'ID')"><Copy :size="13" /></button>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.map-view { flex-direction: column; min-height: 0; overflow: hidden; }
.map-toolbar { gap: 7px; flex-wrap: wrap; }
.map-search { display: flex; align-items: center; gap: 5px; height: 27px; padding: 0 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg-elev); color: var(--text-3); }
.map-search .input { border: 0; background: transparent; height: 24px; width: 190px; padding: 0; outline: none; color: var(--text-1); font-size: 12px; }
.map-filter { height: 27px; width: 128px; font-size: 12px; }
.map-toggle.on { color: var(--accent); border-color: var(--accent); background: var(--accent-weak-2); }
.map-wrap { position: relative; flex: 1; min-height: 0; overflow: hidden; background: radial-gradient(circle at 1px 1px, var(--border) 1px, transparent 0) 0 0 / 22px 22px, var(--bg); }
.map-svg { width: 100%; height: 100%; display: block; cursor: grab; user-select: none; }
.map-svg.panning { cursor: grabbing; }
.map-bg { fill: transparent; }
.map-edge { fill: none; stroke-width: 1.6; opacity: 0.72; transition: opacity 0.15s; }
.map-edge.jump { stroke: #7f9cf5; }
.map-edge.ssh { stroke: #37c2b1; }
.map-edge.cycle { stroke: #e05d5d; stroke-width: 2; stroke-dasharray: 6 4; }
.map-edge.dim { opacity: 0.1; }
.map-edge.active { opacity: 1; stroke-width: 2.6; }
.map-edge-label { font-size: 9px; fill: var(--text-3); text-anchor: middle; paint-order: stroke; stroke: var(--bg); stroke-width: 3px; stroke-linejoin: round; }
.map-edge-label.cycle { fill: #e05d5d; }
.map-edge-label.dim { opacity: 0.15; }
.map-node { box-sizing: border-box; width: 196px; height: 60px; display: flex; align-items: center; gap: 9px; padding: 8px 10px; border: 1px solid var(--border); border-left: 3px solid var(--node-color); border-radius: 8px; background: var(--bg-panel); box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18); cursor: pointer; transition: opacity 0.15s, box-shadow 0.15s, transform 0.12s; overflow: hidden; }
.map-node:hover { transform: translateY(-1px); box-shadow: 0 4px 14px rgba(0, 0, 0, 0.28); }
.map-node.selected { outline: 2px solid var(--accent); outline-offset: 1px; }
.map-node.dim { opacity: 0.18; }
.map-node.isolated { border-style: dashed; }
.map-node.cycle { border-color: #e05d5d; border-left-color: #e05d5d; }
.map-node-icon { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 6px; background: color-mix(in srgb, var(--node-color) 16%, transparent); color: var(--node-color); flex-shrink: 0; }
.map-node-main { display: flex; flex-direction: column; min-width: 0; flex: 1; gap: 2px; }
.map-node-name { font-size: 12px; font-weight: 600; color: var(--text-1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.map-node-sub { font-size: 10px; color: var(--text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.map-node-flag { flex-shrink: 0; min-width: 16px; height: 16px; border-radius: 8px; padding: 0 5px; display: inline-flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700; background: var(--accent-weak); color: var(--accent); }
.map-node-flag.danger { background: var(--red-weak); color: #e05d5d; }
.map-section-label { font-size: 10.5px; letter-spacing: 0.06em; fill: var(--text-3); text-transform: uppercase; }
.map-error { position: absolute; top: 12px; left: 12px; right: 12px; z-index: 4; }
.map-empty { position: absolute; inset: 0; pointer-events: none; }
.map-alert { position: absolute; top: 12px; left: 12px; z-index: 3; display: flex; gap: 8px; align-items: flex-start; padding: 9px 11px; border: 1px solid rgba(224, 93, 93, 0.45); border-radius: 8px; background: var(--red-weak); color: #f08a8a; font-size: 11.5px; max-width: 430px; }
.map-legend { position: absolute; left: 12px; bottom: 12px; z-index: 3; display: flex; flex-wrap: wrap; gap: 4px 10px; padding: 7px 10px; border: 1px solid var(--border); border-radius: 7px; background: color-mix(in srgb, var(--bg-panel) 88%, transparent); font-size: 10.5px; color: var(--text-3); max-width: min(760px, calc(100% - 330px)); }
.legend-item { display: inline-flex; align-items: center; gap: 4px; }
.legend-item i { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
.legend-item i.line { width: 14px; height: 2px; border-radius: 1px; }
.legend-item i.line.jump { background: #7f9cf5; }
.legend-item i.line.ssh { background: #37c2b1; }
.map-detail { position: absolute; top: 12px; right: 12px; bottom: 12px; width: 292px; z-index: 4; display: flex; flex-direction: column; gap: 10px; padding: 12px; border: 1px solid var(--border-strong); border-radius: 10px; background: var(--bg-panel); box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35); overflow: auto; }
.detail-head { display: flex; align-items: center; gap: 8px; }
.detail-icon { display: inline-flex; width: 30px; height: 30px; align-items: center; justify-content: center; border-radius: 8px; background: var(--bg-elev); }
.detail-title { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.detail-title b { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.detail-rows { display: flex; flex-direction: column; gap: 5px; }
.detail-row { display: flex; justify-content: space-between; gap: 10px; font-size: 11.5px; color: var(--text-2); }
.detail-row > span:first-child { color: var(--text-3); flex-shrink: 0; }
.detail-row > span:last-child { text-align: right; overflow-wrap: anywhere; }
.detail-section { display: flex; flex-direction: column; gap: 6px; }
.detail-label { font-size: 10.5px; color: var(--text-3); letter-spacing: 0.04em; }
.chain-flow { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
.chain-chip { border: 1px solid var(--border); background: var(--bg-elev); color: var(--text-2); border-radius: 5px; padding: 2px 7px; font-size: 10.5px; cursor: pointer; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chain-chip.current { border-color: var(--accent); color: var(--accent); }
.chain-arrow { color: var(--text-3); font-size: 10px; }
.relation { display: flex; align-items: center; gap: 6px; border: 1px solid var(--border); background: var(--bg-elev); color: var(--text-2); border-radius: 6px; padding: 5px 7px; font-size: 11px; text-align: left; cursor: pointer; }
.relation:hover { border-color: var(--accent); }
.detail-actions { display: flex; gap: 5px; flex-wrap: wrap; margin-top: auto; padding-top: 4px; }
</style>
