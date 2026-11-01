<script setup>
import { computed, reactive, ref, watch } from 'vue'
import {
  CheckCircle2, ChevronDown, Eye, EyeOff, FileKey, FolderTree, Loader2, Upload, X, XCircle,
} from 'lucide-vue-next'
import AssetIcon from './AssetIcon.vue'
import { api } from '../lib/api'
import { ASSET_TYPE_LIST, DB_TYPES, createAsset, refreshAssets, state, t, toast, updateAsset } from '../lib/store'

const form = reactive({
  typeName: 'SSH',
  name: '',
  alias: '',
  parentId: null,
  color: null,
  config: {},
})
const showSecrets = reactive({})
const testing = ref(false)
const testResult = ref(null)
const saving = ref(false)
const advanced = ref(false)
const keyFileInput = ref(null)
const keyGenType = ref('ed25519')
const generatingKey = ref(false)
const copyTargetId = ref('')
const copyingKey = ref(false)
const savedSecrets = ref(new Set())

const typeInfo = computed(() => ASSET_TYPE_LIST.find((x) => x.name === form.typeName) || ASSET_TYPE_LIST[0])
const isEdit = computed(() => !!state.assetDialog.asset)
const isDir = computed(() => form.typeName === 'Dir')
const sshAssets = computed(() => state.assets.filter((a) => a.typeName === 'SSH'))
const keyAssets = computed(() => state.assets.filter((a) => a.typeName === 'SSHPrivateKey'))
const shellAssets = computed(() => state.assets.filter((a) => ['Shell', 'SSH', 'Telnet'].includes(a.typeName)))
const dockerAssets = computed(() => state.assets.filter((a) => a.typeName === 'Docker'))

const MAX_JUMP_DEPTH = 8
const currentAssetId = computed(() => state.assetDialog.asset?.id || null)

// Follow jumpAssetId from a candidate and report cycles/depth. The target
// itself is a cycle too, so this also prevents A -> B -> A from the editor.
function jumpSelectionState(startId) {
  const byId = new Map(state.assets.map((a) => [a.id, a]))
  const seen = new Set()
  const names = []
  let cur = startId
  let cycle = false
  while (cur) {
    if (cur === currentAssetId.value || seen.has(cur)) {
      cycle = true
      break
    }
    seen.add(cur)
    const asset = byId.get(cur)
    names.push(asset?.name || cur)
    if (names.length > MAX_JUMP_DEPTH) break
    cur = asset?.config?.jumpAssetId || ''
  }
  return { names, cycle, tooDeep: names.length > MAX_JUMP_DEPTH }
}

const jumpOptions = computed(() =>
  sshAssets.value
    .filter((a) => a.id !== currentAssetId.value)
    .map((a) => {
      const info = jumpSelectionState(a.id)
      return { ...a, cycle: info.cycle, label: info.cycle ? `${a.name} ${t('（循环）', '(cycle)')}` : a.name }
    }),
)

const jumpChainInfo = computed(() => {
  if (!form.config.jumpAssetId) return { names: [], cycle: false, tooDeep: false }
  return jumpSelectionState(form.config.jumpAssetId)
})

const jumpChainPreview = computed(() => [...jumpChainInfo.value.names].reverse().join(' → '))

const folderOptions = computed(() => {
  const out = [{ id: null, label: t('根目录', 'Root'), depth: 0 }]
  const walk = (nodes, depth) => {
    for (const node of nodes) {
      if (node.typeName === 'Dir') {
        out.push({ id: node.id, label: node.name, depth })
        walk(node.children || [], depth + 1)
      }
    }
  }
  const map = new Map()
  for (const a of state.assets) map.set(a.id, { ...a, children: [] })
  const roots = []
  for (const node of map.values()) {
    if (node.parentId && map.has(node.parentId)) map.get(node.parentId).children.push(node)
    else roots.push(node)
  }
  walk(roots, 1)
  return out
})

const COLORS = [
  { value: null, label: t('默认', 'Default'), color: 'transparent' },
  { value: '#e05d5d', label: t('红', 'Red'), color: '#e05d5d' },
  { value: '#e0a340', label: t('橙', 'Orange'), color: '#e0a340' },
  { value: '#4ac27a', label: t('绿', 'Green'), color: '#4ac27a' },
  { value: '#4c9aff', label: t('蓝', 'Blue'), color: '#4c9aff' },
  { value: '#b07cff', label: t('紫', 'Purple'), color: '#b07cff' },
  { value: '#8b949e', label: t('灰', 'Gray'), color: '#8b949e' },
]

function defaultsFor(typeName, preset) {
  switch (typeName) {
    case 'SSH':
      return {
        host: '', port: 22, username: 'root', authType: 'password', password: '',
        privateKeyAssetId: '', passphrase: '', termType: 'xterm-256color',
        connectTimeout: 15, keepalive: 30, initCommand: '',
        x11: false, x11Display: '',
        jumpAssetId: '', proxy: { enabled: false, type: 'socks5', host: '', port: 1080, username: '', password: '' },
      }
    case 'FTP':
      return { protocol: 'sftp', host: '', port: 22, username: '', authType: 'password', password: '', privateKeyAssetId: '', passphrase: '' }
    case 'DB':
      return {
        dbType: preset?.dbType || 'mysql', host: '127.0.0.1', port: preset?.dbType === 'postgresql' ? 5432 : 3306,
        username: '', password: '', database: '', file: '', charset: 'utf8mb4', ssl: false, sshAssetId: '',
      }
    case 'Redis':
      return { host: '127.0.0.1', port: 6379, username: '', password: '', db: 0, tls: false, sshAssetId: '', cluster: false, nodes: '' }
    case 'Docker':
      return { mode: 'local', socketPath: '/var/run/docker.sock', host: '', port: 2375, sshAssetId: '' }
    case 'Shell':
      return { shell: '', cwd: '', env: {} }
    case 'Telnet':
      return { host: '', port: 23, termType: 'xterm-256color' }
    case 'VNC':
      return { host: '127.0.0.1', port: 5900, password: '', viewOnly: false }
    case 'Serial':
      return { port: '/dev/ttyUSB0', baudRate: 115200, dataBits: 8, parity: 'N', stopBits: 1 }
    case 'QuickCmd':
      return { assetId: '', command: '' }
    case 'SSHPrivateKey':
      return { privateKey: '', passphrase: '' }
    case 'SSHTunnel':
      return { type: 'local', sshAssetId: '', bindHost: '127.0.0.1', bindPort: 0, targetHost: '127.0.0.1', targetPort: 0 }
    case 'SSHPasswordPair':
      return { username: '', password: '' }
    default:
      return {}
  }
}

function initFromDialog() {
  const d = state.assetDialog
  form.typeName = d.typeName || 'SSH'
  advanced.value = false
  testResult.value = null
  showSecrets && Object.keys(showSecrets).forEach((k) => delete showSecrets[k])
  if (d.asset) {
    form.name = d.asset.name
    form.alias = d.asset.alias || ''
    form.parentId = d.asset.parentId
    form.color = d.asset.color
    form.config = { ...defaultsFor(form.typeName, d.preset), ...JSON.parse(JSON.stringify(d.asset.config || {})) }
    savedSecrets.value = new Set(d.asset.secrets || [])
  } else {
    form.name = ''
    form.alias = ''
    form.parentId = d.parentId ?? null
    form.color = null
    form.config = defaultsFor(form.typeName, d.preset)
    savedSecrets.value = new Set()
  }
}

watch(() => state.assetDialog.open, (open) => { if (open) initFromDialog() }, { immediate: true })
watch(() => form.config.dbType, (val) => {
  if (form.typeName !== 'DB' || isEdit.value) return
  if (val === 'postgresql') form.config.port = 5432
  else if (val === 'clickhouse') form.config.port = 9000
  else if (val === 'mongodb') form.config.port = 27017
  else if (val === 'sqlserver') form.config.port = 1433
  else if (val === 'oracle') form.config.port = 1521
  else if (val === 'dameng') form.config.port = 5236
  else if (val === 'sqlite' || val === 'duckdb') form.config.port = 0
  else form.config.port = 3306
  if (val === 'sqlite' || val === 'duckdb') form.config.host = ''
  else if (!form.config.host) form.config.host = '127.0.0.1'
})

const secretPlaceholder = (path) => (savedSecrets.value.has(path) ? t('已加密保存，留空保持不变', 'Saved & encrypted — leave blank to keep') : '')

function close() {
  state.assetDialog.open = false
}

const testable = computed(() => ['SSH', 'FTP', 'DB', 'Redis', 'Docker', 'Shell', 'Telnet', 'Serial', 'VNC'].includes(form.typeName))

function cleanConfig() {
  const cfg = JSON.parse(JSON.stringify(form.config || {}))
  return cfg
}

async function testConnection() {
  testing.value = true
  testResult.value = null
  try {
    const res = await api.post('/api/test-connection', {
      assetId: isEdit.value ? state.assetDialog.asset.id : null,
      typeName: form.typeName,
      config: cleanConfig(),
    })
    testResult.value = { ok: true, message: res.message || t('连接成功', 'Connected') }
  } catch (err) {
    testResult.value = { ok: false, message: String(err.message || err) }
  } finally {
    testing.value = false
  }
}

async function generateKey() {
  generatingKey.value = true
  try {
    const res = await api.post('/api/keys/generate', {
      type: keyGenType.value,
      comment: form.name || 'h1khub',
      saveAsset: false,
    })
    form.config.privateKey = res.privateKey
    form.config.publicKey = res.publicKey
    form.config.keyType = res.type
    form.config.fingerprint = res.fingerprint
    toast(t('密钥已生成，保存资产后即可使用', 'Key generated; save the asset to use it'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    generatingKey.value = false
  }
}

async function copyPublicKey() {
  const publicKey = form.config.publicKey || ''
  if (!publicKey) {
    toast(t('当前资产没有公钥，请先生成或粘贴', 'No public key on this asset'), 'info')
    return
  }
  if (!copyTargetId.value) {
    toast(t('请选择目标 SSH 主机', 'Choose a target SSH host'), 'info')
    return
  }
  copyingKey.value = true
  try {
    const res = await api.post('/api/ssh/copy-id', { assetId: copyTargetId.value, publicKey })
    toast(res.added ? t('公钥已追加到目标主机', 'Public key added') : t('公钥已存在', 'Public key already present'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    copyingKey.value = false
  }
}

async function loadKeyFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  form.config.privateKey = await file.text()
  form.config.privateKeyAssetId = ''
  toast(t('私钥已载入', 'Private key loaded'), 'success')
}

async function save() {
  if (!form.name.trim()) {
    toast(t('请填写名称', 'Name is required'), 'error')
    return
  }
  if (form.typeName === 'SSH' && form.config.authType === 'password' && !form.config.password && !savedSecrets.value.has('password')) {
    toast(t('请填写登录密码', 'Password is required'), 'error')
    return
  }
  if (form.typeName === 'SSH' && form.config.authType === 'key' && !form.config.privateKey && !form.config.privateKeyAssetId && !savedSecrets.value.has('privateKey')) {
    toast(t('请选择私钥或填写私钥内容', 'Choose or paste a private key'), 'error')
    return
  }
  saving.value = true
  const config = cleanConfig()
  // remove empty secrets so the backend keeps the stored value
  for (const key of ['password', 'privateKey', 'passphrase']) {
    if (config[key] === '' || config[key] === undefined) delete config[key]
  }
  if (config.proxy) {
    for (const key of ['password']) if (!config.proxy[key]) delete config.proxy[key]
  }
  try {
    if (isEdit.value) {
      await updateAsset(state.assetDialog.asset.id, {
        name: form.name.trim(),
        alias: form.alias.trim(),
        parentId: form.parentId,
        color: form.color,
        config,
      })
      toast(t('资产已更新', 'Asset updated'), 'success')
    } else {
      const asset = await createAsset({
        typeName: form.typeName,
        name: form.name.trim(),
        alias: form.alias.trim(),
        parentId: form.parentId,
        color: form.color,
        config,
      })
      if (form.parentId) state.expanded[form.parentId] = true
      state.selectedAssetId = asset.id
      toast(t('资产已创建', 'Asset created'), 'success')
    }
    close()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    saving.value = false
  }
}

function browseKey() {
  keyFileInput.value?.click()
}
</script>

<template>
  <div v-if="state.assetDialog.open" class="modal-mask" @mousedown.self="close">
    <div class="modal" style="width: 640px">
      <div class="modal-head">
        <AssetIcon :type-name="form.typeName" :size="17" :color="typeInfo.color" />
        <span class="modal-title">
          {{ isEdit ? t('编辑', 'Edit') : t('新建', 'New') }}{{ typeInfo.label }}
        </span>
        <span class="badge">{{ typeInfo.name }}</span>
        <button class="icon-btn sm" @click="close"><X :size="14" /></button>
      </div>

      <div class="modal-body">
        <div class="field-row">
          <div class="field" style="flex: 1.4">
            <label>{{ t('名称', 'Name') }}</label>
            <input v-model="form.name" class="input" :placeholder="typeInfo.label" @keyup.enter="save" />
          </div>
          <div class="field" style="flex: 1">
            <label>{{ t('所属目录', 'Folder') }}</label>
            <select v-model="form.parentId" class="select">
              <option v-for="opt in folderOptions" :key="String(opt.id)" :value="opt.id">
                {{ '　'.repeat(opt.depth) + opt.label }}
              </option>
            </select>
          </div>
        </div>

        <div class="field-row" style="margin-top: 10px">
          <div class="field" style="flex: 1">
            <label>{{ t('显示别名（可选）', 'Alias (optional)') }}</label>
            <input v-model="form.alias" class="input" :placeholder="t('树中显示的名称，留空使用名称', 'Shown in tree; empty uses name')" />
          </div>
        </div>

        <div class="field" style="margin-top: 12px">
          <label>{{ t('颜色标记', 'Color') }}</label>
          <div class="theme-swatches">
            <button
              v-for="c in COLORS"
              :key="String(c.value)"
              class="swatch"
              :class="{ active: form.color === c.value }"
              :style="{ background: c.value || 'var(--bg-elev)', borderColor: c.value ? c.value : 'var(--border-strong)' }"
              :title="c.label"
              @click="form.color = c.value"
            />
          </div>
        </div>

        <div class="divider" />

        <!-- SSH -->
        <template v-if="form.typeName === 'SSH'">
          <div class="field-row">
            <div class="field" style="flex: 2.4">
              <label>{{ t('主机地址', 'Host') }}</label>
              <input v-model="form.config.host" class="input" placeholder="127.0.0.1 或 example.com" />
            </div>
            <div class="field" style="flex: 0.8">
              <label>{{ t('端口', 'Port') }}</label>
              <input v-model.number="form.config.port" type="number" class="input" />
            </div>
          </div>
          <div class="field-row" style="margin-top: 10px">
            <div class="field">
              <label>{{ t('用户名', 'Username') }}</label>
              <input v-model="form.config.username" class="input" />
            </div>
            <div class="field">
              <label>{{ t('认证方式', 'Authentication') }}</label>
              <select v-model="form.config.authType" class="select">
                <option value="password">{{ t('密码', 'Password') }}</option>
                <option value="key">{{ t('私钥', 'Private key') }}</option>
                <option value="agent">{{ t('SSH Agent', 'SSH Agent') }}</option>
              </select>
            </div>
          </div>

          <div v-if="form.config.authType === 'password'" class="field" style="margin-top: 10px">
            <label>{{ t('密码', 'Password') }}</label>
            <div class="row">
              <input v-model="form.config.password" class="input" :type="showSecrets.password ? 'text' : 'password'" :placeholder="secretPlaceholder('password')" />
              <button class="icon-btn" @click="showSecrets.password = !showSecrets.password">
                <EyeOff v-if="showSecrets.password" :size="14" /><Eye v-else :size="14" />
              </button>
            </div>
          </div>

          <template v-else-if="form.config.authType === 'key'">
            <div class="field" style="margin-top: 10px">
              <label>{{ t('私钥资产（可选）', 'Key asset (optional)') }}</label>
              <select v-model="form.config.privateKeyAssetId" class="select">
                <option value="">{{ t('不引用资产，直接填写私钥', 'None — paste a key below') }}</option>
                <option v-for="a in keyAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
              </select>
            </div>
            <div v-if="!form.config.privateKeyAssetId" class="field" style="margin-top: 10px">
              <div class="row">
                <label style="flex: 1">{{ t('私钥内容', 'Private key') }}</label>
                <button class="btn sm ghost" @click="browseKey"><Upload :size="12" /> {{ t('从文件载入', 'Load file') }}</button>
              </div>
              <textarea v-model="form.config.privateKey" class="textarea" rows="5" :placeholder="'-----BEGIN OPENSSH PRIVATE KEY-----' + (savedSecrets.has('privateKey') ? '\n' + t('已加密保存，留空保持不变', 'Saved — leave blank to keep') : '')" />
              <input ref="keyFileInput" type="file" class="hidden" @change="loadKeyFile" />
            </div>
            <div class="field" style="margin-top: 10px">
              <label>{{ t('私钥口令（可选）', 'Key passphrase (optional)') }}</label>
              <input v-model="form.config.passphrase" class="input" type="password" :placeholder="secretPlaceholder('passphrase')" />
            </div>
          </template>

          <div class="field-row" style="margin-top: 10px">
            <div class="field">
              <label>{{ t('跳板机（可选）', 'Jump host (optional)') }}</label>
              <select v-model="form.config.jumpAssetId" class="select">
                <option value="">{{ t('不使用跳板机', 'None') }}</option>
                <option v-for="a in jumpOptions" :key="a.id" :value="a.id" :disabled="a.cycle">{{ a.label }}</option>
              </select>
            </div>
            <div class="field">
              <label>{{ t('终端类型', 'Terminal type') }}</label>
              <select v-model="form.config.termType" class="select">
                <option value="xterm-256color">xterm-256color</option>
                <option value="xterm">xterm</option>
                <option value="screen-256color">screen-256color</option>
                <option value="vt100">vt100</option>
              </select>
            </div>
          </div>

          <div v-if="form.config.jumpAssetId" class="alert" :class="jumpChainInfo.cycle || jumpChainInfo.tooDeep ? 'error' : 'info'" style="margin-top: 10px">
            <template v-if="jumpChainInfo.cycle">
              {{ t('跳板链存在循环，无法连接：', 'Jump chain contains a cycle and cannot connect: ') }}{{ jumpChainPreview }}
            </template>
            <template v-else-if="jumpChainInfo.tooDeep">
              {{ t(`跳板层数超过上限（${MAX_JUMP_DEPTH}）`, `Jump chain exceeds the limit of ${MAX_JUMP_DEPTH}`) }}
            </template>
            <template v-else>
              {{ t('跳板链（由外到内）：', 'Jump chain (outermost first): ') }}{{ jumpChainPreview }}
            </template>
          </div>

          <div class="field" style="margin-top: 10px">
            <label>{{ t('登录后执行（可选）', 'Command after login (optional)') }}</label>
            <input v-model="form.config.initCommand" class="input" placeholder="cd /opt && bash -l" />
          </div>

          <button class="btn ghost sm" style="margin-top: 12px" @click="advanced = !advanced">
            <ChevronDown :size="13" :style="{ transform: advanced ? 'rotate(180deg)' : 'none' }" />
            {{ t('高级：代理与超时', 'Advanced: proxy & timeouts') }}
          </button>
          <template v-if="advanced">
            <div class="field-row" style="margin-top: 10px">
              <div class="field">
                <label>{{ t('连接超时（秒）', 'Connect timeout (s)') }}</label>
                <input v-model.number="form.config.connectTimeout" type="number" class="input" />
              </div>
              <div class="field">
                <label>{{ t('保活间隔（秒）', 'Keepalive (s)') }}</label>
                <input v-model.number="form.config.keepalive" type="number" class="input" />
              </div>
              <div class="field" style="justify-content: flex-end">
                <label class="row" style="cursor: pointer">
                  <span class="switch" :class="{ on: form.config.proxy.enabled }" @click="form.config.proxy.enabled = !form.config.proxy.enabled" />
                  <span>{{ t('使用代理', 'Use proxy') }}</span>
                </label>
              </div>
            </div>
            <div class="field-row" style="margin-top: 10px">
              <div class="field">
                <label>{{ t('X11 转发', 'X11 forwarding') }}</label>
                <label class="row" style="cursor: pointer; gap: 8px">
                  <span class="switch" :class="{ on: form.config.x11 }" @click="form.config.x11 = !form.config.x11" />
                  <span class="tiny">{{ t('为远程图形程序转发本地 X 显示', 'Forward the local X display to remote GUI apps') }}</span>
                </label>
              </div>
              <div class="field" v-if="form.config.x11">
                <label>{{ t('本地显示（可选）', 'Local display (optional)') }}</label>
                <input v-model="form.config.x11Display" class="input" placeholder=":1" />
              </div>
            </div>
            <div v-if="form.config.proxy.enabled" class="field-row" style="margin-top: 10px">
              <div class="field" style="flex: 1.2">
                <label>{{ t('代理类型', 'Proxy type') }}</label>
                <select v-model="form.config.proxy.type" class="select">
                  <option value="socks5">SOCKS5</option>
                  <option value="http">HTTP CONNECT</option>
                </select>
              </div>
              <div class="field" style="flex: 1.4">
                <label>{{ t('代理主机', 'Proxy host') }}</label>
                <input v-model="form.config.proxy.host" class="input" placeholder="127.0.0.1" />
              </div>
              <div class="field" style="flex: 0.7">
                <label>{{ t('端口', 'Port') }}</label>
                <input v-model.number="form.config.proxy.port" type="number" class="input" />
              </div>
            </div>
            <div v-if="form.config.proxy.enabled" class="field-row" style="margin-top: 10px">
              <div class="field"><label>{{ t('代理用户名', 'Proxy username') }}</label><input v-model="form.config.proxy.username" class="input" /></div>
              <div class="field"><label>{{ t('代理密码', 'Proxy password') }}</label><input v-model="form.config.proxy.password" type="password" class="input" :placeholder="secretPlaceholder('proxy.password')" /></div>
            </div>
          </template>
        </template>

        <!-- SFTP -->
        <template v-else-if="form.typeName === 'FTP'">
          <div class="field-row">
            <div class="field" style="flex: 2.4">
              <label>{{ t('主机地址', 'Host') }}</label>
              <input v-model="form.config.host" class="input" />
            </div>
            <div class="field" style="flex: 0.8"><label>{{ t('端口', 'Port') }}</label><input v-model.number="form.config.port" type="number" class="input" /></div>
          </div>
          <div class="field-row" style="margin-top: 10px">
            <div class="field"><label>{{ t('用户名', 'Username') }}</label><input v-model="form.config.username" class="input" /></div>
            <div class="field"><label>{{ t('认证方式', 'Authentication') }}</label>
              <select v-model="form.config.authType" class="select">
                <option value="password">{{ t('密码', 'Password') }}</option>
                <option value="key">{{ t('私钥', 'Private key') }}</option>
              </select>
            </div>
          </div>
          <div v-if="form.config.authType === 'password'" class="field" style="margin-top: 10px">
            <label>{{ t('密码', 'Password') }}</label>
            <input v-model="form.config.password" type="password" class="input" :placeholder="secretPlaceholder('password')" />
          </div>
          <template v-else>
            <div class="field" style="margin-top: 10px">
              <label>{{ t('私钥资产', 'Key asset') }}</label>
              <select v-model="form.config.privateKeyAssetId" class="select">
                <option value="">{{ t('直接填写', 'Paste below') }}</option>
                <option v-for="a in keyAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
              </select>
            </div>
            <div v-if="!form.config.privateKeyAssetId" class="field" style="margin-top: 10px">
              <label>{{ t('私钥内容', 'Private key') }}</label>
              <textarea v-model="form.config.privateKey" class="textarea" rows="4" :placeholder="secretPlaceholder('privateKey')" />
            </div>
          </template>
          <div class="alert info mt12">{{ t('H1KHub 同时支持 SFTP（基于 SSH）与明文 FTP；保存后可在文件视图中直接浏览、上传、下载与在线编辑。', 'This replica supports both SFTP (over SSH) and plain FTP; after saving, browse, upload, download and edit files directly in the file view.') }}</div>
        </template>

        <!-- DB -->
        <template v-else-if="form.typeName === 'DB'">
          <div class="field-row">
            <div class="field">
              <label>{{ t('数据库类型', 'Database type') }}</label>
              <select v-model="form.config.dbType" class="select" :disabled="isEdit">
                <option v-for="d in DB_TYPES" :key="d.id" :value="d.id">{{ d.label }}</option>
              </select>
            </div>
            <div class="field" v-if="!['sqlite', 'duckdb'].includes(form.config.dbType)">
              <label>{{ form.config.dbType === 'oracle' ? t('服务名（Service Name）', 'Service name') : t('数据库名（可选）', 'Database (optional)') }}</label>
              <input v-model="form.config.database" class="input" :placeholder="form.config.dbType === 'oracle' ? 'XEPDB1' : form.config.dbType === 'sqlserver' ? 'master' : form.config.dbType === 'dameng' ? 'DAMENG' : 'demo'" />
            </div>
          </div>
          <template v-if="['sqlite', 'duckdb'].includes(form.config.dbType)">
            <div class="field" style="margin-top: 10px">
              <label>{{ form.config.dbType === 'duckdb' ? t('DuckDB 数据库文件', 'DuckDB database file') : t('SQLite 文件路径', 'SQLite file path') }}</label>
              <input v-model="form.config.file" class="input" :placeholder="form.config.dbType === 'duckdb' ? '/home/user/analytics.duckdb' : '/home/user/data.db'" />
            </div>
          </template>
          <template v-else>
            <div class="field-row" style="margin-top: 10px">
              <div class="field" style="flex: 2.4"><label>{{ t('主机地址', 'Host') }}</label><input v-model="form.config.host" class="input" /></div>
              <div class="field" style="flex: 0.8"><label>{{ t('端口', 'Port') }}</label><input v-model.number="form.config.port" type="number" class="input" /></div>
            </div>
            <div class="field-row" style="margin-top: 10px">
              <div class="field"><label>{{ t('用户名', 'Username') }}</label><input v-model="form.config.username" class="input" /></div>
              <div class="field"><label>{{ t('密码', 'Password') }}</label><input v-model="form.config.password" type="password" class="input" :placeholder="secretPlaceholder('password')" /></div>
            </div>
            <div class="field-row" style="margin-top: 10px">
              <div class="field">
                <label>{{ t('字符集', 'Charset') }}</label>
                <select v-model="form.config.charset" class="select">
                  <option value="utf8mb4">utf8mb4</option>
                  <option value="utf8">utf8</option>
                  <option value="latin1">latin1</option>
                </select>
              </div>
              <div class="field" style="justify-content: flex-end">
                <label class="row" style="cursor: pointer">
                  <span class="switch" :class="{ on: form.config.ssl }" @click="form.config.ssl = !form.config.ssl" />
                  <span>{{ t('启用 TLS/SSL', 'Enable TLS/SSL') }}</span>
                </label>
              </div>
            </div>
            <div class="field" style="margin-top: 10px">
              <label>{{ t('通过 SSH 隧道连接（可选）', 'Connect via SSH tunnel (optional)') }}</label>
              <select v-model="form.config.sshAssetId" class="select">
                <option value="">{{ t('不使用隧道', 'None') }}</option>
                <option v-for="a in sshAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
              </select>
              <div class="hint">{{ t('数据库像 H1KHub 一样可复用资产树中的 SSH 连接作为隧道。', 'Reuse an SSH asset from the tree as a tunnel, just like H1KHub.') }}</div>
            </div>
          </template>
        </template>

        <!-- Redis -->
        <template v-else-if="form.typeName === 'Redis'">
          <div class="field">
            <label>{{ t('集群模式', 'Cluster mode') }}</label>
            <div class="row" style="gap: 8px; align-items: center">
              <span class="switch" :class="{ on: form.config.cluster }" @click="form.config.cluster = !form.config.cluster" />
              <span class="tiny">{{ t('启用后按集群连接，仅支持 db0', 'Use cluster client; db0 only') }}</span>
            </div>
          </div>
          <div v-if="form.config.cluster" class="field" style="margin-top: 10px">
            <label>{{ t('节点列表', 'Nodes') }}</label>
            <input v-model="form.config.nodes" class="input mono" placeholder="127.0.0.1:7001,127.0.0.1:7002,127.0.0.1:7003" />
          </div>
          <div class="field-row">
            <div class="field" style="flex: 2.4"><label>{{ t('主机地址', 'Host') }}</label><input v-model="form.config.host" class="input" /></div>
            <div class="field" style="flex: 0.8"><label>{{ t('端口', 'Port') }}</label><input v-model.number="form.config.port" type="number" class="input" /></div>
          </div>
          <div class="field-row" style="margin-top: 10px">
            <div class="field"><label>{{ t('密码（可选）', 'Password (optional)') }}</label><input v-model="form.config.password" type="password" class="input" :placeholder="secretPlaceholder('password')" /></div>
            <div class="field"><label>{{ t('默认 DB', 'Default DB') }}</label><input v-model.number="form.config.db" type="number" class="input" /></div>
          </div>
          <div class="field-row" style="margin-top: 10px">
            <div class="field">
              <label>{{ t('通过 SSH 隧道', 'SSH tunnel') }}</label>
              <select v-model="form.config.sshAssetId" class="select">
                <option value="">{{ t('不使用', 'None') }}</option>
                <option v-for="a in sshAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
              </select>
            </div>
            <div class="field" style="justify-content: flex-end">
              <label class="row" style="cursor: pointer">
                <span class="switch" :class="{ on: form.config.tls }" @click="form.config.tls = !form.config.tls" />
                <span>{{ t('启用 TLS', 'Enable TLS') }}</span>
              </label>
            </div>
          </div>
        </template>

        <!-- Docker -->
        <template v-else-if="form.typeName === 'Docker'">
          <div class="field">
            <label>{{ t('连接方式', 'Connection mode') }}</label>
            <div class="seg">
              <button :class="{ active: form.config.mode === 'local' }" @click="form.config.mode = 'local'">{{ t('本地 Socket', 'Local socket') }}</button>
              <button :class="{ active: form.config.mode === 'ssh' }" @click="form.config.mode = 'ssh'">{{ t('SSH 远程', 'Remote over SSH') }}</button>
            </div>
          </div>
          <div v-if="form.config.mode === 'local'" class="field" style="margin-top: 12px">
            <label>{{ t('Docker Socket 路径', 'Docker socket path') }}</label>
            <input v-model="form.config.socketPath" class="input" placeholder="/var/run/docker.sock" />
            <div class="hint">{{ t('需要当前用户对 socket 有读写权限（docker 组或 ACL）。', 'Your user needs read/write access to the socket (docker group or ACL).') }}</div>
          </div>
          <div v-else class="field" style="margin-top: 12px">
            <label>{{ t('SSH 资产', 'SSH asset') }}</label>
            <select v-model="form.config.sshAssetId" class="select">
              <option value="">{{ t('请选择 SSH 资产', 'Choose an SSH asset') }}</option>
              <option v-for="a in sshAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
            </select>
            <div class="hint">{{ t('远程主机需已安装 docker CLI。', 'The remote host needs the docker CLI installed.') }}</div>
          </div>
        </template>

        <!-- Shell -->
        <template v-else-if="form.typeName === 'Shell'">
          <div class="field">
            <label>{{ t('Shell 路径', 'Shell path') }}</label>
            <input v-model="form.config.shell" class="input" :placeholder="t('留空使用 $SHELL（推荐 /bin/bash）', 'Empty uses $SHELL (recommended /bin/bash)')" />
          </div>
          <div class="field" style="margin-top: 10px">
            <label>{{ t('工作目录', 'Working directory') }}</label>
            <input v-model="form.config.cwd" class="input" placeholder="~" />
          </div>
          <div class="field" style="margin-top: 10px">
            <label>{{ t('环境变量（KEY=VALUE，每行一个）', 'Environment variables (KEY=VALUE per line)') }}</label>
            <textarea class="textarea" rows="3" :value="Object.entries(form.config.env || {}).map(([k, v]) => `${k}=${v}`).join('\n')" @input="(e) => {
              const env = {}
              for (const line of e.target.value.split('\n')) {
                const i = line.indexOf('=')
                if (i > 0) env[line.slice(0, i).trim()] = line.slice(i + 1)
              }
              form.config.env = env
            }" placeholder="LANG=C.UTF-8" />
          </div>
        </template>

        <!-- Telnet -->
        <template v-else-if="form.typeName === 'VNC'">
          <div class="field-row">
            <div class="field" style="flex: 2"><label>{{ t('主机', 'Host') }}</label><input v-model="form.config.host" class="input mono" placeholder="127.0.0.1" /></div>
            <div class="field"><label>{{ t('端口', 'Port') }}</label><input v-model.number="form.config.port" type="number" class="input" /></div>
          </div>
          <div class="field" style="margin-top: 10px"><label>{{ t('密码（可选）', 'Password (optional)') }}</label><input v-model="form.config.password" type="password" class="input" /></div>
          <div class="alert info" style="margin-top: 12px">{{ t('仅管理连接信息并检测端口；不内置 RFB 画面客户端。', 'Like the original, manages connection info and checks the port; no built-in RFB viewer.') }}</div>
        </template>

        <template v-else-if="form.typeName === 'Serial'">
          <div class="field">
            <label>{{ t('串口设备', 'Serial device') }}</label>
            <input v-model="form.config.port" class="input mono" placeholder="/dev/ttyUSB0" />
          </div>
          <div class="field-row" style="margin-top: 10px">
            <div class="field"><label>{{ t('波特率', 'Baud rate') }}</label>
              <select v-model.number="form.config.baudRate" class="select">
                <option v-for="b in [9600,19200,38400,57600,115200,230400]" :key="b" :value="b">{{ b }}</option>
              </select>
            </div>
            <div class="field"><label>{{ t('数据位', 'Data bits') }}</label>
              <select v-model.number="form.config.dataBits" class="select"><option :value="8">8</option><option :value="7">7</option></select>
            </div>
            <div class="field"><label>{{ t('校验', 'Parity') }}</label>
              <select v-model="form.config.parity" class="select"><option value="N">None</option><option value="E">Even</option><option value="O">Odd</option></select>
            </div>
            <div class="field"><label>{{ t('停止位', 'Stop bits') }}</label>
              <select v-model.number="form.config.stopBits" class="select"><option :value="1">1</option><option :value="1.5">1.5</option><option :value="2">2</option></select>
            </div>
          </div>
          <div class="alert info" style="margin-top: 12px">
            {{ t('本机无串口设备时可用 socat 创建 pty 对进行测试。', 'Without hardware, use socat to create a PTY pair for testing.') }}
          </div>
        </template>

        <template v-else-if="form.typeName === 'Telnet'">
          <div class="field-row">
            <div class="field" style="flex: 2.4"><label>{{ t('主机地址', 'Host') }}</label><input v-model="form.config.host" class="input" /></div>
            <div class="field" style="flex: 0.8"><label>{{ t('端口', 'Port') }}</label><input v-model.number="form.config.port" type="number" class="input" /></div>
          </div>
          <div class="alert info mt12">{{ t('Telnet 为明文协议，仅建议在隔离实验环境中使用。', 'Telnet is plaintext; use it only in isolated labs.') }}</div>
        </template>

        <!-- Quick command -->
        <template v-else-if="form.typeName === 'QuickCmd'">
          <div class="field">
            <label>{{ t('目标连接', 'Target connection') }}</label>
            <select v-model="form.config.assetId" class="select">
              <option value="">{{ t('使用当前终端', 'Current terminal') }}</option>
              <option v-for="a in shellAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
            </select>
          </div>
          <div class="field" style="margin-top: 10px">
            <label>{{ t('命令', 'Command') }}</label>
            <textarea v-model="form.config.command" class="textarea" rows="3" placeholder="df -h && free -m" />
          </div>
        </template>

        <!-- Private key -->
        <template v-else-if="form.typeName === 'SSHPrivateKey'">
          <div class="field">
            <div class="row" style="gap: 6px">
              <label style="flex: 1">{{ t('私钥内容', 'Private key') }}</label>
              <select v-model="keyGenType" class="select" style="width: 110px; height: 26px">
                <option value="ed25519">ed25519</option><option value="rsa">RSA 3072</option>
              </select>
              <button class="btn sm" :disabled="generatingKey" @click="generateKey">
                <Loader2 v-if="generatingKey" :size="12" class="tool-spin" /><FileKey v-else :size="12" /> {{ t('生成密钥', 'Generate') }}
              </button>
              <button class="btn sm ghost" @click="browseKey"><Upload :size="12" /> {{ t('从文件载入', 'Load file') }}</button>
            </div>
            <textarea v-model="form.config.privateKey" class="textarea" rows="8" :placeholder="secretPlaceholder('privateKey') || '-----BEGIN OPENSSH PRIVATE KEY-----'" />
            <input ref="keyFileInput" type="file" class="hidden" @change="loadKeyFile" />
          </div>
          <div class="field" style="margin-top: 10px">
            <label>{{ t('口令（可选）', 'Passphrase (optional)') }}</label>
            <input v-model="form.config.passphrase" type="password" class="input" :placeholder="secretPlaceholder('passphrase')" />
          </div>
          <div v-if="form.config.publicKey" class="field" style="margin-top: 10px">
            <label>{{ t('公钥', 'Public key') }}</label>
            <textarea v-model="form.config.publicKey" class="textarea mono" rows="2" style="font-size: 11px" />
            <div class="row" style="gap: 6px; margin-top: 6px">
              <select v-model="copyTargetId" class="select" style="flex: 1">
                <option value="">{{ t('选择目标 SSH 主机', 'Choose target SSH host') }}</option>
                <option v-for="a in sshAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
              </select>
              <button class="btn sm" :disabled="copyingKey || !copyTargetId" @click="copyPublicKey">
                <Loader2 v-if="copyingKey" :size="12" class="tool-spin" /><Upload v-else :size="12" /> {{ t('复制公钥到主机', 'ssh-copy-id') }}
              </button>
            </div>
          </div>
        </template>

        <!-- SSH tunnel -->
        <template v-else-if="form.typeName === 'SSHTunnel'">
          <div class="field">
            <label>{{ t('SSH 连接', 'SSH connection') }}</label>
            <select v-model="form.config.sshAssetId" class="select">
              <option value="">{{ t('请选择', 'Choose') }}</option>
              <option v-for="a in sshAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
            </select>
          </div>
          <div class="field-row" style="margin-top: 10px">
            <div class="field">
              <label>{{ t('隧道类型', 'Tunnel type') }}</label>
              <select v-model="form.config.type" class="select">
                <option value="local">{{ t('本地转发 (local -L)', 'Local forward (-L)') }}</option>
                <option value="remote">{{ t('远程转发 (remote -R)', 'Remote forward (-R)') }}</option>
                <option value="dynamic">{{ t('动态 SOCKS5 (dynamic -D)', 'Dynamic SOCKS5 (-D)') }}</option>
              </select>
            </div>
          </div>
          <div class="field-row" style="margin-top: 10px">
            <div class="field">
              <label>{{ form.config.type === 'remote' ? t('远端监听地址', 'Remote bind host') : t('本地监听地址', 'Local bind host') }}</label>
              <input v-model="form.config.bindHost" class="input" placeholder="127.0.0.1" />
            </div>
            <div class="field">
              <label>{{ form.config.type === 'remote' ? t('远端监听端口', 'Remote bind port') : t('本地监听端口', 'Local bind port') }}</label>
              <input v-model.number="form.config.bindPort" type="number" class="input" placeholder="0 = 自动" />
            </div>
          </div>
          <div v-if="form.config.type !== 'dynamic'" class="field-row" style="margin-top: 10px">
            <div class="field">
              <label>{{ form.config.type === 'remote' ? t('本地目标主机', 'Local target host') : t('目标主机（SSH 服务器可达）', 'Target host (reachable from server)') }}</label>
              <input v-model="form.config.targetHost" class="input" placeholder="127.0.0.1" />
            </div>
            <div class="field">
              <label>{{ form.config.type === 'remote' ? t('本地目标端口', 'Local target port') : t('目标端口', 'Target port') }}</label>
              <input v-model.number="form.config.targetPort" type="number" class="input" placeholder="80" />
            </div>
          </div>
          <div v-else class="alert info" style="margin-top: 10px">
            {{ t('动态模式在本地监听一个 SOCKS5 代理，浏览器/工具可指向该地址；端口 0 表示自动分配。', 'Dynamic mode exposes a local SOCKS5 proxy; port 0 allocates one automatically.') }}
          </div>
          <div class="tiny" style="margin-top: 6px">
            {{ t('启动后可在「隧道」标签页查看监听地址与连接数。', 'Start it to see the listening address and connection count on the Tunnel tab.') }}
          </div>
        </template>

        <!-- Shared password pair -->
        <template v-else-if="form.typeName === 'SSHPasswordPair'">
          <div class="field">
            <label>{{ t('用户名', 'Username') }}</label>
            <input v-model="form.config.username" class="input" placeholder="root" />
          </div>
          <div class="field" style="margin-top: 10px">
            <label>{{ t('口令', 'Password') }}</label>
            <input v-model="form.config.password" type="password" class="input" :placeholder="secretPlaceholder('password')" />
          </div>
          <div class="alert info" style="margin-top: 12px">
            {{ t('SSH 资产可通过“密码对”引用该凭据，避免在多个连接中重复填写口令。', 'SSH assets can reference this pair instead of duplicating the password.') }}
          </div>
        </template>

        <template v-else-if="!isDir">
          <div class="alert info">{{ t('该资产类型暂未实现编辑器。', 'This asset type has no editor yet.') }}</div>
        </template>

        <template v-else>
          <div class="alert info">{{ t('目录用于对资产分组，可拖放/移动管理。', 'Folders group assets.') }}</div>
        </template>

        <div v-if="testResult" class="alert mt12" :class="testResult.ok ? 'info' : 'error'" style="display: flex; gap: 8px; align-items: flex-start">
          <CheckCircle2 v-if="testResult.ok" :size="14" style="color: var(--green); flex-shrink: 0; margin-top: 2px" />
          <XCircle v-else :size="14" style="color: var(--red); flex-shrink: 0; margin-top: 2px" />
          <span style="word-break: break-word">{{ testResult.message }}</span>
        </div>
      </div>

      <div class="modal-foot">
        <div class="left">
          <button v-if="testable" class="btn" :disabled="testing" @click="testConnection">
            <Loader2 v-if="testing" :size="13" class="tool-spin" />
            {{ testing ? t('测试中…', 'Testing…') : t('测试连接', 'Test connection') }}
          </button>
        </div>
        <button class="btn" @click="close">{{ t('取消', 'Cancel') }}</button>
        <button class="btn primary" :disabled="saving" @click="save">{{ isEdit ? t('保存', 'Save') : t('创建', 'Create') }}</button>
      </div>
    </div>
  </div>
</template>
