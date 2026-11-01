import { computed, reactive } from 'vue'
import { api } from './api'

export const ASSET_TYPE_LIST = [
  { id: 0, name: 'Dir', label: '目录', en: 'Folder', icon: 'folder', color: '#d9b45a' },
  { id: 1, name: 'SSHPrivateKey', label: '私钥', en: 'Private key', icon: 'key', color: '#c9a227' },
  { id: 2, name: 'SSH', label: 'SSH 连接', en: 'SSH', icon: 'server', color: '#7f9cf5' },
  { id: 3, name: 'FTP', label: 'SFTP 连接', en: 'SFTP', icon: 'folder-tree', color: '#5aa9e6' },
  { id: 4, name: 'DB', label: '数据库', en: 'Database', icon: 'database', color: '#5aa9e6' },
  { id: 5, name: 'QuickCmd', label: '快捷命令', en: 'Quick command', icon: 'terminal-square', color: '#f0a35e' },
  { id: 6, name: 'DBQuery', label: 'SQL 查询', en: 'SQL query', icon: 'file-code', color: '#7ec699' },
  { id: 7, name: 'Docker', label: 'Docker', en: 'Docker', icon: 'container', color: '#4c9aff' },
  { id: 8, name: 'DockerRegistry', label: '镜像仓库', en: 'Registry', icon: 'layers', color: '#4c9aff' },
  { id: 9, name: 'Setting', label: '设置', en: 'Setting', icon: 'settings', color: '#9aa0a6' },
  { id: 10, name: 'Hotkey', label: '快捷键', en: 'Hotkey', icon: 'command', color: '#9aa0a6' },
  { id: 11, name: 'QuickCmdGroup', label: '命令分组', en: 'Command group', icon: 'folder', color: '#f0a35e' },
  { id: 12, name: 'RDP', label: 'RDP', en: 'RDP', icon: 'monitor', color: '#9aa0a6' },
  { id: 13, name: 'VNC', label: 'VNC', en: 'VNC', icon: 'monitor', color: '#9aa0a6' },
  { id: 14, name: 'RepositorySource', label: '备份仓库', en: 'Repository', icon: 'cloud', color: '#9aa0a6' },
  { id: 15, name: 'Redis', label: 'Redis', en: 'Redis', icon: 'database-zap', color: '#e05d5d' },
  { id: 16, name: 'Shell', label: '本地终端', en: 'Local shell', icon: 'square-terminal', color: '#37c2b1' },
  { id: 17, name: 'Serial', label: '串口', en: 'Serial', icon: 'usb', color: '#9aa0a6' },
  { id: 18, name: 'Telnet', label: 'Telnet', en: 'Telnet', icon: 'globe', color: '#b07cff' },
  { id: 19, name: 'SSHPasswordPair', label: '密码对', en: 'Password pair', icon: 'shield', color: '#c9a227' },
  { id: 20, name: 'SSHTunnel', label: 'SSH 隧道', en: 'SSH tunnel', icon: 'git-branch', color: '#7f9cf5' },
  { id: 21, name: 'LLMProviderConfig', label: '模型供应商', en: 'Model provider', icon: 'sparkles', color: '#b07cff' },
]

export const TYPE_INFO = Object.fromEntries(ASSET_TYPE_LIST.map((t) => [t.name, t]))

export const DB_TYPES = [
  { id: 'mysql', label: 'MySQL', port: 3306 },
  { id: 'mariadb', label: 'MariaDB', port: 3306 },
  { id: 'postgresql', label: 'PostgreSQL', port: 5432 },
  { id: 'sqlite', label: 'SQLite', port: 0 },
  { id: 'duckdb', label: 'DuckDB', port: 0 },
  { id: 'clickhouse', label: 'ClickHouse', port: 9000 },
  { id: 'mongodb', label: 'MongoDB', port: 27017 },
  { id: 'sqlserver', label: 'SQL Server', port: 1433 },
  { id: 'oracle', label: 'Oracle', port: 1521 },
  { id: 'dameng', label: '达梦 DM8', port: 5236 },
]

const DEFAULT_SETTINGS = {
  appearance: {
    theme: 'dark',
    accent: 'blue',
    fontSize: 13,
    terminalFontSize: 13,
    terminalFontFamily: 'JetBrains Mono, Menlo, Consolas, monospace',
    language: 'zh-CN',
  },
  terminal: { scrollback: 5000, cursorStyle: 'block', cursorBlink: true, copyOnSelect: false, confirmClose: true, rightClick: 'menu', logSessions: false },
  editor: { fontSize: 13, tabSize: 2, wordWrap: false, autoSave: false },
  agent: {
    allowSilentFileRead: true,
    allowSilentFileWrite: false,
    allowSilentCommand: false,
    allowSilentReadonlySql: true,
    blockLocal: false,
    maxToolRounds: 12,
  },
  ai: { providers: [], activeProviderId: '', activeModel: '', systemPrompt: '' },
  general: {
    confirmOnExit: true,
    recentLimit: 30,
    hotkeys: {
      search: 'Ctrl+K',
      settings: 'Ctrl+,',
      localTerminal: 'Ctrl+`',
      closeTab: 'Ctrl+W',
      history: 'Ctrl+E',
      togglePanel: 'Ctrl+Shift+E',
      splitVertical: 'Ctrl+Shift+=',
      splitHorizontal: 'Ctrl+Shift+-',
      moveLeft: 'Ctrl+ArrowLeft',
      moveRight: 'Ctrl+ArrowRight',
      moveUp: 'Ctrl+ArrowUp',
      moveDown: 'Ctrl+ArrowDown',
    },
  },
  // Reserved (round 21): mirrored from backend store.py so deepMerge keeps
  // them; none of these keys is read by any feature code.
  update: { channel: 'stable', env: 'production', manifestUrl: '', autoCheck: false },
  lock: { enabled: false, idleMinutes: 0 },
  plugins: { enabled: false, dir: '' },
  mcp: { enabled: false, transport: 'stdio' },
  cloud: { encryption: false },
}

function deepMerge(base, patch) {
  if (!patch || typeof patch !== 'object') return base
  const out = Array.isArray(base) ? [...base] : { ...base }
  for (const [k, v] of Object.entries(patch)) {
    if (v && typeof v === 'object' && !Array.isArray(v) && base && typeof base[k] === 'object' && !Array.isArray(base[k])) {
      out[k] = deepMerge(base[k], v)
    } else {
      out[k] = v
    }
  }
  return out
}

export const state = reactive({
  ready: false,
  bootError: '',
  info: null,
  assets: [],
  settings: JSON.parse(JSON.stringify(DEFAULT_SETTINGS)),
  tabs: [],
  activeTabId: null,
  panes: [{ id: 'pane_1', activeTabId: null, size: 1 }],
  activePaneId: 'pane_1',
  splitDir: 'vertical',
  selectedAssetId: null,
  expanded: {},
  panelVisible: true,
  panelWidth: 292,
  sideMode: 'assets', // assets | history | ai
  history: [],
  chats: [],
  toasts: [],
  assetDialog: { open: false, asset: null, parentId: null, typeName: 'SSH' },
  settingsOpen: false,
  searchOpen: false,
  pickerOpen: false,
  aboutOpen: false,
  taskPanelOpen: false,
  chromeProxyOpen: false,
  chromeProxy: { available: false, path: '', tried: [], sessions: [], maxSessions: 8 },
  batchOpen: false,
  connecting: false,
  // Update check (#91, round 23): the result of the last read-only manifest
  // check plus its loading/error state.  Nothing here is persisted.
  updateCheck: { checking: false, result: null, error: '' },
  // Server-side lock state (round 22).  The password hash never reaches the
  // browser; only enabled/hasPassword/locked/backoff data does.
  lock: {
    enabled: false,
    hasPassword: false,
    locked: false,
    idleMinutes: 0,
    lockedAt: null,
    failedAttempts: 0,
    retryAfter: 0,
    minPasswordLength: 4,
    maxPasswordLength: 128,
  },
})

export const assetMap = computed(() => new Map(state.assets.map((a) => [a.id, a])))

export const assetTree = computed(() => {
  const nodes = new Map()
  for (const a of state.assets) nodes.set(a.id, { ...a, children: [] })
  const roots = []
  for (const node of nodes.values()) {
    if (node.parentId && nodes.has(node.parentId)) nodes.get(node.parentId).children.push(node)
    else roots.push(node)
  }
  const sortFn = (a, b) => (a.sort || 0) - (b.sort || 0) || a.name.localeCompare(b.name, 'zh-Hans-CN')
  const walk = (list) => {
    list.sort(sortFn)
    for (const item of list) walk(item.children)
  }
  walk(roots)
  return roots
})

export const activeTab = computed(() => state.tabs.find((t) => t.id === state.activeTabId) || null)

/* --------------------------------------------------------------- panes */
// 视图拆分：对应原版「垂直分屏 / 水平分屏 / 同屏打开」，最多 4 个面板。
export const MAX_PANES = 4
let paneSeq = 1

export const activePane = computed(() => state.panes.find((p) => p.id === state.activePaneId) || state.panes[0])

export function paneTabs(paneId) {
  return state.tabs.filter((t) => (t.paneId || state.panes[0].id) === paneId)
}

export function paneActiveTab(paneId) {
  const pane = state.panes.find((p) => p.id === paneId)
  const tabs = paneTabs(paneId)
  return tabs.find((t) => t.id === (pane?.activeTabId)) || tabs[tabs.length - 1] || null
}

function nextPaneId() {
  let id = ''
  do { id = `pane_${++paneSeq}` } while (state.panes.some((p) => p.id === id))
  return id
}

export function normalizePaneSizes() {
  const n = state.panes.length || 1
  const sum = state.panes.reduce((acc, p) => acc + (p.size || 0), 0)
  if (!sum || Math.abs(sum - 1) > 0.02) state.panes.forEach((p) => { p.size = 1 / n })
}

function resetPaneSizes() {
  const n = state.panes.length || 1
  state.panes.forEach((p) => { p.size = 1 / n })
}

export function resizePanes(index, delta) {
  const a = state.panes[index]
  const b = state.panes[index + 1]
  if (!a || !b) return
  const min = 0.15
  const total = (a.size || 0) + (b.size || 0)
  const nextA = Math.min(total - min, Math.max(min, (a.size || 0) + delta))
  a.size = nextA
  b.size = total - nextA
}

export function canSplit() {
  return state.panes.length < MAX_PANES
}

function cloneTabIntoPane(tab, paneId) {
  const params = { ...(tab.params || {}) }
  delete params.pendingCommand
  delete params.pendingToken
  let key = tab.key
  if (state.tabs.some((t) => t.paneId === paneId && t.key === key)) key = `${tab.key}#${paneSeq}`
  return openTab({
    kind: tab.kind,
    title: tab.title,
    assetId: tab.assetId,
    icon: tab.icon,
    params,
    key,
    paneId,
    forceNew: true,
  })
}

export function splitPane(direction = 'vertical', options = {}) {
  if (!canSplit()) return { ok: false, reason: 'max' }
  const source = options.tab || activeTab.value
  state.splitDir = direction === 'horizontal' ? 'horizontal' : 'vertical'
  const pane = { id: nextPaneId(), activeTabId: null, size: 0 }
  state.panes.push(pane)
  resetPaneSizes()
  state.activePaneId = pane.id
  state.activeTabId = null
  if (source) cloneTabIntoPane(source, pane.id)
  return { ok: true, paneId: pane.id }
}

export function focusPane(direction) {
  if (state.panes.length < 2) return false
  const idx = state.panes.findIndex((p) => p.id === state.activePaneId)
  if (idx < 0) return false
  const row = state.splitDir !== 'horizontal'
  let next = idx
  if (row) {
    if (direction === 'left') next = Math.max(0, idx - 1)
    else if (direction === 'right') next = Math.min(state.panes.length - 1, idx + 1)
  } else if (direction === 'up') next = Math.max(0, idx - 1)
  else if (direction === 'down') next = Math.min(state.panes.length - 1, idx + 1)
  if (next === idx) return false
  state.activePaneId = state.panes[next].id
  state.activeTabId = state.panes[next].activeTabId || null
  return true
}

export function focusPaneById(paneId) {
  const pane = state.panes.find((p) => p.id === paneId)
  if (!pane) return
  state.activePaneId = pane.id
  state.activeTabId = pane.activeTabId || null
}

function prunePane(paneId) {
  if (state.panes.length <= 1) return
  const pane = state.panes.find((p) => p.id === paneId)
  if (!pane || paneTabs(paneId).length) return
  const idx = state.panes.indexOf(pane)
  state.panes.splice(idx, 1)
  normalizePaneSizes()
  if (state.activePaneId === paneId) {
    const next = state.panes[Math.max(0, idx - 1)]
    state.activePaneId = next.id
    state.activeTabId = next.activeTabId || null
  }
}

export async function closePane(paneId) {
  if (state.panes.length <= 1) return
  for (const tab of [...paneTabs(paneId)]) await closeTab(tab.id)
  prunePane(paneId)
}

export function openAssetInNewPane(asset) {
  if (!asset || asset.typeName === 'Dir') return null
  if (!canSplit()) {
    toast(t('最多支持 4 个分屏', 'At most 4 panes'), 'info')
    return null
  }
  const pane = { id: nextPaneId(), activeTabId: null, size: 0 }
  state.panes.push(pane)
  resetPaneSizes()
  state.activePaneId = pane.id
  state.activeTabId = null
  return openAsset(asset)
}

export function typeInfo(type) {
  return ASSET_TYPE_LIST.find((t) => t.id === type) || ASSET_TYPE_LIST[0]
}

export function iconNameForAsset(asset) {
  if (!asset) return 'folder'
  const info = TYPE_INFO[asset.typeName] || TYPE_INFO.Dir
  if (asset.typeName === 'DB') return 'database'
  return info.icon
}

let toastSeq = 0
export function toast(message, kind = 'info', timeout = 3800) {
  const id = ++toastSeq
  state.toasts.push({ id, message, kind })
  setTimeout(() => {
    const idx = state.toasts.findIndex((t) => t.id === id)
    if (idx >= 0) state.toasts.splice(idx, 1)
  }, timeout)
}

export function applyAppearance() {
  const ap = state.settings.appearance || {}
  let theme = ap.theme || 'dark'
  if (theme === 'system') {
    theme = window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  }
  document.documentElement.dataset.theme = theme
  document.documentElement.dataset.accent = ap.accent || 'blue'
  document.documentElement.style.setProperty('--base-font-size', `${ap.fontSize || 13}px`)
  document.body.style.fontSize = `${ap.fontSize || 13}px`
}

export async function bootstrap() {
  try {
    const [info, assetRes, settings] = await Promise.all([
      api.get('/api/public/info'),
      api.get('/api/assets'),
      api.get('/api/settings'),
    ])
    state.info = info
    state.assets = assetRes.assets || []
    state.settings = deepMerge(JSON.parse(JSON.stringify(DEFAULT_SETTINGS)), settings || {})
    applyAppearance()
    await refreshChromeProxy()
    // Lock state is authoritative on the server, so a refresh or a new tab
    // immediately sees the overlay instead of trusting client storage.
    await refreshLockStatus()
    state.tabs = []
    state.activeTabId = null
    state.panes = [{ id: 'pane_1', activeTabId: null, size: 1 }]
    state.activePaneId = 'pane_1'
    state.splitDir = 'vertical'
    ensureDefaultAssets()
    try {
      const hist = await api.get('/api/history', { limit: 30 })
      state.history = hist.history || []
    } catch {}
    try {
      const chats = await api.get('/api/chats')
      state.chats = chats.chats || []
    } catch {}
    state.ready = true
    // 多窗口：独立浏览器窗口通过 ?asset=<id> 只打开目标资产（原版「新窗口打开」）
    try {
      const params = new URLSearchParams(window.location.search)
      const assetId = params.get('asset')
      if (assetId) {
        const asset = state.assets.find((a) => a.id === assetId)
        if (asset) openAsset(asset)
      }
    } catch {}
  } catch (err) {
    state.bootError = String(err.message || err)
    throw err
  }
}

async function ensureDefaultAssets() {
  if (state.assets.some((a) => a.typeName === 'Shell')) return
  try {
    const res = await api.post('/api/assets', {
      typeName: 'Shell',
      name: '本地终端',
      config: { shell: '', cwd: '' },
    })
    state.assets.push(res.asset)
  } catch (err) {
    console.warn('seed shell failed', err)
  }
}

export async function refreshAssets() {
  const res = await api.get('/api/assets')
  state.assets = res.assets || []
}

export async function refreshChromeProxy() {
  try {
    const res = await api.get('/api/chrome-proxy')
    state.chromeProxy = { ...state.chromeProxy, ...res }
  } catch (err) {
    // Keep the last known snapshot; the launch/stop paths surface real errors.
  }
  return state.chromeProxy
}

export async function saveSettings(patch) {
  const settings = await api.put('/api/settings', patch)
  state.settings = deepMerge(state.settings, settings || {})
  applyAppearance()
  return state.settings
}

function mergeLockStatus(status) {
  if (!status || typeof status !== 'object') return state.lock
  state.lock = { ...state.lock, ...status }
  if (state.settings.lock) {
    state.settings.lock.enabled = !!state.lock.enabled
    state.settings.lock.idleMinutes = state.lock.idleMinutes
  }
  return state.lock
}

export async function refreshLockStatus() {
  try {
    return mergeLockStatus(await api.get('/api/lock/status'))
  } catch {
    return state.lock
  }
}

export async function lockNow() {
  return mergeLockStatus(await api.post('/api/lock/lock', {}))
}

export async function unlockWithPassword(password) {
  return mergeLockStatus(await api.post('/api/lock/verify', { password }))
}

export async function saveLockPassword(oldPassword, newPassword) {
  return mergeLockStatus(await api.post('/api/lock/password', { oldPassword, newPassword }))
}

export async function clearLockPassword(oldPassword) {
  return mergeLockStatus(await api.del('/api/lock/password', { oldPassword }))
}

export async function updateLockConfig(patch) {
  return mergeLockStatus(await api.put('/api/lock/config', patch))
}

/* ------------------------------------------------------- update (#91/#93) */
export async function checkUpdate() {
  state.updateCheck.checking = true
  state.updateCheck.error = ''
  try {
    const result = await api.get('/api/update/check')
    state.updateCheck.result = result
    return result
  } catch (err) {
    const info = err?.payload?.error || {}
    state.updateCheck.result = null
    state.updateCheck.error = info.message || String(err?.message || err)
    throw err
  } finally {
    state.updateCheck.checking = false
  }
}

export async function setUpdateChannel(patch) {
  const result = await api.post('/api/update/channel', patch)
  state.settings = deepMerge(state.settings, {
    update: { channel: result.channel, env: result.env },
  })
  return result
}

export async function saveUpdatePrefs(patch) {
  const settings = await saveSettings({ update: patch })
  return settings.update || {}
}

// autoCheck is a single delayed check after bootstrap, never a background
// poll.  The module flag also makes an accidental second call a no-op.
let autoCheckScheduled = false
export function scheduleAutoUpdateCheck(delay = 1500) {
  if (autoCheckScheduled) return false
  autoCheckScheduled = true
  setTimeout(() => {
    if (!state.settings.update?.autoCheck) return
    checkUpdate().catch(() => {})
  }, delay)
  return true
}

let lastActivitySent = 0
export async function sendLockActivity(force = false) {
  if (state.lock.locked || !state.lock.enabled) return
  const now = Date.now()
  if (!force && now - lastActivitySent < 15000) return
  lastActivitySent = now
  try {
    await api.post('/api/lock/activity', {})
  } catch {
    /* activity pings are best-effort */
  }
}

export async function createAsset(payload) {
  const res = await api.post('/api/assets', payload)
  state.assets.push(res.asset)
  return res.asset
}

export async function updateAsset(id, patch) {
  const res = await api.put(`/api/assets/${id}`, patch)
  const idx = state.assets.findIndex((a) => a.id === id)
  if (idx >= 0) state.assets.splice(idx, 1, res.asset)
  return res.asset
}

export async function deleteAsset(id) {
  await api.del(`/api/assets/${id}`)
  const remove = new Set([id])
  let changed = true
  while (changed) {
    changed = false
    for (const a of state.assets) {
      if (a.parentId && remove.has(a.parentId) && !remove.has(a.id)) {
        remove.add(a.id)
        changed = true
      }
    }
  }
  state.assets = state.assets.filter((a) => !remove.has(a.id))
  for (const tab of [...state.tabs]) {
    if (tab.assetId && remove.has(tab.assetId)) closeTab(tab.id)
  }
}

export function openAssetDialog(typeName = 'SSH', parentId = null, asset = null) {
  state.assetDialog = { open: true, typeName, parentId, asset }
}

/* ------------------------------------------------------------------ tabs */
let tabSeq = 0
export function openTab(config) {
  const paneId = config.paneId || state.activePaneId || state.panes[0].id
  const pane = state.panes.find((p) => p.id === paneId) || state.panes[0]
  const key = config.key || `${config.kind}:${config.assetId || config.title}`
  const existing = state.tabs.find((t) => t.key === key && (t.paneId || state.panes[0].id) === pane.id)
  if (existing && !config.forceNew) {
    activateTab(existing.id, pane.id)
    return existing
  }
  const tab = {
    id: `tab_${++tabSeq}`,
    key,
    kind: config.kind,
    title: config.title,
    assetId: config.assetId || null,
    icon: config.icon || 'terminal',
    params: config.params || {},
    closable: config.closable !== false,
    paneId: pane.id,
    sessionId: null,
    status: 'idle',
  }
  state.tabs.push(tab)
  pane.activeTabId = tab.id
  state.activePaneId = pane.id
  state.activeTabId = tab.id
  return tab
}

export async function closeTab(id) {
  const idx = state.tabs.findIndex((t) => t.id === id)
  if (idx < 0) return
  const tab = state.tabs[idx]
  const paneId = tab.paneId || state.panes[0].id
  if (tab.sessionId) {
    try {
      await api.post(`/api/sessions/${tab.sessionId}/close`)
    } catch {}
  }
  state.tabs.splice(idx, 1)
  const pane = state.panes.find((p) => p.id === paneId)
  if (pane && pane.activeTabId === id) {
    const rest = paneTabs(paneId)
    pane.activeTabId = rest.length ? rest[Math.min(idx, rest.length - 1)].id : null
  }
  if (state.activeTabId === id) {
    const focused = state.panes.find((p) => p.id === state.activePaneId)
    const fallback = (focused && focused.activeTabId) || state.tabs[state.tabs.length - 1]?.id || null
    state.activeTabId = fallback
  }
  prunePane(paneId)
}

export function activateTab(id, paneId = null) {
  const tab = state.tabs.find((t) => t.id === id)
  const pid = paneId || tab?.paneId || state.activePaneId
  const pane = state.panes.find((p) => p.id === pid)
  if (pane) pane.activeTabId = id
  if (pane) state.activePaneId = pane.id
  state.activeTabId = id
}

export function patchTab(id, patch) {
  const tab = state.tabs.find((t) => t.id === id)
  if (tab) Object.assign(tab, patch)
}

/* --------------------------------------------------------------- actions */
export function openAsset(asset) {
  if (!asset) return
  const info = TYPE_INFO[asset.typeName] || TYPE_INFO.Dir
  if (asset.typeName === 'Dir') {
    state.expanded[asset.id] = !state.expanded[asset.id]
    state.selectedAssetId = asset.id
    return
  }
  state.selectedAssetId = asset.id
  if (asset.typeName === 'FTP') {
    return openTab({
      kind: 'files',
      title: asset.name,
      assetId: asset.id,
      icon: info.icon,
      key: `files:${asset.id}`,
      params: { typeName: asset.typeName },
    })
  }
  if (['SSH', 'Telnet', 'Shell', 'Serial'].includes(asset.typeName)) {
    return openTab({
      kind: 'terminal',
      title: asset.name,
      assetId: asset.id,
      icon: info.icon,
      key: `terminal:${asset.id}`,
      params: { typeName: asset.typeName },
    })
  }
  if (asset.typeName === 'VNC') {
    return openTab({
      kind: 'vnc',
      title: asset.name,
      assetId: asset.id,
      icon: info.icon,
      key: `vnc:${asset.id}`,
      params: { typeName: asset.typeName },
    })
  }
  if (asset.typeName === 'SSHTunnel') {
    return openTab({
      kind: 'tunnel',
      title: asset.name,
      assetId: asset.id,
      icon: 'git-branch',
      key: `tunnel:${asset.id}`,
    })
  }
  if (asset.typeName === 'DB') {
    return openTab({
      kind: 'db',
      title: asset.name,
      assetId: asset.id,
      icon: 'database',
      key: `db:${asset.id}`,
    })
  }
  if (asset.typeName === 'Redis') {
    return openTab({
      kind: 'redis',
      title: asset.name,
      assetId: asset.id,
      icon: 'database-zap',
      key: `redis:${asset.id}`,
    })
  }
  if (asset.typeName === 'Docker') {
    return openTab({
      kind: 'docker',
      title: asset.name,
      assetId: asset.id,
      icon: 'container',
      key: `docker:${asset.id}`,
    })
  }
  if (asset.typeName === 'QuickCmd') {
    return runQuickCommand(asset)
  }
  if (asset.typeName === 'DBQuery') {
    return openTab({
      kind: 'db',
      title: asset.name,
      assetId: asset.config?.assetId || null,
      icon: 'file-code',
      key: `query:${asset.id}`,
      params: { queryAssetId: asset.id },
    })
  }
  if (asset.typeName === 'VNC') {
    return openTab({
      kind: 'vnc',
      title: asset.name,
      assetId: asset.id,
      icon: info.icon,
      key: `vnc:${asset.id}`,
      params: { typeName: asset.typeName },
    })
  }
  if (asset.typeName === 'SSHTunnel') {
    toast('SSH 隧道的独立管理暂未实现，可在数据库 / Redis 连接中直接选择 SSH 隧道', 'info', 5200)
    return
  }
  toast(`${info.label} 在当前版本中暂未实现`, 'info')
}

export async function runQuickCommand(asset) {
  const target = asset.config?.assetId
  let targetAsset = target ? assetMap.value.get(target) : null
  if (!targetAsset) {
    targetAsset = state.assets.find((a) => a.typeName === 'Shell')
  }
  if (!targetAsset) targetAsset = await createAsset({ typeName: 'Shell', name: '本地终端', config: {} })
  openAsset(targetAsset)
  const tab = state.tabs.find((t) => t.key === `terminal:${targetAsset.id}`)
  if (tab) tab.params = { ...(tab.params || {}), pendingCommand: asset.config?.command || '', pendingToken: Date.now() }
  return tab
}

export async function newLocalTerminal(paneId = null) {
  let shell = state.assets.find((a) => a.typeName === 'Shell')
  if (!shell) {
    shell = await createAsset({ typeName: 'Shell', name: '本地终端', config: {} })
  }
  if (paneId && state.panes.some((p) => p.id === paneId)) {
    state.activePaneId = paneId
    state.activeTabId = state.panes.find((p) => p.id === paneId)?.activeTabId || null
  }
  return openAsset(shell)
}

export async function openDockerDefault() {
  const dockerAssets = state.assets.filter((a) => a.typeName === 'Docker')
  if (dockerAssets.length) return openAsset(dockerAssets[0])
  return openTab({
    kind: 'docker',
    title: 'Docker 本机',
    assetId: null,
    icon: 'container',
    key: 'docker:local',
  })
}

export async function openDbDefault() {
  const dbAssets = state.assets.filter((a) => a.typeName === 'DB')
  if (dbAssets.length) return openAsset(dbAssets[0])
  toast('还没有数据库连接，请先在资产列表中新建', 'info')
}

export async function openRedisDefault() {
  const redisAssets = state.assets.filter((a) => a.typeName === 'Redis')
  if (redisAssets.length) return openAsset(redisAssets[0])
  toast('还没有 Redis 连接，请先在资产列表中新建', 'info')
}

export function openAi() {
  return openTab({
    kind: 'ai',
    title: 'AI 助手',
    icon: 'sparkles',
    key: 'ai',
    params: {},
  })
}

export function openAssetMap() {
  return openTab({
    kind: 'map',
    title: t('资产地图', 'Asset map'),
    icon: 'network',
    key: 'asset-map',
    params: {},
  })
}

export function openHistory() {
  state.sideMode = 'history'
  state.panelVisible = true
}

export async function loadHistory() {
  const res = await api.get('/api/history', { limit: 60 })
  state.history = res.history || []
}

export function currentLocale() {
  return state.settings.appearance?.language || 'zh-CN'
}

export function t(zh, en) {
  return currentLocale() === 'en' && en ? en : zh
}
