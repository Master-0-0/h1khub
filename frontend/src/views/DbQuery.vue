<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  Bookmark, Check, CircleDot, Copy, Download, Eraser, FileUp, Loader2, Play, RotateCcw, Search,
  X, Zap,
} from 'lucide-vue-next'
import SqlEditor from '../components/SqlEditor.vue'
import DbImportDialog from '../components/DbImportDialog.vue'
import { api, postDownload, rpc } from '../lib/api'
import { decodeTableData } from '../lib/packet'
import { createAsset, t, toast } from '../lib/store'
import { promptDialog, showContextMenu } from '../lib/ui'
import { cellText, formatSql } from '../lib/format'

const props = defineProps({
  assetId: { type: String, required: true },
  database: { type: String, default: '' },
  dialect: { type: String, default: 'mysql' },
  initialSql: { type: String, default: '' },
  name: { type: String, default: '' },
})

const sql = ref(props.initialSql || '')
const running = ref(false)
const liveElapsed = ref(0)
let liveTimer = null
const results = ref([])
const elapsed = ref(0)
const error = ref('')
const editorRef = ref(null)
const explainOpen = ref(false)
const explainText = ref('')
const explainLoading = ref(false)
const editorHeight = ref(230)
const session = ref(null)
const txBusy = ref(false)
const importOpen = ref(false)
const sessionError = ref('')

const editorPlaceholder = computed(() => (
  props.dialect === 'mongodb'
    ? 'db.users.find({"role":"admin"}).limit(50)'
    : props.dialect === 'clickhouse'
      ? 'SELECT * FROM demo.users LIMIT 100'
      : 'SELECT * FROM users LIMIT 100;'
))

const txLabel = computed(() => {
  if (!session.value) return t('未连接', 'offline')
  if (session.value.inTransaction) return t('事务进行中', 'in transaction')
  return session.value.autocommit ? t('自动提交', 'autocommit') : t('手动提交', 'manual commit')
})

const rowTotal = computed(() => results.value.reduce((acc, r) => acc + (r.rowCount || 0), 0))

async function ensureSession() {
  if (session.value?.id) return session.value
  try {
    session.value = await api.post('/api/db/session/open', { assetId: props.assetId, autocommit: true })
    sessionError.value = ''
  } catch (err) {
    sessionError.value = String(err.message || err)
    return null
  }
  return session.value
}

async function tx(action) {
  const current = await ensureSession()
  if (!current) return
  txBusy.value = true
  error.value = ''
  try {
    session.value = await api.post('/api/db/tx', { sessionId: current.id, action })
    const text = {
      begin: t('事务已开启', 'Transaction started'),
      commit: t('事务已提交', 'Transaction committed'),
      rollback: t('事务已回滚', 'Transaction rolled back'),
      'set-autocommit': t('自动提交已切换', 'Autocommit toggled'),
    }[action]
    if (text) toast(text, 'success')
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    txBusy.value = false
  }
}

async function toggleAutocommit() {
  const current = await ensureSession()
  if (!current) return
  txBusy.value = true
  try {
    session.value = await api.post('/api/db/tx', { sessionId: current.id, action: 'set-autocommit', enabled: !current.autocommit })
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    txBusy.value = false
  }
}

async function closeSession() {
  const current = session.value
  session.value = null
  if (current?.id) {
    try {
      await api.post('/api/db/session/close', { sessionId: current.id })
    } catch {}
  }
}

async function run() {
  const selected = editorRef.value?.getSelection?.() || ''
  const statement = (selected || sql.value).trim()
  if (!statement) {
    toast(t('SQL 为空', 'SQL is empty'), 'info')
    return
  }
  running.value = true
  error.value = ''
  liveElapsed.value = 0
  liveTimer = setInterval(() => { liveElapsed.value = Date.now() - startedAt }, 200)
  const startedAt = Date.now()
  try {
    // Transactions must run on the persistent session; plain statements use
    // the binary RPC channel (protobuf TableDataResponse) below.
    const current = session.value?.id && session.value?.inTransaction ? await ensureSession() : null
    if (current) {
      const res = await api.post('/api/db/session/query', {
        sessionId: current.id,
        database: props.database || undefined,
        sql: statement,
      })
      results.value = res.results || []
      elapsed.value = res.elapsed || 0
      session.value = res.session || current
    } else {
      // Non-transactional queries travel over the binary RPC channel and the
      // reply is a protobuf TableDataResponse (proto_model.TableDataResponse).
      let protoOk = false
      try {
        const started = Date.now()
        const bytes = await rpc.call('db.query', {
          assetId: props.assetId,
          database: props.database,
          sql: statement,
        })
        const data = decodeTableData(bytes)
        results.value = [{
          columns: data.columns,
          rows: data.rows,
          rowCount: data.rows.length,
          truncated: data.totalCount > data.rows.length,
          affectedRows: null,
        }]
        elapsed.value = Date.now() - started
        protoOk = true
        if (typeof window !== 'undefined') {
          window.__h1khubRpc = { ...(window.__h1khubRpc || {}), mode: 'binary', protoQuery: (window.__h1khubRpc?.protoQuery || 0) + 1 }
        }
      } catch (err) {
        protoOk = false
        if (typeof window !== 'undefined') {
          window.__h1khubRpc = { ...(window.__h1khubRpc || {}), error: String((err && err.message) || err) }
        }
      }
      if (!protoOk) {
        const res = await api.post('/api/db/query', { assetId: props.assetId, database: props.database, sql: statement })
        results.value = res.results || []
        elapsed.value = res.elapsed || 0
      }
    }
  } catch (err) {
    error.value = String(err.message || err)
    results.value = []
  } finally {
    running.value = false
    liveElapsed.value = Date.now() - startedAt
    if (liveTimer) clearInterval(liveTimer)
    liveTimer = null
  }
}

async function stopQuery() {
  try {
    if (session.value?.id) await api.post('/api/db/session/cancel', { sessionId: session.value.id })
    else await api.post('/api/db/cancel', { assetId: props.assetId })
    toast(t('已请求取消查询', 'Cancellation requested'), 'info')
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function formatQuery() {
  sql.value = formatSql(sql.value)
}

function exportMenu(event) {
  if (!results.value.length) {
    toast(t('暂无结果可导出', 'No results to export'), 'info')
    return
  }
  showContextMenu(event, [
    { label: 'CSV', action: () => exportResult('csv') },
    { label: t('制表符 TXT', 'TXT (tab)'), action: () => exportResult('txt') },
    { label: 'Excel XLSX', action: () => exportResult('xlsx') },
    { label: 'JSON', action: () => exportResult('json') },
    { label: t('SQL INSERT', 'SQL INSERT'), action: () => exportResult('sql') },
  ])
}

async function exportResult(format) {
  const statement = (editorRef.value?.getSelection?.() || sql.value).trim()
  if (!statement) return
  try {
    await postDownload(
      '/api/db/export',
      { assetId: props.assetId, database: props.database, sql: statement, format },
      `query.${format}`,
    )
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function explain() {
  const selected = editorRef.value?.getSelection?.() || ''
  const statement = (selected || sql.value).trim()
  if (!statement) return
  explainOpen.value = true
  explainLoading.value = true
  try {
    const res = await api.post('/api/db/explain', { assetId: props.assetId, database: props.database, sql: statement })
    explainText.value = res.plan || ''
  } catch (err) {
    explainText.value = String(err.message || err)
  } finally {
    explainLoading.value = false
  }
}

function clear() {
  results.value = []
  error.value = ''
}

function copyResults() {
  const payload = results.value.map((r) => ({ columns: r.columns, rows: r.rows }))
  navigator.clipboard?.writeText(JSON.stringify(payload, null, 2))
  toast(t('结果已复制为 JSON', 'Results copied as JSON'), 'success')
}

async function saveAsQuery() {
  const name = await promptDialog({
    title: t('保存查询', 'Save query'),
    message: t('查询名称', 'Query name'),
    placeholder: props.name || 'my-query',
  })
  if (!name) return
  try {
    await createAsset({
      typeName: 'DBQuery',
      name,
      config: { assetId: props.assetId, database: props.database, sql: sql.value },
    })
    toast(t('查询已保存到资产', 'Query saved to assets'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function display(value) {
  if (value === null || value === undefined) return null
  if (typeof value === 'object') return cellText(value)
  return String(value)
}

function startResize(event) {
  const startY = event.clientY
  const startH = editorHeight.value
  const move = (e) => {
    editorHeight.value = Math.min(Math.max(startH + (e.clientY - startY), 100), window.innerHeight - 240)
  }
  const up = () => {
    document.removeEventListener('mousemove', move)
    document.removeEventListener('mouseup', up)
  }
  document.addEventListener('mousemove', move)
  document.addEventListener('mouseup', up)
}

onMounted(() => {
  if (!sql.value && props.initialSql) sql.value = props.initialSql
})

onBeforeUnmount(closeSession)
</script>

<template>
  <div style="display: flex; flex-direction: column; flex: 1; min-height: 0">
    <div class="toolbar">
      <button class="btn primary sm" :disabled="running" @click="run">
        <Loader2 v-if="running" :size="13" class="tool-spin" /><Play v-else :size="13" />
        {{ running ? t('执行中…', 'Running…') + ' ' + (liveElapsed / 1000).toFixed(1) + 's' : t('执行', 'Run') }}
      </button>
      <button v-if="running" class="btn sm" @click="stopQuery"><Square :size="11" /> {{ t('停止', 'Stop') }}</button>
      <button class="btn ghost sm" :disabled="running" @click="formatQuery"><AlignLeft :size="12" /> {{ t('格式化', 'Format') }}</button>
      <span class="tiny" style="margin-left: 4px"><span class="kbd">Ctrl</span> <span class="kbd">↵</span></span>
      <div class="sep" />
      <button class="btn ghost sm" @click="explain"><Zap :size="13" /> Explain</button>
      <button class="btn ghost sm" @click="saveAsQuery"><Bookmark :size="13" /> {{ t('保存查询', 'Save') }}</button>
      <button class="btn ghost sm" @click="clear"><Eraser :size="13" /> {{ t('清空结果', 'Clear') }}</button>
      <button class="btn ghost sm" :disabled="!results.length" @click="copyResults"><Copy :size="13" /> {{ t('复制结果', 'Copy') }}</button>
      <span class="grow" style="flex: 1" />
      <span class="badge">{{ props.database || t('默认库', 'default') }}</span>
      <span v-if="error" class="badge red">{{ error.slice(0, 80) }}</span>
    </div>

    <div class="toolbar" style="height: 34px; border-top: 0; background: var(--bg-2, var(--bg-panel))">
      <CircleDot :size="12" :style="{ color: session?.inTransaction ? 'var(--warning, #f0a35e)' : 'var(--text-3)' }" />
      <span class="tiny" style="min-width: 78px">{{ txLabel }}</span>
      <button class="btn sm" :disabled="txBusy || session?.inTransaction" @click="tx('begin')">BEGIN</button>
      <button class="btn sm" :disabled="txBusy || !session?.inTransaction" @click="tx('commit')"><Check :size="12" /> {{ t('提交', 'Commit') }}</button>
      <button class="btn sm" :disabled="txBusy || !session?.inTransaction" @click="tx('rollback')"><RotateCcw :size="12" /> {{ t('回滚', 'Rollback') }}</button>
      <label class="tiny row" style="gap: 4px; margin-left: 4px; cursor: pointer">
        <input type="checkbox" :checked="!!session?.autocommit" :disabled="txBusy || session?.inTransaction" @change="toggleAutocommit" />
        {{ t('自动提交', 'autocommit') }}
      </label>
      <span v-if="sessionError" class="badge red" :title="sessionError">{{ t('会话不可用', 'session unavailable') }}</span>
      <span class="grow" style="flex: 1" />
      <button class="btn ghost sm" @click="importOpen = true"><FileUp :size="13" /> {{ t('导入', 'Import') }}</button>
      <button class="btn ghost sm" :disabled="!results.length" @click="exportMenu"><Download :size="13" /> {{ t('导出结果', 'Export') }}</button>
    </div>

    <div :style="{ height: editorHeight + 'px', flex: 'none', display: 'flex', flexDirection: 'column', borderBottom: '1px solid var(--border)' }">
      <SqlEditor
        ref="editorRef"
        v-model="sql"
        :dialect="dialect"
        :placeholder="editorPlaceholder"
        @run="run"
      />
    </div>
    <div class="split-handle" style="width: 100%; height: 4px; cursor: row-resize" @mousedown.prevent="startResize" />

    <div style="flex: 1; overflow: auto; min-height: 0">
      <div v-if="error" class="alert error" style="margin: 12px">{{ error }}</div>
      <div v-if="!results.length && !error" class="center-hint" style="flex-direction: column; gap: 8px; padding: 40px">
        <Search :size="26" style="opacity: .4" />
        <span class="tiny">{{ t('执行 SQL 后在此查看结果（支持选中部分执行）', 'Run SQL to see results here (selection is supported)') }}</span>
      </div>
      <template v-for="(result, index) in results" :key="index">
        <div style="display: flex; align-items: center; gap: 10px; padding: 7px 12px; border-bottom: 1px solid var(--border); background: var(--bg-panel); position: sticky; top: 0; z-index: 1">
          <span class="badge accent">{{ t('结果', 'Result') }} {{ index + 1 }}</span>
          <span class="tiny">{{ result.rowCount }} {{ t('行', 'rows') }}</span>
          <span v-if="result.affectedRows !== null && result.affectedRows !== undefined" class="tiny">{{ t('影响', 'Affected') }} {{ result.affectedRows }}</span>
          <span v-if="result.truncated" class="badge orange">{{ t('已截断', 'truncated') }}</span>
          <span class="grow" />
          <span class="tiny">{{ elapsed }} ms</span>
        </div>
        <div v-if="result.columns.length" style="overflow: auto; max-height: 420px">
          <table class="grid">
            <thead><tr><th v-for="col in result.columns" :key="col">{{ col }}</th></tr></thead>
            <tbody>
              <tr v-for="(row, ri) in result.rows.slice(0, 500)" :key="ri">
                <td v-for="(cell, ci) in row" :key="ci" :class="{ null: cell === null }" style="max-width: 480px">
                  {{ display(cell) === null ? 'NULL' : display(cell) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="alert info" style="margin: 12px">
          {{ result.message || t('语句已执行', 'Statement executed') }}
          <span v-if="result.affectedRows !== null && result.affectedRows !== undefined"> · {{ t('影响行数', 'affected') }} {{ result.affectedRows }}</span>
        </div>
      </template>
    </div>

    <div class="statusbar">
      <span>{{ t('共', 'Total') }} {{ rowTotal }} {{ t('行', 'rows') }}</span>
      <span class="grow" />
      <span>{{ t('执行耗时', 'Elapsed') }} {{ elapsed }} ms</span>
    </div>

    <DbImportDialog
      v-if="importOpen"
      :asset-id="assetId"
      :database="database"
      :dialect="dialect"
      @close="importOpen = false"
      @imported="importOpen = false; toast(t('导入完成', 'Import finished'), 'success')"
    />

    <div v-if="explainOpen" class="modal-mask" @mousedown.self="explainOpen = false">
      <div class="modal" style="width: 720px; max-height: 80vh">
        <div class="modal-head">
          <Zap :size="15" />
          <span class="modal-title">Explain</span>
          <button class="icon-btn sm" @click="explainOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div v-if="explainLoading" class="center-hint" style="padding: 30px"><Loader2 :size="18" class="tool-spin" /></div>
          <pre v-else style="font-family: var(--mono); font-size: 11.5px; white-space: pre-wrap; margin: 0">{{ explainText }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>
