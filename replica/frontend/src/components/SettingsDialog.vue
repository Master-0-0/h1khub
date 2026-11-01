<script setup>
import { computed, reactive, ref, watch } from 'vue'
import {
  Check, Code2, Eye, EyeOff, Info, Keyboard, Loader2, Palette, Plus, Puzzle, RefreshCw, ShieldCheck,
  Sparkles, Terminal, Trash2, X,
} from 'lucide-vue-next'
import { api } from '../lib/api'
import {
  clearLockPassword, lockNow, refreshLockStatus, saveLockPassword, saveSettings, setUpdateChannel,
  state, saveUpdatePrefs, t, toast, updateLockConfig,
} from '../lib/store'
import { confirmDialog } from '../lib/ui'
import UpdateCheckPanel from './UpdateCheckPanel.vue'

// Reserved capability UI (round 21): sections are driven by
// GET /api/capabilities.  The controls are always disabled; no id string is
// special-cased in the rendering logic.
const RESERVED_GROUPS = computed(() => [
  { id: 'update', group: 'update', label: t('更新', 'Updates'), icon: RefreshCw },
  { id: 'extension', group: 'extension', label: t('扩展', 'Extensions'), icon: Puzzle },
])

const sections = computed(() => [
  { id: 'appearance', label: t('外观', 'Appearance'), icon: Palette },
  { id: 'terminal', label: t('终端', 'Terminal'), icon: Terminal },
  { id: 'editor', label: t('编辑器', 'Editor'), icon: Code2 },
  { id: 'hotkeys', label: t('快捷键', 'Shortcuts'), icon: Keyboard },
  { id: 'ai', label: t('AI 与模型', 'AI & models'), icon: Sparkles },
  { id: 'agent', label: t('Agent 审批', 'Agent approvals'), icon: ShieldCheck },
  { id: 'security', label: t('安全', 'Security'), icon: ShieldCheck },
  ...RESERVED_GROUPS.value.map((s) => ({ id: s.id, label: s.label, icon: s.icon })),
  { id: 'about', label: t('关于', 'About'), icon: Info },
])

const capabilities = ref([])
const capabilitiesError = ref('')

async function loadCapabilities() {
  try {
    const res = await api.get('/api/capabilities')
    capabilities.value = Array.isArray(res?.capabilities) ? res.capabilities : []
    capabilitiesError.value = ''
  } catch (err) {
    capabilities.value = []
    capabilitiesError.value = String(err?.message || err)
  }
}

watch(() => state.settingsOpen, (open) => {
  if (!open) return
  loadCapabilities()
  refreshLockStatus()
})
loadCapabilities()

function isReservedGroup(id) {
  return RESERVED_GROUPS.value.some((s) => s.id === id)
}

function capabilitiesFor(id) {
  const group = RESERVED_GROUPS.value.find((s) => s.id === id)?.group
  return capabilities.value.filter((c) => c.group === group)
}

function statusLabel(cap) {
  return cap.status === 'reserved'
    ? t('预留，未实现', 'Reserved, not implemented')
    : t('不可用（浏览器形态）', 'Unavailable in browser form')
}

function controlTitle(cap) {
  return cap.status === 'reserved'
    ? t('预留（未实现）', 'Reserved (not implemented)')
    : (cap.reason || t('不可用', 'Unavailable'))
}

/* ------------------------------------------------------- screen lock (r22) */
const LOCK_IDLE_OPTIONS = [
  { value: 0, label: t('关闭自动锁屏', 'Off') },
  { value: 1, label: t('1 分钟', '1 minute') },
  { value: 5, label: t('5 分钟', '5 minutes') },
  { value: 10, label: t('10 分钟', '10 minutes') },
  { value: 15, label: t('15 分钟', '15 minutes') },
  { value: 30, label: t('30 分钟', '30 minutes') },
  { value: 60, label: t('1 小时', '1 hour') },
]

const lockOldPassword = ref('')
const lockNewPassword = ref('')
const lockConfirmPassword = ref('')
const lockBusy = ref('')
const lockError = ref('')
const lockNotice = ref('')

function lockErrorText(err) {
  const info = err?.payload?.error || {}
  return info.message || String(err?.message || err)
}

function clearLockMessages() {
  lockError.value = ''
  lockNotice.value = ''
}

async function toggleLockEnabled() {
  if (lockBusy.value) return
  clearLockMessages()
  lockBusy.value = 'toggle'
  try {
    await updateLockConfig({ enabled: !state.lock.enabled })
    lockNotice.value = state.lock.enabled
      ? t('锁屏已启用', 'Screen lock enabled')
      : t('锁屏已关闭', 'Screen lock disabled')
  } catch (err) {
    lockError.value = lockErrorText(err)
  } finally {
    lockBusy.value = ''
  }
}

async function changeLockIdle(value) {
  if (lockBusy.value) return
  clearLockMessages()
  lockBusy.value = 'idle'
  try {
    await updateLockConfig({ idleMinutes: Number(value) })
    lockNotice.value = t('自动锁屏时间已保存', 'Idle lock updated')
  } catch (err) {
    lockError.value = lockErrorText(err)
  } finally {
    lockBusy.value = ''
  }
}

async function savePassword() {
  clearLockMessages()
  if (lockNewPassword.value !== lockConfirmPassword.value) {
    lockError.value = t('两次输入的新口令不一致', 'The new passwords do not match')
    return
  }
  lockBusy.value = 'password'
  try {
    await saveLockPassword(lockOldPassword.value, lockNewPassword.value)
    lockOldPassword.value = ''
    lockNewPassword.value = ''
    lockConfirmPassword.value = ''
    lockNotice.value = t('锁屏口令已保存', 'Screen-lock password saved')
  } catch (err) {
    lockError.value = lockErrorText(err)
  } finally {
    lockBusy.value = ''
  }
}

async function clearPassword() {
  clearLockMessages()
  const ok = await confirmDialog({
    title: t('清除锁屏口令', 'Clear screen-lock password'),
    message: t('清除后将同时关闭锁屏，需要当前口令确认。', 'This also disables the lock and requires the current password.'),
    danger: true,
  })
  if (!ok) return
  lockBusy.value = 'password'
  try {
    await clearLockPassword(lockOldPassword.value)
    lockOldPassword.value = ''
    lockNotice.value = t('锁屏口令已清除', 'Screen-lock password cleared')
  } catch (err) {
    lockError.value = lockErrorText(err)
  } finally {
    lockBusy.value = ''
  }
}

async function lockNowFromSettings() {
  clearLockMessages()
  try {
    await lockNow()
    state.settingsOpen = false
  } catch (err) {
    lockError.value = lockErrorText(err)
  }
}


/* ---------------------------------------------------- updates (#91/#93) */
const updateCurrent = computed(() => `${state.info?.version || '5.1.9'} · build ${state.info?.build || 519}`)
const hotCapability = computed(() => capabilities.value.find((c) => c.id === 'update.hot'))
const hotSummary = computed(() => hotCapability.value?.summary
  || t('下载 → MD5 比对 → 解压 → runtime → 重启；本地未实现。', 'Download → MD5 → extract → runtime → restart; not implemented.'))

function requestErrorText(err) {
  const info = err?.payload?.error || {}
  return info.message || String(err?.message || err)
}

function clearUpdateMessages() {
  updateError.value = ''
  updateNotice.value = ''
}

function syncUpdateDraft() {
  const upd = state.settings.update || {}
  if (draft.update) {
    draft.update.channel = upd.channel
    draft.update.env = upd.env
    draft.update.manifestUrl = upd.manifestUrl
    draft.update.autoCheck = !!upd.autoCheck
  }
  manifestDraft.value = upd.manifestUrl || ''
  channelDraft.value = upd.channel || 'stable'
  envDraft.value = upd.env || 'production'
}

async function toggleAutoCheck() {
  if (updateBusy.value) return
  clearUpdateMessages()
  updateBusy.value = 'auto'
  try {
    await saveUpdatePrefs({ autoCheck: !state.settings.update?.autoCheck })
    syncUpdateDraft()
    updateNotice.value = state.settings.update?.autoCheck
      ? t('启动后自动检查一次已开启', 'One check after startup enabled')
      : t('启动后自动检查已关闭', 'Automatic startup check disabled')
  } catch (err) {
    updateError.value = requestErrorText(err)
  } finally {
    updateBusy.value = ''
  }
}

async function saveManifestUrl() {
  if (updateBusy.value) return
  const value = (manifestDraft.value || '').trim()
  if (value === (state.settings.update?.manifestUrl || '')) return
  clearUpdateMessages()
  updateBusy.value = 'manifest'
  try {
    await saveUpdatePrefs({ manifestUrl: value })
    syncUpdateDraft()
    updateNotice.value = t('manifest 地址已保存', 'Manifest URL saved')
  } catch (err) {
    updateError.value = requestErrorText(err)
  } finally {
    updateBusy.value = ''
  }
}

async function changeUpdateChannel(field, value) {
  if (updateBusy.value) return
  clearUpdateMessages()
  updateBusy.value = field
  if (field === 'channel') channelDraft.value = value
  else envDraft.value = value
  try {
    await setUpdateChannel({ [field]: value })
    syncUpdateDraft()
    updateNotice.value = t('更新通道已保存', 'Update channel saved')
  } catch (err) {
    updateError.value = requestErrorText(err)
    syncUpdateDraft()
  } finally {
    updateBusy.value = ''
  }
}


const active = ref('appearance')
const draft = reactive({})
// Update section (#91/#93) uses immediate-save controls, so its own refs live
// beside the shared settings draft.
const manifestDraft = ref(state.settings.update?.manifestUrl || '')
const channelDraft = ref(state.settings.update?.channel || 'stable')
const envDraft = ref(state.settings.update?.env || 'production')
const updateBusy = ref('')
const updateError = ref('')
const updateNotice = ref('')
const saving = ref(false)
const testingId = ref('')
const testResult = reactive({})
const revealKey = reactive({})
const loadingModels = ref('')

function initEmptyDraft() {
  const settings = clone(state.settings)
  Object.assign(draft, settings)
  if (!Array.isArray(draft.ai?.providers)) draft.ai = { ...(draft.ai || {}), providers: [] }
}
initEmptyDraft()

const PRESETS = [
  { name: 'OpenAI', protocol: 'openai', baseUrl: 'https://api.openai.com/v1', models: ['gpt-4o', 'gpt-4o-mini', 'o3-mini'] },
  { name: 'DeepSeek', protocol: 'openai', baseUrl: 'https://api.deepseek.com/v1', models: ['deepseek-chat', 'deepseek-reasoner'] },
  { name: 'Anthropic Claude', protocol: 'anthropic', baseUrl: 'https://api.anthropic.com', models: ['claude-sonnet-4-5', 'claude-opus-4-1', 'claude-3-5-haiku-latest'] },
  { name: 'Google Gemini', protocol: 'openai', baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', models: ['gemini-2.5-pro', 'gemini-2.5-flash'] },
  { name: 'Kimi (Moonshot)', protocol: 'openai', baseUrl: 'https://api.moonshot.cn/v1', models: ['kimi-k2-0905-preview'] },
  { name: '智谱 GLM', protocol: 'openai', baseUrl: 'https://open.bigmodel.cn/api/paas/v4', models: ['glm-4.6'] },
  { name: '通义千问 (DashScope)', protocol: 'openai', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: ['qwen-max', 'qwen-plus'] },
  { name: '硅基流动 SiliconFlow', protocol: 'openai', baseUrl: 'https://api.siliconflow.cn/v1', models: ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen2.5-72B-Instruct'] },
  { name: 'Groq', protocol: 'openai', baseUrl: 'https://api.groq.com/openai/v1', models: ['llama-3.3-70b-versatile'] },
  { name: 'Mistral', protocol: 'openai', baseUrl: 'https://api.mistral.ai/v1', models: ['mistral-large-latest'] },
  { name: 'xAI Grok', protocol: 'openai', baseUrl: 'https://api.x.ai/v1', models: ['grok-4', 'grok-3-mini'] },
  { name: 'OpenRouter', protocol: 'openai', baseUrl: 'https://openrouter.ai/api/v1', models: [] },
  { name: 'Ollama（本地）', protocol: 'openai', baseUrl: 'http://127.0.0.1:11434/v1', models: ['qwen2.5-coder:7b', 'llama3.1:8b'] },
  { name: t('自定义 OpenAI 兼容', 'Custom OpenAI-compatible'), protocol: 'openai', baseUrl: '', models: [] },
]

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function resetDraft() {
  const settings = clone(state.settings)
  Object.keys(draft).forEach((k) => delete draft[k])
  Object.assign(draft, settings)
  if (!Array.isArray(draft.ai?.providers)) draft.ai = { ...(draft.ai || {}), providers: [] }
}

watch(
  () => state.settingsOpen,
  (open) => {
    if (!open) return
    resetDraft()
    active.value = 'appearance'
    syncUpdateDraft()
  },
  { immediate: true },
)

const providers = computed(() => draft.ai?.providers || [])
const activeProvider = computed(() => providers.value.find((p) => p.id === draft.ai?.activeProviderId) || null)
const activeModels = computed(() => (activeProvider.value?.models || []).filter(Boolean))

function addProvider(preset) {
  const id = 'p_' + Math.random().toString(36).slice(2, 8)
  draft.ai.providers.push({
    id,
    name: preset.name,
    protocol: preset.protocol,
    baseUrl: preset.baseUrl,
    apiKey: '',
    models: preset.models || [],
    defaultModel: (preset.models || [])[0] || '',
    hasKey: false,
  })
  if (!draft.ai.activeProviderId) draft.ai.activeProviderId = id
  active.value = 'ai'
}

function removeProvider(provider) {
  const idx = draft.ai.providers.findIndex((p) => p.id === provider.id)
  if (idx >= 0) draft.ai.providers.splice(idx, 1)
  if (draft.ai.activeProviderId === provider.id) {
    draft.ai.activeProviderId = draft.ai.providers[0]?.id || ''
    draft.ai.activeModel = draft.ai.providers[0]?.defaultModel || ''
  }
}

function selectPreset(event) {
  const menu = event.currentTarget
  const idx = Number(menu.dataset.index || 0)
  // simple cycling through presets via a small popup would be nicer; use prompt-free chooser:
  // handled by the preset grid rendered below instead
}

function modelText(provider) {
  return (provider.models || []).join(', ')
}

function updateModels(provider, value) {
  provider.models = value.split(',').map((s) => s.trim()).filter(Boolean)
  if (!provider.defaultModel || !provider.models.includes(provider.defaultModel)) {
    provider.defaultModel = provider.models[0] || ''
  }
  if (draft.ai.activeProviderId === provider.id && !draft.ai.activeModel) {
    draft.ai.activeModel = provider.defaultModel
  }
}

async function testProvider(provider) {
  testingId.value = provider.id
  testResult[provider.id] = null
  try {
    const model = provider.defaultModel || provider.models?.[0]
    const res = await api.post('/api/ai/test', {
      providerId: provider.id,
      provider: { ...provider },
      model,
    })
    testResult[provider.id] = { ok: true, message: res.message || t('连通正常', 'OK') }
  } catch (err) {
    testResult[provider.id] = { ok: false, message: String(err.message || err) }
  } finally {
    testingId.value = ''
  }
}

async function fetchModels(provider) {
  loadingModels.value = provider.id
  try {
    const res = await api.post('/api/ai/models', { providerId: provider.id, provider: { ...provider } })
    if (Array.isArray(res.models)) {
      provider.models = res.models
      if (!provider.defaultModel || !res.models.includes(provider.defaultModel)) provider.defaultModel = res.models[0] || ''
      toast(t(`已获取 ${res.models.length} 个模型`, `Fetched ${res.models.length} models`), 'success')
    }
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    loadingModels.value = ''
  }
}

async function save() {
  saving.value = true
  try {
    await saveSettings(clone(draft))
    state.settingsOpen = false
    toast(t('设置已保存', 'Settings saved'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    saving.value = false
  }
}

const THEMES = [
  { id: 'dark', label: t('暗色', 'Dark') },
  { id: 'light', label: t('亮色', 'Light') },
  { id: 'nord', label: 'Nord' },
  { id: 'dracula', label: 'Dracula' },
  { id: 'solarized', label: 'Solarized' },
  { id: 'github', label: 'GitHub' },
  { id: 'system', label: t('跟随系统', 'System') },
]
const ACCENTS = [
  { id: 'blue', color: '#4c9aff' },
  { id: 'teal', color: '#2fbfa0' },
  { id: 'violet', color: '#a78bfa' },
  { id: 'orange', color: '#f59e0b' },
]

function setActiveProvider(id) {
  draft.ai.activeProviderId = id
  const provider = providers.value.find((p) => p.id === id)
  draft.ai.activeModel = provider?.defaultModel || provider?.models?.[0] || ''
}
</script>

<template>
  <div v-if="state.settingsOpen" class="modal-mask" @mousedown.self="state.settingsOpen = false">
    <div class="modal settings-layout">
      <div class="settings-nav">
        <div style="padding: 4px 10px 10px; font-weight: 600">{{ t('设置', 'Settings') }}</div>
        <button v-for="section in sections" :key="section.id" :class="{ active: active === section.id }" @click="active = section.id">
          <component :is="section.icon" :size="15" />
          {{ section.label }}
        </button>
      </div>
      <div style="display: flex; flex-direction: column; flex: 1; min-width: 0">
        <div class="modal-head" style="border-bottom: 1px solid var(--border)">
          <span class="modal-title">{{ sections.find((s) => s.id === active)?.label }}</span>
          <button class="icon-btn sm" @click="state.settingsOpen = false"><X :size="14" /></button>
        </div>
        <div class="settings-body">
          <!-- appearance -->
          <div v-if="active === 'appearance'" class="settings-section">
            <h3>{{ t('外观与主题', 'Appearance') }}</h3>
            <div class="settings-row">
              <div><div class="label">{{ t('主题', 'Theme') }}</div><div class="desc">{{ t('暗色更适合长时间操作终端与数据表', 'Dark works best for long terminal sessions') }}</div></div>
              <div class="control">
                <div class="seg">
                  <button v-for="th in THEMES" :key="th.id" :class="{ active: draft.appearance.theme === th.id }" @click="draft.appearance.theme = th.id">{{ th.label }}</button>
                </div>
              </div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('强调色', 'Accent color') }}</div></div>
              <div class="control">
                <div class="theme-swatches">
                  <button
                    v-for="ac in ACCENTS" :key="ac.id" class="swatch"
                    :class="{ active: draft.appearance.accent === ac.id }"
                    :style="{ background: ac.color }"
                    @click="draft.appearance.accent = ac.id"
                  />
                </div>
              </div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('界面语言', 'Language') }}</div><div class="desc">{{ t('保存后立即生效', 'Applies after saving') }}</div></div>
              <div class="control">
                <select v-model="draft.appearance.language" class="select">
                  <option value="zh-CN">简体中文</option>
                  <option value="en">English</option>
                </select>
              </div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('界面字号', 'UI font size') }}</div></div>
              <div class="control"><input v-model.number="draft.appearance.fontSize" type="number" min="11" max="18" class="input" /></div>
            </div>
          </div>

          <!-- terminal -->
          <div v-else-if="active === 'terminal'" class="settings-section">
            <h3>{{ t('终端', 'Terminal') }}</h3>
            <div class="settings-row">
              <div><div class="label">{{ t('终端字号', 'Terminal font size') }}</div></div>
              <div class="control"><input v-model.number="draft.appearance.terminalFontSize" type="number" min="9" max="28" class="input" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('字体', 'Font family') }}</div></div>
              <div class="control"><input v-model="draft.appearance.terminalFontFamily" class="input" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('回滚行数', 'Scrollback') }}</div></div>
              <div class="control"><input v-model.number="draft.terminal.scrollback" type="number" min="500" max="100000" class="input" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('光标样式', 'Cursor style') }}</div></div>
              <div class="control">
                <select v-model="draft.terminal.cursorStyle" class="select">
                  <option value="block">{{ t('方块', 'Block') }}</option>
                  <option value="underline">{{ t('下划线', 'Underline') }}</option>
                  <option value="bar">{{ t('竖线', 'Bar') }}</option>
                </select>
              </div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('光标闪烁', 'Blinking cursor') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.terminal.cursorBlink }" @click="draft.terminal.cursorBlink = !draft.terminal.cursorBlink" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('选中即复制', 'Copy on select') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.terminal.copyOnSelect }" @click="draft.terminal.copyOnSelect = !draft.terminal.copyOnSelect" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('右键行为', 'Right click') }}</div></div>
              <div class="control">
                <select v-model="draft.terminal.rightClick" class="select">
                  <option value="menu">{{ t('浏览器菜单', 'Browser menu') }}</option>
                  <option value="select">{{ t('选中单词', 'Select word') }}</option>
                  <option value="paste">{{ t('粘贴', 'Paste') }}</option>
                </select>
              </div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('自动保存会话日志', 'Save session logs') }}</div><div class="desc">{{ t('关闭会话时写入数据目录 logs/，权限 0600', 'Write logs/ (0600) when a session closes') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.terminal.logSessions }" @click="draft.terminal.logSessions = !draft.terminal.logSessions" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('WebGL 渲染', 'WebGL renderer') }}</div><div class="desc">{{ t('使用 GPU 加速 xterm 渲染；不可用时自动回退到 DOM', 'GPU-accelerated xterm rendering; falls back to DOM when unavailable') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.terminal.webgl !== false }" @click="draft.terminal.webgl = draft.terminal.webgl === false" /></div>
            </div>
          </div>

          <!-- editor -->
          <div v-else-if="active === 'editor'" class="settings-section">
            <h3>{{ t('代码编辑器', 'Code editor') }}</h3>
            <div class="settings-row">
              <div><div class="label">{{ t('字号', 'Font size') }}</div></div>
              <div class="control"><input v-model.number="draft.editor.fontSize" type="number" min="9" max="24" class="input" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('Tab 宽度', 'Tab size') }}</div></div>
              <div class="control"><input v-model.number="draft.editor.tabSize" type="number" min="1" max="8" class="input" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('自动换行', 'Word wrap') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.editor.wordWrap }" @click="draft.editor.wordWrap = !draft.editor.wordWrap" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('自动保存', 'Auto save') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.editor.autoSave }" @click="draft.editor.autoSave = !draft.editor.autoSave" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('字号', 'Font size') }}</div></div>
              <div class="control"><input v-model.number="draft.editor.fontSize" type="number" min="10" max="24" class="input" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('Tab 宽度', 'Tab size') }}</div></div>
              <div class="control"><input v-model.number="draft.editor.tabSize" type="number" min="1" max="8" class="input" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('自动换行', 'Word wrap') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.editor.wordWrap }" @click="draft.editor.wordWrap = !draft.editor.wordWrap" /></div>
            </div>
          </div>

          <!-- AI -->
          <div v-else-if="active === 'ai'" class="settings-section">
            <h3>{{ t('模型供应商', 'Model providers') }}</h3>
            <div class="alert info">
              {{ t('支持 OpenAI 兼容协议与 Anthropic Claude 协议。API Key 会使用本机 AES-256-GCM 加密后写入本地数据库。',
                   'OpenAI-compatible and Anthropic protocols are supported. API keys are encrypted with AES-256-GCM before being stored locally.') }}
            </div>

            <div class="field">
              <label>{{ t('默认对话模型', 'Default model') }}</label>
              <div class="field-row">
                <select :value="draft.ai.activeProviderId" class="select" @change="setActiveProvider($event.target.value)">
                  <option value="">{{ t('未选择供应商', 'No provider') }}</option>
                  <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
                </select>
                <select v-model="draft.ai.activeModel" class="select" :disabled="!activeProvider">
                  <option v-for="m in activeModels" :key="m" :value="m">{{ m }}</option>
                  <option v-if="!activeModels.length" value="">{{ t('请先填写模型', 'Add models first') }}</option>
                </select>
              </div>
            </div>

            <div v-if="providers.length">
              <div v-for="provider in providers" :key="provider.id" class="provider-card">
                <div class="provider-head">
                  <Sparkles :size="15" style="color: var(--accent)" />
                  <input v-model="provider.name" class="input" style="height: 26px; max-width: 190px" />
                  <span v-if="provider.hasKey || provider.apiKey" class="badge green">{{ t('已配置密钥', 'Key set') }}</span>
                  <span class="grow" />
                  <button class="btn sm ghost" :disabled="testingId === provider.id" @click="testProvider(provider)">
                    <Loader2 v-if="testingId === provider.id" :size="12" class="tool-spin" />
                    {{ t('测试', 'Test') }}
                  </button>
                  <button class="icon-btn sm" :title="t('删除', 'Remove')" @click="removeProvider(provider)"><Trash2 :size="13" /></button>
                </div>
                <div class="provider-grid">
                  <div class="field">
                    <label>{{ t('协议', 'Protocol') }}</label>
                    <select v-model="provider.protocol" class="select">
                      <option value="openai">OpenAI Chat Completions</option>
                      <option value="anthropic">Anthropic Messages</option>
                    </select>
                  </div>
                  <div class="field">
                    <label>Base URL</label>
                    <input v-model="provider.baseUrl" class="input" placeholder="https://api.example.com/v1" />
                  </div>
                </div>
                <div class="field">
                  <label>API Key</label>
                  <div class="input-group">
                    <input
                      v-model="provider.apiKey"
                      class="input"
                      :type="revealKey[provider.id] ? 'text' : 'password'"
                      :placeholder="provider.hasKey ? t('已保存，留空不修改', 'Saved — leave blank to keep') : 'sk-…'"
                    />
                    <button class="icon-btn" style="height: 30px" @click="revealKey[provider.id] = !revealKey[provider.id]">
                      <EyeOff v-if="revealKey[provider.id]" :size="14" /><Eye v-else :size="14" />
                    </button>
                  </div>
                </div>
                <div class="field">
                  <div class="row">
                    <label style="flex: 1">{{ t('模型列表（逗号分隔）', 'Models (comma separated)') }}</label>
                    <button class="btn sm ghost" :disabled="loadingModels === provider.id" @click="fetchModels(provider)">
                      <RefreshCw :size="12" :class="{ 'tool-spin': loadingModels === provider.id }" /> {{ t('拉取', 'Fetch') }}
                    </button>
                  </div>
                  <input class="input" :value="modelText(provider)" @change="updateModels(provider, $event.target.value)" placeholder="gpt-4o, gpt-4o-mini" />
                </div>
                <div class="field">
                  <label>{{ t('默认模型', 'Default model') }}</label>
                  <select v-model="provider.defaultModel" class="select">
                    <option v-for="m in provider.models" :key="m" :value="m">{{ m }}</option>
                    <option v-if="!provider.models?.length" value="">{{ t('无', 'None') }}</option>
                  </select>
                </div>
                <div v-if="testResult[provider.id]" class="alert" :class="testResult[provider.id].ok ? 'info' : 'error'" style="font-size: 11.5px">
                  {{ testResult[provider.id].message }}
                </div>
              </div>
            </div>

            <div style="margin-top: 10px">
              <div class="tiny" style="margin-bottom: 6px">{{ t('添加供应商', 'Add provider') }}</div>
              <div style="display: flex; flex-wrap: wrap; gap: 6px">
                <button v-for="preset in PRESETS" :key="preset.name" class="btn sm" @click="addProvider(preset)">
                  <Plus :size="12" /> {{ preset.name }}
                </button>
              </div>
            </div>

            <div class="field" style="margin-top: 12px">
              <label>{{ t('额外系统提示（可选）', 'Extra system prompt (optional)') }}</label>
              <textarea v-model="draft.ai.systemPrompt" class="textarea" rows="4" :placeholder="t('例如：所有 SQL 必须加 LIMIT 100', 'e.g. always add LIMIT 100 to queries')" />
            </div>
          </div>

          <!-- agent -->
          <div v-else-if="active === 'agent'" class="settings-section">
            <h3>{{ t('Agent 执行策略', 'Agent execution policy') }}</h3>
            <div class="alert info">{{ t('默认策略与 HexHub 一致：读取与只读 SQL 可静默执行，写文件 / 命令 / 写 SQL 必须审批；危险命令始终审批。', 'Same defaults as HexHub: reads and read-only SQL run silently; file writes, commands and SQL writes need approval; dangerous commands always require approval.') }}</div>
            <div class="settings-row">
              <div><div class="label">{{ t('静默读取文件', 'Silent file reads') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.agent.allowSilentFileRead }" @click="draft.agent.allowSilentFileRead = !draft.agent.allowSilentFileRead" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('静默写入文件', 'Silent file writes') }}</div><div class="desc">{{ t('不建议开启：Agent 可覆盖任意文件', 'Not recommended: overwrite any file') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.agent.allowSilentFileWrite }" @click="draft.agent.allowSilentFileWrite = !draft.agent.allowSilentFileWrite" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('静默执行命令', 'Silent shell commands') }}</div><div class="desc">{{ t('危险命令仍会强制审批', 'Dangerous commands still require approval') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.agent.allowSilentCommand }" @click="draft.agent.allowSilentCommand = !draft.agent.allowSilentCommand" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('静默执行只读 SQL', 'Silent read-only SQL') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.agent.allowSilentReadonlySql }" @click="draft.agent.allowSilentReadonlySql = !draft.agent.allowSilentReadonlySql" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('禁止本机操作', 'Block local operations') }}</div><div class="desc">{{ t('Agent 只能操作远程资产', 'Only remote assets') }}</div></div>
              <div class="control"><span class="switch" :class="{ on: draft.agent.blockLocal }" @click="draft.agent.blockLocal = !draft.agent.blockLocal" /></div>
            </div>
            <div class="settings-row">
              <div><div class="label">{{ t('最大工具轮次', 'Max tool rounds') }}</div></div>
              <div class="control"><input v-model.number="draft.agent.maxToolRounds" type="number" min="1" max="40" class="input" /></div>
            </div>
          </div>

          <!-- hotkeys -->
          <div v-else-if="active === 'hotkeys'" class="settings-section">
            <h3>{{ t('快捷键', 'Shortcuts') }}</h3>
            <div v-for="item in [
              ['search', t('全局搜索', 'Global search')],
              ['settings', t('设置', 'Settings')],
              ['localTerminal', t('本地终端', 'Local terminal')],
              ['closeTab', t('关闭标签', 'Close tab')],
              ['history', t('历史面板', 'History')],
              ['togglePanel', t('显示/隐藏资产面板', 'Toggle asset panel')],
              ['splitVertical', t('垂直分屏', 'Split vertical')],
              ['splitHorizontal', t('水平分屏', 'Split horizontal')],
              ['moveLeft', t('分屏-聚焦左', 'Pane focus left')],
              ['moveRight', t('分屏-聚焦右', 'Pane focus right')],
              ['moveUp', t('分屏-聚焦上', 'Pane focus up')],
              ['moveDown', t('分屏-聚焦下', 'Pane focus down')],
            ]" :key="item[0]" class="settings-row">
              <div><div class="label">{{ item[1] }}</div></div>
              <div class="control"><input v-model="draft.general.hotkeys[item[0]]" class="input mono" style="width: 160px" placeholder="Ctrl+K" /></div>
            </div>
            <div class="alert info" style="margin-top: 10px">{{ t('格式：Ctrl+Shift+K、Alt+1 等；修改后保存立即生效。', 'Format: Ctrl+Shift+K, Alt+1…; applies after saving.') }}</div>
          </div>

          <!-- screen lock (round 22): configurable; tray stays unavailable -->
          <div v-else-if="active === 'security'" class="settings-section">
            <h3>{{ t('安全', 'Security') }}</h3>

            <div class="settings-row">
              <div>
                <div class="label">
                  {{ t('锁屏', 'Screen lock') }}
                  <span class="badge" :class="state.lock.enabled ? 'green' : ''">
                    {{ state.lock.enabled ? t('已启用', 'Enabled') : t('已关闭', 'Disabled') }}
                  </span>
                </div>
                <div class="desc">
                  {{ t('设置本地口令后启用；锁定状态保存在服务端，刷新页面或重开标签仍保持锁定。',
                       'Enable after setting a local password; the lock state lives on the server, so a refresh or a new tab stays locked.') }}
                </div>
              </div>
              <div class="control">
                <span
                  class="switch lock-enabled"
                  :class="{ on: state.lock.enabled }"
                  role="switch"
                  :aria-checked="state.lock.enabled"
                  @click="toggleLockEnabled"
                />
              </div>
            </div>

            <div class="settings-row">
              <div>
                <div class="label">{{ t('自动锁屏', 'Idle lock') }}</div>
                <div class="desc">
                  {{ t('空闲超过阈值后由服务端强制锁定；0 表示关闭。', 'The server locks after this much idle time; 0 disables it.') }}
                </div>
              </div>
              <div class="control">
                <select
                  class="select lock-idle"
                  :value="String(state.lock.idleMinutes)"
                  :disabled="lockBusy === 'idle'"
                  @change="changeLockIdle($event.target.value)"
                >
                  <option v-for="opt in LOCK_IDLE_OPTIONS" :key="opt.value" :value="String(opt.value)">{{ opt.label }}</option>
                </select>
              </div>
            </div>

            <div class="settings-row">
              <div>
                <div class="label">
                  {{ t('锁屏口令', 'Lock password') }}
                  <span class="badge" :class="state.lock.hasPassword ? 'green' : 'orange'">
                    {{ state.lock.hasPassword ? t('已设置', 'Set') : t('未设置', 'Not set') }}
                  </span>
                </div>
                <div class="desc">
                  {{ t('只保存 PBKDF2-HMAC-SHA256 哈希与随机 salt，不保存也不回传明文；修改需当前口令。',
                       'Only a PBKDF2-HMAC-SHA256 hash with a random salt is stored; the plaintext is never stored or returned. Changing it requires the current password.') }}
                </div>
              </div>
              <div class="control">
                <button class="btn sm lock-now" :disabled="!state.lock.enabled || !state.lock.hasPassword" @click="lockNowFromSettings">
                  {{ t('立即锁屏', 'Lock now') }}
                </button>
              </div>
            </div>

            <div class="lock-password-form">
              <input
                v-if="state.lock.hasPassword"
                v-model="lockOldPassword"
                class="input lock-old-password"
                type="password"
                autocomplete="current-password"
                :placeholder="t('当前口令', 'Current password')"
              />
              <input
                v-model="lockNewPassword"
                class="input lock-new-password"
                type="password"
                autocomplete="new-password"
                :placeholder="t('新口令（至少 4 位）', 'New password (min 4)')"
              />
              <input
                v-model="lockConfirmPassword"
                class="input lock-confirm-password"
                type="password"
                autocomplete="new-password"
                :placeholder="t('确认新口令', 'Confirm new password')"
              />
              <div class="row">
                <button class="btn sm primary lock-save-password" :disabled="!!lockBusy" @click="savePassword">
                  {{ lockBusy === 'password' ? t('保存中…', 'Saving…') : t('保存口令', 'Save password') }}
                </button>
                <button
                  v-if="state.lock.hasPassword"
                  class="btn sm lock-clear-password"
                  :disabled="!!lockBusy"
                  @click="clearPassword"
                >
                  {{ t('清除口令', 'Clear password') }}
                </button>
              </div>
            </div>

            <div v-if="lockError" class="alert error lock-error">{{ lockError }}</div>
            <div v-if="lockNotice" class="alert info lock-notice">{{ lockNotice }}</div>

            <div class="settings-row lock-tray">
              <div>
                <div class="label">
                  {{ t('系统托盘', 'System tray') }}
                  <span class="badge orange">{{ t('不可用', 'Unavailable') }}</span>
                </div>
                <div class="desc">
                  {{ t('浏览器形态没有系统托盘 API：原版 enableTray / setTrayMenu 与托盘菜单无法在页面内实现。',
                       'A browser has no system-tray API: the original enableTray / setTrayMenu and tray menu cannot be implemented in a page.') }}
                </div>
              </div>
              <div class="control">
                <button class="btn sm" disabled :title="t('浏览器形态无系统托盘', 'No system tray in a browser')">
                  {{ t('不可用', 'Unavailable') }}
                </button>
              </div>
            </div>

            <div class="alert info mt12">
              {{ t('锁屏只覆盖当前浏览器界面，防误触、不防对抗：能读取本地数据目录或会话令牌的人仍可绕过。',
                   'The lock only covers this browser UI. It prevents accidental exposure, not a determined local attacker: anyone with the data directory or session token can bypass it.') }}
            </div>
          </div>

          <!-- updates (#91/#93): configurable; 92 hot update stays reserved -->
          <div v-else-if="active === 'update'" class="settings-section">
            <h3>{{ t('更新', 'Updates') }}</h3>

            <div class="settings-row">
              <div>
                <div class="label">
                  {{ t('版本检查', 'Version check') }}
                  <span class="badge green">{{ t('可用', 'Available') }}</span>
                </div>
                <div class="desc">
                  {{ t('读取本机 manifest（file:// 绝对路径或 127.0.0.1 / localhost 的 http(s)），按数值比较 version/build；只读检查，不下载、不访问云端。',
                       'Reads a local manifest (file:// path or loopback http(s)) and compares version/build numerically; read-only, no download, no cloud.') }}
                </div>
              </div>
              <div class="control"><span class="muted mono text-xs update-current">{{ updateCurrent }}</span></div>
            </div>

            <UpdateCheckPanel class="mt8" />

            <div class="settings-row">
              <div>
                <div class="label">{{ t('更新通道', 'Update channel') }}</div>
                <div class="desc">
                  {{ t('通道与发布环境共同决定 manifest 选择：可用 {channel} / {env} 占位符，或以 / 结尾的目录（自动追加 update-<channel>-<env>.json）。',
                       'Channel and release environment select the manifest: use {channel}/{env} placeholders, or a directory ending in / (update-<channel>-<env>.json is appended).') }}
                </div>
              </div>
              <div class="control update-channel-controls">
                <select class="select update-channel" :value="channelDraft" :disabled="!!updateBusy" @change="changeUpdateChannel('channel', $event.target.value)">
                  <option value="stable">{{ t('稳定通道', 'Stable') }}</option>
                  <option value="experimental">{{ t('实验性通道', 'Experimental') }}</option>
                </select>
                <select class="select update-env" :value="envDraft" :disabled="!!updateBusy" @change="changeUpdateChannel('env', $event.target.value)">
                  <option value="production">production</option>
                  <option value="test">test</option>
                </select>
              </div>
            </div>

            <div class="settings-row">
              <div>
                <div class="label">{{ t('自动检查更新', 'Automatic update check') }}</div>
                <div class="desc">{{ t('启动完成（bootstrap）后延迟检查一次，不做后台轮询。', 'One delayed check after bootstrap; no background polling.') }}</div>
              </div>
              <div class="control">
                <span
                  class="switch update-auto"
                  :class="{ on: !!state.settings.update?.autoCheck }"
                  role="switch"
                  :aria-checked="!!state.settings.update?.autoCheck"
                  @click="toggleAutoCheck"
                />
              </div>
            </div>

            <div class="settings-row">
              <div>
                <div class="label">{{ t('Manifest 地址', 'Manifest URL') }}</div>
                <div class="desc">{{ t('仅允许 file:// 绝对路径或本机 http(s)；禁止 api.hexhub.cn 等外部地址。', 'Only file:// absolute paths or loopback http(s); external hosts such as api.hexhub.cn are rejected.') }}</div>
              </div>
              <div class="control update-manifest-controls">
                <input class="input update-manifest" v-model="manifestDraft" :placeholder="t('file:///…/update.json', 'file:///…/update.json')" @change="saveManifestUrl" />
                <button class="btn sm update-manifest-save" :disabled="!!updateBusy" @click="saveManifestUrl">{{ t('保存', 'Save') }}</button>
              </div>
            </div>

            <div class="settings-row update-hot-row">
              <div>
                <div class="label">
                  {{ t('热更新', 'Hot update') }}
                  <span class="badge orange">{{ t('预留，未实现', 'Reserved, not implemented') }}</span>
                </div>
                <div class="desc">{{ hotSummary }}</div>
              </div>
              <div class="control">
                <button class="btn sm update-hot" disabled :title="t('预留（未实现）', 'Reserved (not implemented)')">{{ t('预留', 'Reserved') }}</button>
              </div>
            </div>

            <div v-if="updateError" class="alert error mt12 update-error">{{ updateError }}</div>
            <div v-if="updateNotice" class="alert info mt12 update-notice">{{ updateNotice }}</div>
            <div class="alert info mt12">
              {{ t('版本检查只读取你配置的本机 manifest，不访问 api.hexhub.cn、不下载安装包、不写资产库；热更新（下载→MD5→解压→重启）仍未实现，仍返回 501。',
                   'The version check only reads the local manifest you configure; it never contacts api.hexhub.cn, downloads a package or writes the asset store. Hot update (download → MD5 → extract → restart) remains unimplemented and still answers 501.') }}
            </div>
          </div>

          <!-- reserved capability slots (round 21): extension only -->
          <div v-else-if="isReservedGroup(active)" class="settings-section">
            <h3>{{ sections.find((s) => s.id === active)?.label }}</h3>
            <div v-for="cap in capabilitiesFor(active)" :key="cap.id" class="settings-row">
              <div>
                <div class="label">
                  {{ cap.name }}
                  <span class="badge" :class="cap.status === 'reserved' ? 'orange' : ''">{{ statusLabel(cap) }}</span>
                </div>
                <div class="desc">{{ cap.summary }}</div>
              </div>
              <div class="control">
                <button class="btn sm" :disabled="cap.status !== 'done'" :title="controlTitle(cap)">
                  {{ cap.status === 'reserved' ? t('预留', 'Reserved') : t('不可用', 'Unavailable') }}
                </button>
              </div>
            </div>
            <div v-if="!capabilitiesFor(active).length" class="alert info mt12">
              {{ capabilitiesError || t('能力注册表未加载', 'Capability registry not loaded') }}
            </div>
            <div class="alert info mt12">
              {{ t('这些是预留扩展点：本轮只登记位置与 501 契约，不提供功能。实现状态以 /api/capabilities 为准。',
                   'These are reserved slots: this round only records the location and the 501 contract. Status comes from /api/capabilities.') }}
            </div>
          </div>

          <!-- about -->
          <div v-else class="settings-section">
            <h3>{{ t('关于', 'About') }}</h3>
            <div class="settings-row"><div class="label">{{ t('版本', 'Version') }}</div><div class="muted">{{ state.info?.version || '5.1.9' }} (build {{ state.info?.build || 519 }})</div></div>
            <div class="settings-row">
              <div>
                <div class="label">{{ t('版本检查', 'Version check') }}</div>
                <div class="desc">{{ t('读取本机 manifest 与本机版本比较；只读检查，不下载、不安装。', 'Compares the local manifest with this build; read-only, no download or install.') }}</div>
              </div>
              <div class="control"><span class="muted mono text-xs update-current">{{ updateCurrent }}</span></div>
            </div>
            <UpdateCheckPanel class="mt8" />
            <div class="settings-row"><div class="label">{{ t('运行平台', 'Platform') }}</div><div class="muted">{{ state.info?.os }} {{ state.info?.arch }}</div></div>
            <div class="settings-row"><div class="label">{{ t('主机名', 'Hostname') }}</div><div class="muted">{{ state.info?.hostname }}</div></div>
            <div class="settings-row"><div class="label">{{ t('本地端口', 'Local port') }}</div><div class="muted">{{ state.info?.port }}</div></div>
            <div class="settings-row"><div class="label">{{ t('数据目录', 'Data directory') }}</div><div class="muted mono text-xs">{{ state.info?.dataHome }}</div></div>
            <div class="alert info mt12">
              {{ t('H1KHub 是基于《HexHub 5.1.9 逆向拆解报告》独立实现的本地工作台，与 cn.hexhub 官方无关联。',
                   'H1KHub is an independent local-first workspace based on the HexHub 5.1.9 reverse-engineering report. Not affiliated with cn.hexhub.') }}
            </div>
            <button class="btn mt12" @click="state.aboutOpen = true; state.settingsOpen = false">{{ t('打开关于窗口', 'Open about dialog') }}</button>
          </div>
        </div>
        <div class="modal-foot">
          <div class="left">
            <button class="btn ghost sm" @click="resetDraft()">{{ t('重置未保存更改', 'Reset changes') }}</button>
          </div>
          <button class="btn" @click="state.settingsOpen = false">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" :disabled="saving" @click="save">
            <Check :size="14" /> {{ saving ? t('保存中…', 'Saving…') : t('保存', 'Save') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.update-channel-controls { gap: 8px; }
.update-channel-controls .select { width: 150px; }
.update-manifest-controls { gap: 8px; }
.update-manifest-controls .input { width: 330px; }
.update-manifest-controls .input, .update-channel-controls .select { font-size: 11.5px; }
</style>
