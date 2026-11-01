<script setup>
import { onMounted, ref } from 'vue'
import { Loader2, Megaphone, RefreshCw, Send, X } from 'lucide-vue-next'
import { api } from '../lib/api'
import { t, toast } from '../lib/store'

const emit = defineEmits(['close'])
const open = ref(true)
const sessions = ref([])
const checked = ref({})
const command = ref('')
const sending = ref(false)
const results = ref([])
const error = ref('')

onMounted(load)

async function load() {
  try {
    const res = await api.get('/api/sessions')
    sessions.value = (res.sessions || []).filter((s) => !s.closed)
    if (!Object.keys(checked.value).length) {
      const next = {}
      for (const s of sessions.value) next[s.id] = true
      checked.value = next
    }
  } catch (err) {
    error.value = String(err.message || err)
  }
}

async function send() {
  const ids = Object.keys(checked.value).filter((id) => checked.value[id])
  if (!ids.length) {
    toast(t('请选择至少一个会话', 'Select at least one session'), 'info')
    return
  }
  if (!command.value.trim()) {
    toast(t('命令不能为空', 'Command is empty'), 'info')
    return
  }
  sending.value = true
  results.value = []
  try {
    const res = await api.post('/api/sessions/broadcast', { sessionIds: ids, command: command.value })
    results.value = res.results || []
    toast(t(`已发送到 ${res.sent} 个会话`, `Sent to ${res.sent} session(s)`), 'success')
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    sending.value = false
  }
}

function close() {
  open.value = false
  emit('close')
}
</script>

<template>
  <div v-if="open" class="modal-mask" @mousedown.self="close">
    <div class="modal" style="width: 640px; max-height: 86vh; display: flex; flex-direction: column">
      <div class="modal-head">
        <Megaphone :size="15" />
        <span class="modal-title">{{ t('命令广播', 'Command broadcast') }}</span>
        <span class="grow" style="flex: 1" />
        <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="load"><RefreshCw :size="14" /></button>
        <button class="icon-btn sm" @click="close"><X :size="14" /></button>
      </div>
      <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
        <div class="tiny" style="margin-bottom: 6px">{{ t('选择要同时发送命令的终端会话', 'Choose the sessions to receive the command') }}</div>
        <div style="border: 1px solid var(--border); border-radius: 6px; max-height: 200px; overflow: auto">
          <label v-for="s in sessions" :key="s.id" class="row" style="gap: 8px; padding: 6px 10px; font-size: 12px; cursor: pointer; border-bottom: 1px solid var(--border)">
            <input v-model="checked[s.id]" type="checkbox" />
            <span>{{ s.title }}</span>
            <span class="badge">{{ s.kind }}</span>
            <span class="grow" style="flex: 1" />
            <span class="tiny mono">{{ s.assetId?.slice(0, 8) || '' }}</span>
          </label>
          <div v-if="!sessions.length" class="tiny" style="padding: 16px; text-align: center">{{ t('没有打开的终端会话', 'No open terminal sessions') }}</div>
        </div>
        <div class="field" style="margin-top: 12px">
          <label>{{ t('命令（发送时自动补换行）', 'Command (newline appended)') }}</label>
          <textarea v-model="command" class="textarea mono" style="height: 90px; font-size: 12px" placeholder="uptime && df -h" />
        </div>
        <div v-if="error" class="alert error" style="margin-top: 10px">{{ error }}</div>
        <div v-if="results.length" class="alert info" style="margin-top: 10px; font-size: 12px">
          {{ results.filter((r) => r.ok).length }} / {{ results.length }} {{ t('发送成功', 'sent') }}
          <span v-for="r in results.filter((x) => !x.ok)" :key="r.sessionId" class="badge red" style="margin-left: 6px">{{ r.error }}</span>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="close">{{ t('关闭', 'Close') }}</button>
        <button class="btn primary" :disabled="sending" @click="send">
          <Loader2 v-if="sending" :size="13" class="tool-spin" /><Send v-else :size="13" /> {{ t('发送', 'Send') }}
        </button>
      </div>
    </div>
  </div>
</template>
