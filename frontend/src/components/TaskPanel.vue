<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Ban, CheckCircle2, ListTodo, Loader2, RefreshCw, X, XCircle } from 'lucide-vue-next'
import { api } from '../lib/api'
import { state, t, toast } from '../lib/store'

const tasks = ref([])
const loading = ref(false)
const error = ref('')
let timer = null

async function load() {
  loading.value = true
  try {
    const res = await api.get('/api/tasks', { limit: 100 })
    tasks.value = res.tasks || []
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

async function cancel(task) {
  try {
    await api.post(`/api/tasks/${task.id}/cancel`)
    toast(t('已请求取消', 'Cancellation requested'), 'info')
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function resultText(task) {
  if (task.error) return task.error
  if (!task.result) return ''
  try {
    return JSON.stringify(task.result).slice(0, 200)
  } catch {
    return String(task.result).slice(0, 200)
  }
}

function statusClass(status) {
  return { success: 'green', failed: 'red', cancelled: 'orange', running: 'accent' }[status] || ''
}

onMounted(() => {
  load()
  timer = setInterval(load, 2500)
})
onBeforeUnmount(() => timer && clearInterval(timer))
</script>

<template>
  <div v-if="state.taskPanelOpen" class="modal-mask" @mousedown.self="state.taskPanelOpen = false">
    <div class="modal" style="width: 720px; max-height: 82vh; display: flex; flex-direction: column">
      <div class="modal-head">
        <ListTodo :size="15" />
        <span class="modal-title">{{ t('后台任务', 'Background tasks') }}</span>
        <span class="badge">{{ tasks.filter((x) => x.status === 'running' || x.status === 'queued').length }} {{ t('进行中', 'active') }}</span>
        <span class="grow" style="flex: 1" />
        <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="load"><RefreshCw :size="14" :class="{ 'tool-spin': loading }" /></button>
        <button class="icon-btn sm" @click="state.taskPanelOpen = false"><X :size="14" /></button>
      </div>
      <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
        <div v-if="error" class="alert error">{{ error }}</div>
        <div v-if="!tasks.length && !error" class="center-hint" style="padding: 30px; flex-direction: column">
          <ListTodo :size="24" style="opacity: .4" />
          <span class="tiny">{{ t('暂无后台任务', 'No background tasks') }}</span>
        </div>
        <div v-for="task in tasks" :key="task.id" style="border-bottom: 1px solid var(--border); padding: 9px 2px">
          <div class="row" style="gap: 8px; align-items: center">
            <CheckCircle2 v-if="task.status === 'success'" :size="14" style="color: var(--green, #4caf50)" />
            <XCircle v-else-if="task.status === 'failed'" :size="14" style="color: var(--red)" />
            <Ban v-else-if="task.status === 'cancelled'" :size="14" style="color: var(--orange, #f0a35e)" />
            <Loader2 v-else :size="14" class="tool-spin" />
            <b style="font-size: 12.5px">{{ task.title }}</b>
            <span class="badge" :class="statusClass(task.status)">{{ task.status }}</span>
            <span class="tiny">{{ task.kind }}</span>
            <span class="grow" style="flex: 1" />
            <span v-if="task.elapsedMs" class="tiny">{{ task.elapsedMs }} ms</span>
            <button v-if="task.status === 'running' || task.status === 'queued'" class="btn sm" @click="cancel(task)"><Ban :size="12" /> {{ t('取消', 'Cancel') }}</button>
          </div>
          <div v-if="resultText(task)" class="tiny mono" style="margin-top: 4px; color: var(--text-3); white-space: pre-wrap; word-break: break-all">{{ resultText(task) }}</div>
          <div class="tiny" style="margin-top: 2px; color: var(--text-3)">{{ task.createdAt }}<span v-if="task.finishedAt"> → {{ task.finishedAt }}</span></div>
        </div>
      </div>
    </div>
  </div>
</template>
