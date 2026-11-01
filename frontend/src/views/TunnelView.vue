<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Copy, GitBranch, Loader2, Play, Plug, RefreshCw, Square, Wifi } from 'lucide-vue-next'
import { api } from '../lib/api'
import { assetMap, t, toast } from '../lib/store'

const props = defineProps({ tab: { type: Object, required: true } })

const asset = computed(() => assetMap.value.get(props.tab.assetId))
const cfg = computed(() => asset.value?.config || {})
const sshName = computed(() => (cfg.value.sshAssetId ? assetMap.value.get(cfg.value.sshAssetId)?.name : ''))
const tunnels = ref([])
const loading = ref(false)
const starting = ref(false)
const error = ref('')
const probe = ref(null)
let timer = null

const kindLabel = computed(() => ({ local: 'Local -L', remote: 'Remote -R', dynamic: 'SOCKS5 -D' }[cfg.value.type] || 'Local'))
const mine = computed(() => tunnels.value.filter((x) => x.assetId === props.tab.assetId))

async function refresh() {
  try {
    const res = await api.get('/api/tunnels')
    tunnels.value = res.tunnels || []
  } catch (err) {
    error.value = String(err.message || err)
  }
}

async function start() {
  starting.value = true
  error.value = ''
  probe.value = null
  try {
    const status = await api.post('/api/tunnels/start', { assetId: props.tab.assetId })
    toast(t('隧道已启动', 'Tunnel started'), 'success')
    await refresh()
    await doProbe(status.id)
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    starting.value = false
  }
}

async function stop(tunnel) {
  try {
    await api.post(`/api/tunnels/${tunnel.id}/stop`)
    toast(t('隧道已停止', 'Tunnel stopped'), 'success')
    await refresh()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function doProbe(tunnelId) {
  const tunnel = tunnels.value.find((x) => x.id === tunnelId)
  if (!tunnel) return
  loading.value = true
  try {
    probe.value = await api.post('/api/tunnels/probe', {
      tunnelId,
      payload: tunnel.type === 'dynamic' ? '' : 'SSH-2.0-h1khub-probe\r\n',
      timeout: 5,
    })
  } catch (err) {
    probe.value = { ok: false, error: String(err.message || err) }
  } finally {
    loading.value = false
  }
}

function copyAddress(tunnel) {
  navigator.clipboard?.writeText(`${tunnel.bindHost}:${tunnel.bindPort}`)
  toast(t('地址已复制', 'Address copied'), 'success')
}

onMounted(async () => {
  await refresh()
  timer = setInterval(refresh, 4000)
})
onBeforeUnmount(() => timer && clearInterval(timer))
</script>

<template>
  <div class="view" style="flex-direction: column; min-height: 0; overflow: auto">
    <div class="toolbar">
      <GitBranch :size="14" style="color: var(--accent)" />
      <b style="font-size: 12.5px">{{ asset?.name || t('SSH 隧道', 'SSH tunnel') }}</b>
      <span class="badge accent">{{ kindLabel }}</span>
      <span class="tiny mono">{{ sshName || t('未选择 SSH 连接', 'no SSH connection') }}</span>
      <span class="grow" style="flex: 1" />
      <button class="btn ghost sm" @click="refresh"><RefreshCw :size="13" :class="{ 'tool-spin': loading }" /> {{ t('刷新', 'Refresh') }}</button>
      <button class="btn primary sm" :disabled="starting || !cfg.sshAssetId" @click="start">
        <Loader2 v-if="starting" :size="13" class="tool-spin" /><Play v-else :size="13" />
        {{ t('启动隧道', 'Start tunnel') }}
      </button>
    </div>

    <div style="padding: 14px 16px; max-width: 900px">
      <div class="alert error" v-if="error">{{ error }}</div>
      <div class="alert info" v-if="!cfg.sshAssetId">
        {{ t('该隧道资产还没有绑定 SSH 连接，请编辑资产后重试。', 'This tunnel asset has no SSH connection. Edit the asset first.') }}
      </div>

      <div class="stat-grid" style="margin-bottom: 14px">
        <div class="stat-card">
          <div class="stat-value mono">{{ cfg.bindHost || '127.0.0.1' }}:{{ cfg.bindPort || 0 }}</div>
          <div class="stat-label">{{ cfg.type === 'remote' ? t('远端监听地址', 'Remote bind') : t('本地监听地址', 'Local bind') }}</div>
        </div>
        <div class="stat-card" v-if="cfg.type !== 'dynamic'">
          <div class="stat-value mono">{{ cfg.targetHost || '-' }}:{{ cfg.targetPort || '-' }}</div>
          <div class="stat-label">{{ cfg.type === 'remote' ? t('本地目标', 'Local target') : t('转发目标', 'Forward target') }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ mine.length }}</div>
          <div class="stat-label">{{ t('运行中的实例', 'Running instances') }}</div>
        </div>
      </div>

      <div class="row" style="margin: 6px 0 8px; align-items: center">
        <b style="font-size: 12.5px">{{ t('运行中的隧道', 'Running tunnels') }}</b>
        <span class="badge" style="margin-left: 6px">{{ tunnels.length }}</span>
      </div>

      <div v-if="!tunnels.length" class="empty-state" style="padding: 26px">
        <GitBranch :size="24" style="opacity: .4" />
        <div class="tiny">{{ t('还没有运行中的隧道', 'No running tunnels') }}</div>
      </div>

      <div v-for="tunnel in tunnels" :key="tunnel.id" class="tunnel-row" :style="{ border: '1px solid var(--border)', borderRadius: '8px', padding: '10px 12px', marginBottom: '8px', background: tunnel.assetId === tab.assetId ? 'var(--bg-panel)' : 'transparent' }">
        <div class="row" style="gap: 8px; align-items: center">
          <Wifi :size="14" :style="{ color: tunnel.running ? 'var(--green, #4caf50)' : 'var(--text-3)' }" />
          <b style="font-size: 12.5px">{{ tunnel.name }}</b>
          <span class="badge">{{ tunnel.type }}</span>
          <span class="mono tiny">{{ tunnel.bindHost }}:{{ tunnel.bindPort }}</span>
          <span v-if="tunnel.type !== 'dynamic'" class="tiny">→ {{ tunnel.targetHost }}:{{ tunnel.targetPort }}</span>
          <span class="grow" style="flex: 1" />
          <span class="tiny">{{ tunnel.connections }} {{ t('连接', 'conns') }}</span>
          <button class="icon-btn sm" :title="t('复制地址', 'Copy address')" @click="copyAddress(tunnel)"><Copy :size="13" /></button>
          <button class="icon-btn sm" :title="t('探测连接', 'Probe')" @click="doProbe(tunnel.id)"><Plug :size="13" /></button>
          <button class="btn sm" @click="stop(tunnel)"><Square :size="12" /> {{ t('停止', 'Stop') }}</button>
        </div>
      </div>

      <div v-if="probe" class="alert" :class="probe.ok ? 'info' : 'error'" style="margin-top: 10px; font-family: var(--mono); font-size: 11.5px; white-space: pre-wrap">
        {{ probe.ok ? `${probe.host}:${probe.port} · ${probe.elapsed} ms · ${probe.bytes} bytes\n${probe.banner || ''}` : probe.error }}
      </div>

      <div class="alert info" style="margin-top: 12px">
        <template v-if="cfg.type === 'dynamic'">
          {{ t('SOCKS5 代理：把浏览器/工具的代理设置为 SOCKS5 上述监听地址即可。仅本机使用。', 'Point your tool at that SOCKS5 address (local use only).') }}
        </template>
        <template v-else-if="cfg.type === 'remote'">
          {{ t('远程转发：监听在 SSH 服务器侧，适合让远端访问本机服务。关闭隧道时监听会一并移除。', 'Remote forward listens on the SSH server side; closing the tunnel removes it.') }}
        </template>
        <template v-else>
          {{ t('本地转发：通过 SSH 服务器访问目标主机；可用于访问仅服务器可达的内网服务。', 'Local forward reaches a target through the SSH server.') }}
        </template>
      </div>
      <div class="tiny" style="margin-top: 8px">
        {{ t('隧道与数据库/Redis 使用的临时 SSH 转发相互独立；停止后监听端口立即释放。', 'Independent from DB/Redis SSH tunnels; stopping releases the port.') }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 8px; }
.stat-card { border: 1px solid var(--border); border-radius: 6px; padding: 9px 10px; background: var(--bg-panel); }
.stat-value { font-size: 14px; font-weight: 600; }
.stat-label { font-size: 10.5px; color: var(--text-3); margin-top: 2px; }
</style>
