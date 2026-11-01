<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Chrome, Loader2, RefreshCw, Square, X } from 'lucide-vue-next'
import { api } from '../lib/api'
import { state, t, toast } from '../lib/store'

const info = ref({ available: false, path: '', tried: [], sessions: [], maxSessions: 8 })
const loading = ref(false)
const stopping = ref('')
const error = ref('')
let timer = null

const sessions = computed(() => info.value.sessions || [])

async function load(quiet = false) {
  if (!quiet) loading.value = true
  try {
    const res = await api.get('/api/chrome-proxy')
    info.value = { ...info.value, ...res }
    // Keep the asset context menu's stop entry in sync with the dialog.
    state.chromeProxy = { ...state.chromeProxy, ...res }
    error.value = ''
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(
  () => state.chromeProxyOpen,
  (open) => {
    if (open) {
      load()
      stopTimer()
      timer = setInterval(() => load(true), 3000)
    } else {
      stopTimer()
    }
  },
)

async function stop(session) {
  stopping.value = session.id
  try {
    await api.post(`/api/chrome-proxy/${session.id}/stop`)
    toast(t('Chrome 代理已停止', 'Chrome proxy stopped'), 'success')
    await load(true)
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    stopping.value = ''
  }
}

function elapsed(session) {
  const ms = Number(session.elapsedMs || 0)
  if (ms < 60000) return `${Math.max(1, Math.round(ms / 1000))}s`
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`
}

onBeforeUnmount(stopTimer)
</script>

<template>
  <div v-if="state.chromeProxyOpen" class="modal-mask" @mousedown.self="state.chromeProxyOpen = false">
    <div class="modal" style="width: 760px; max-height: 82vh; display: flex; flex-direction: column">
      <div class="modal-head">
        <Chrome :size="15" />
        <span class="modal-title">{{ t('Chrome 服务器代理', 'Chrome via SSH proxy') }}</span>
        <span class="badge">{{ sessions.length }} {{ t('运行中', 'running') }}</span>
        <span class="grow" style="flex: 1" />
        <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="load()">
          <RefreshCw :size="14" :class="{ 'tool-spin': loading }" />
        </button>
        <button class="icon-btn sm" @click="state.chromeProxyOpen = false"><X :size="14" /></button>
      </div>
      <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
        <div v-if="error" class="alert error">{{ error }}</div>

        <div v-if="!info.available" class="alert error">
          <div>
            {{ t('未找到本机 Chrome/Chromium，无法启动代理浏览器。', 'No local Chrome/Chromium found; the proxied browser cannot be launched.') }}
          </div>
          <div class="tiny mono" style="margin-top: 5px; word-break: break-all">
            {{ t('已尝试：', 'Tried: ') }}{{ (info.tried || []).join(' · ') || '-' }}
          </div>
          <div class="tiny" style="margin-top: 5px">
            {{ t('安装 Chrome/Chromium 后点刷新，或用环境变量 H1KHUB_CHROME_PATH 指定可执行文件。', 'Install Chrome/Chromium and refresh, or point H1KHUB_CHROME_PATH at the executable.') }}
          </div>
        </div>
        <div v-else class="alert info">
          {{ t('在资产列表右键 SSH 资产，选择「通过服务器代理 Chrome」即可启动；浏览器流量经该服务器的 SOCKS5 隧道转发。', 'Right-click an SSH asset and choose "Proxy Chrome via server"; browser traffic goes through that server over SOCKS5.') }}
        </div>

        <div class="row" style="align-items: center; gap: 8px; padding: 2px 2px 10px">
          <span class="tiny" style="color: var(--text-3)">{{ t('浏览器', 'Browser') }}</span>
          <span class="tiny mono" style="word-break: break-all">{{ info.path || '-' }}</span>
        </div>

        <div v-if="!sessions.length" class="center-hint" style="padding: 34px; flex-direction: column">
          <Chrome :size="24" style="opacity: .4" />
          <span class="tiny">{{ t('没有运行中的 Chrome 代理会话', 'No running Chrome proxy sessions') }}</span>
        </div>

        <div v-for="session in sessions" :key="session.id" class="chrome-session">
          <div class="row" style="align-items: center; gap: 8px">
            <Chrome :size="14" style="color: var(--accent)" />
            <b style="font-size: 12.5px">{{ session.assetName }}</b>
            <span class="badge accent">{{ t('运行中', 'running') }}</span>
            <span class="tiny">{{ elapsed(session) }}</span>
            <span class="grow" style="flex: 1" />
            <button class="btn sm" :disabled="stopping === session.id" @click="stop(session)">
              <Loader2 v-if="stopping === session.id" :size="12" class="tool-spin" />
              <Square v-else :size="12" />
              {{ t('停止', 'Stop') }}
            </button>
          </div>
          <div class="tiny mono" style="margin-top: 5px; word-break: break-all">
            {{ session.proxy }} · {{ t(`${session.connections || 0} 个连接`, `${session.connections || 0} connections`) }} · pid {{ session.pid }} · {{ session.startedAt }}
          </div>
          <div v-if="session.recentConnections?.length" class="tiny mono" style="margin-top: 2px; color: var(--text-3); word-break: break-all">
            {{ t('最近目标', 'Recent targets') }}: {{ session.recentConnections.slice(-3).join(' · ') }}
          </div>
          <div v-if="session.profileDir" class="tiny mono" style="margin-top: 2px; color: var(--text-3); word-break: break-all">
            {{ session.profileDir }}
          </div>
        </div>

        <div class="tiny" style="margin-top: 14px; color: var(--text-3); line-height: 1.65">
          {{ t('Chrome 由本机后端启动，每次使用独立临时配置目录；关闭浏览器或点击停止会同时关闭 SOCKS5 隧道并删除临时配置。请确保已安装 Chrome 且未开启其他代理软件。', 'Chrome is launched by the local backend with a throwaway profile; closing the browser or pressing Stop shuts the SOCKS5 tunnel and deletes the profile. Make sure Chrome is installed and no other proxy tool is active.') }}
        </div>
      </div>
      <div class="modal-foot">
        <span class="left tiny">
          {{ t(`上限 ${info.maxSessions || 8} 个并发实例`, `Up to ${info.maxSessions || 8} concurrent instances`) }}
        </span>
        <button class="btn sm" @click="state.chromeProxyOpen = false">{{ t('关闭', 'Close') }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chrome-session {
  border-bottom: 1px solid var(--border);
  padding: 10px 2px;
}
.chrome-session:last-of-type {
  border-bottom: 0;
}
</style>
