<script setup>
import { computed, ref } from 'vue'
import { Clipboard, Monitor, RefreshCw } from 'lucide-vue-next'
import { api } from '../lib/api'
import { assetMap, state, t, toast } from '../lib/store'

const props = defineProps({ tab: { type: Object, required: true } })
const asset = computed(() => assetMap.value.get(props.tab.assetId))
const cfg = computed(() => asset.value?.config || {})
const address = computed(() => `${cfg.value.host || '127.0.0.1'}:${cfg.value.port || 5900}`)
const testing = ref(false)
const result = ref('')

async function test() {
  testing.value = true
  try {
    const res = await api.post('/api/test-connection', { assetId: props.tab.assetId })
    result.value = res.message || t('端口可达', 'Port reachable')
    toast(result.value, 'success')
  } catch (err) {
    result.value = String(err.message || err)
    toast(result.value, 'error')
  } finally {
    testing.value = false
  }
}

function copy() {
  navigator.clipboard?.writeText(address.value)
  toast(t('地址已复制', 'Address copied'), 'info')
}
</script>

<template>
  <div class="view">
    <div class="toolbar" style="height: 38px">
      <Monitor :size="15" style="color: var(--accent)" />
      <b style="font-size: 12.5px">{{ asset?.name }}</b>
      <span class="badge">VNC</span>
      <span class="grow" style="flex: 1" />
      <button class="btn sm" :disabled="testing" @click="test"><RefreshCw :size="12" :class="{ 'tool-spin': testing }" /> {{ t('测试端口', 'Test port') }}</button>
    </div>
    <div style="padding: 28px; max-width: 720px">
      <div class="card" style="padding: 20px">
        <div class="tiny">{{ t('连接地址', 'Address') }}</div>
        <div class="row" style="gap: 10px; margin-top: 6px; align-items: center">
          <code class="mono" style="font-size: 16px">{{ address }}</code>
          <button class="icon-btn sm" @click="copy"><Clipboard :size="13" /></button>
        </div>
        <div v-if="cfg.password" class="tiny" style="margin-top: 10px">{{ t('已保存访问密码（不回显）', 'Password saved (hidden)') }}</div>
        <div v-if="result" class="alert info" style="margin-top: 14px">{{ result }}</div>
      </div>
      <div class="alert info" style="margin-top: 14px">
        {{ t('VNC 条目管理保存地址/端口/密码并检测端口可达性；不内置 RFB 画面客户端。可复制地址到系统 VNC 客户端打开。', 'VNC entries store address/port/password and check port reachability; no built-in RFB viewer. Copy the address into a system VNC client.') }}
      </div>
      <div v-if="state.assets.length" class="tiny" style="margin-top: 8px">{{ t('提示：使用 vncviewer 或 Remmina 连接上述地址。', 'Tip: connect with vncviewer or Remmina.') }}</div>
    </div>
  </div>
</template>
