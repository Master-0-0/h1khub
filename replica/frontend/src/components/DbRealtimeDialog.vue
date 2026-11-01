<script setup>
import { onMounted, ref } from 'vue'
import { Activity, Loader2, RefreshCw, X } from 'lucide-vue-next'
import { api } from '../lib/api'
import { t } from '../lib/store'
import { formatBytes } from '../lib/format'

const props = defineProps({
  assetId: { type: String, required: true },
  database: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const open = ref(true)
const loading = ref(false)
const error = ref('')
const info = ref(null)
const processes = ref([])

const labels = {
  uptime: t('运行时长（秒）', 'Uptime (s)'),
  threadsConnected: t('已连接线程', 'Connected threads'),
  threadsRunning: t('运行中线程', 'Running threads'),
  queries: t('累计查询', 'Queries'),
  selects: t('SELECT', 'Selects'),
  inserts: t('INSERT', 'Inserts'),
  updates: t('UPDATE', 'Updates'),
  deletes: t('DELETE', 'Deletes'),
  slowQueries: t('慢查询', 'Slow queries'),
  abortedConnects: t('失败连接', 'Aborted connects'),
  bytesReceived: t('接收字节', 'Bytes received'),
  bytesSent: t('发送字节', 'Bytes sent'),
  bufferPoolHitRate: t('缓冲池命中率 %', 'Buffer pool hit %'),
  maxConnections: t('最大连接数', 'Max connections'),
  processCount: t('进程数', 'Processes'),
  connections: t('连接数', 'Connections'),
  active: t('活跃连接', 'Active'),
  databaseSize: t('数据库大小', 'Database size'),
  commits: t('提交事务', 'Commits'),
  rollbacks: t('回滚事务', 'Rollbacks'),
  cacheHitRate: t('缓存命中率 %', 'Cache hit %'),
  lockWaiters: t('锁等待', 'Lock waiters'),
  pageCount: t('页数', 'Pages'),
  pageSize: t('页大小', 'Page size'),
  journalMode: t('journal 模式', 'Journal mode'),
  freePages: t('空闲页', 'Free pages'),
  cacheSize: t('缓存页数', 'Cache pages'),
  collectedAt: t('采集时间', 'Collected at'),
  dialect: t('方言', 'Dialect'),
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.post('/api/db/realtime', { assetId: props.assetId, database: props.database })
    info.value = res.info || {}
    processes.value = res.processes || []
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

function close() {
  open.value = false
  emit('close')
}

function render(key, value) {
  if (value === null || value === undefined || value === '') return '—'
  if (key === 'databaseSize' && /^\d+$/.test(String(value))) return formatBytes(Number(value))
  if ((key === 'bytesReceived' || key === 'bytesSent') && /^\d+$/.test(String(value))) return formatBytes(Number(value))
  return String(value)
}

onMounted(load)
</script>

<template>
  <div v-if="open" class="modal-mask" @mousedown.self="close">
    <div class="modal" style="width: 860px; max-height: 86vh; display: flex; flex-direction: column">
      <div class="modal-head">
        <Activity :size="15" />
        <span class="modal-title">{{ t('数据库实时信息', 'Database realtime info') }}</span>
        <span class="grow" style="flex: 1" />
        <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="load"><RefreshCw :size="14" :class="{ 'tool-spin': loading }" /></button>
        <button class="icon-btn sm" @click="close"><X :size="14" /></button>
      </div>
      <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
        <div v-if="loading" class="center-hint" style="padding: 40px"><Loader2 :size="20" class="tool-spin" /></div>
        <div v-else-if="error" class="alert error">{{ error }}</div>
        <template v-else>
          <div class="stat-grid">
            <div v-for="(value, key) in info" :key="key" class="stat-card">
              <div class="stat-value">{{ render(key, value) }}</div>
              <div class="stat-label">{{ labels[key] || key }}</div>
            </div>
          </div>

          <div class="row" style="margin: 14px 0 6px; align-items: center">
            <b style="font-size: 12.5px">{{ t('进程 / 会话', 'Processes / sessions') }}</b>
            <span class="badge" style="margin-left: 6px">{{ processes.length }}</span>
          </div>
          <div style="border: 1px solid var(--border); border-radius: 6px; max-height: 260px; overflow: auto">
            <table class="grid">
              <thead>
                <tr>
                  <th style="width: 70px">ID</th>
                  <th style="width: 100px">{{ t('用户', 'User') }}</th>
                  <th style="width: 110px">{{ t('来源', 'Host') }}</th>
                  <th style="width: 100px">DB</th>
                  <th style="width: 90px">{{ t('状态', 'State') }}</th>
                  <th style="width: 60px">{{ t('时长', 'Time') }}</th>
                  <th>{{ t('语句', 'Query') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in processes" :key="p.id">
                  <td class="mono tiny">{{ p.id }}</td>
                  <td class="tiny">{{ p.user }}</td>
                  <td class="tiny mono">{{ p.host }}</td>
                  <td class="tiny">{{ p.db }}</td>
                  <td class="tiny">{{ p.command }}<span v-if="p.state"> / {{ p.state }}</span></td>
                  <td class="tiny">{{ p.time }}</td>
                  <td class="mono tiny" style="max-width: 320px; overflow: hidden; text-overflow: ellipsis">{{ p.info }}</td>
                </tr>
                <tr v-if="!processes.length"><td colspan="7" class="tiny" style="text-align: center; padding: 14px">{{ t('无进程信息', 'No processes') }}</td></tr>
              </tbody>
            </table>
          </div>
          <div class="tiny" style="margin-top: 8px">
            {{ t('数据来自当前连接会话；MySQL 的进程列表需要 PROCESS 权限。', 'Data comes from this connection; MySQL process list needs the PROCESS privilege.') }}
          </div>
        </template>
      </div>
      <div class="modal-foot">
        <button class="btn primary" @click="close">{{ t('关闭', 'Close') }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.stat-card { border: 1px solid var(--border); border-radius: 6px; padding: 9px 10px; background: var(--bg-panel); }
.stat-value { font-size: 15px; font-weight: 600; font-family: var(--mono); }
.stat-label { font-size: 10.5px; color: var(--text-3); margin-top: 2px; }
</style>
