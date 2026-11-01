<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  ArrowDown, ArrowRightLeft, ArrowUp, Binary, ChevronLeft, ChevronRight, Copy, Download, FileCode2,
  Filter, Loader2, Pencil, Plus, RefreshCw, Table2, Trash2, X,
} from 'lucide-vue-next'
import { api, postDownload } from '../lib/api'
import { state, t, toast } from '../lib/store'
import { confirmDialog, promptDialog, showContextMenu } from '../lib/ui'
import { cellText } from '../lib/format'
import BlobDialog from '../components/BlobDialog.vue'
import DbSchemaDialog from '../components/DbSchemaDialog.vue'
import DbSyncDialog from '../components/DbSyncDialog.vue'

const props = defineProps({
  assetId: { type: String, required: true },
  database: { type: String, default: '' },
  schema: { type: String, default: '' },
  table: { type: String, required: true },
  readOnly: { type: Boolean, default: false },
  dialect: { type: String, default: 'mysql' },
})

const loading = ref(false)
const error = ref('')
const data = ref(null)
const where = ref('')
const whereInput = ref('')
const orderBy = ref('')
const orderDir = ref('ASC')
const limit = ref(200)
const offset = ref(0)
const selected = ref(-1)
const editing = ref(null)
const saving = ref(false)
const showDdl = ref(false)
const ddl = ref('')
const ddlLoading = ref(false)
const addOpen = ref(false)
const newRow = ref({})
const newRowNull = ref({})
const checked = ref({})
const blobTarget = ref(null)
const schemaOpen = ref(false)
const syncOpen = ref(false)
const batchOpen = ref(false)
const batchColumn = ref('')
const batchValue = ref('')
const deletedNotice = ref('')

const columns = computed(() => data.value?.columns || [])
const rows = computed(() => data.value?.rows || [])
const pk = computed(() => data.value?.primaryKey || [])
const editable = computed(() => !props.readOnly && data.value?.editable && pk.value.length > 0)
const total = computed(() => data.value?.total)
const page = computed(() => Math.floor(offset.value / limit.value) + 1)
const pageCount = computed(() => (total.value ? Math.max(1, Math.ceil(total.value / limit.value)) : 1))
const checkedIndexes = computed(() => Object.keys(checked.value).filter((k) => checked.value[k]).map(Number))
const allChecked = computed(() => rows.value.length > 0 && checkedIndexes.value.length === rows.value.length)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.post('/api/db/table', {
      assetId: props.assetId,
      database: props.database,
      schema: props.schema,
      table: props.table,
      limit: limit.value,
      offset: offset.value,
      orderBy: orderBy.value || undefined,
      orderDir: orderDir.value,
      where: where.value || undefined,
    })
    data.value = res
    checked.value = {}
    if (selected.value >= res.rows.length) selected.value = res.rows.length - 1
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

function reloadFirstPage() {
  offset.value = 0
  load()
}

function applyFilter() {
  where.value = whereInput.value.trim()
  offset.value = 0
  load()
}

function toggleSort(column) {
  if (orderBy.value === column.name) {
    if (orderDir.value === 'ASC') orderDir.value = 'DESC'
    else {
      orderBy.value = ''
      orderDir.value = 'ASC'
    }
  } else {
    orderBy.value = column.name
    orderDir.value = 'ASC'
  }
  offset.value = 0
  load()
}

function setLimit(value) {
  limit.value = Number(value)
  offset.value = 0
  load()
}

function goPage(delta) {
  const next = offset.value + delta * limit.value
  offset.value = Math.max(0, next)
  load()
}

function jumpPage(event) {
  const value = Number(event.target.value)
  if (Number.isFinite(value) && value > 0) {
    offset.value = Math.min((value - 1) * limit.value, Math.max(0, (pageCount.value - 1) * limit.value))
    load()
  }
}

function rowKey(index) {
  const row = rows.value[index]
  const keys = {}
  pk.value.forEach((name) => {
    const colIndex = columns.value.findIndex((c) => c.name === name)
    keys[name] = row[colIndex]
  })
  return keys
}

function isEditing(rowIndex, colIndex) {
  return editing.value && editing.value.row === rowIndex && editing.value.col === colIndex
}

function startEdit(rowIndex, colIndex) {
  if (!editable.value || saving.value) return
  const col = columns.value[colIndex]
  const value = rows.value[rowIndex][colIndex]
  editing.value = {
    row: rowIndex,
    col: colIndex,
    value: value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value),
    original: value,
  }
}

async function commitEdit() {
  const stateEdit = editing.value
  if (!stateEdit || saving.value) return
  const col = columns.value[stateEdit.col]
  const before = rows.value[stateEdit.row][stateEdit.col]
  let after = stateEdit.value
  if (typeof before === 'number' && after !== '' && !Number.isNaN(Number(after))) after = Number(after)
  if (before === after) {
    editing.value = null
    return
  }
  saving.value = true
  try {
    const keys = rowKey(stateEdit.row)
    await api.post('/api/db/row', {
      assetId: props.assetId,
      database: props.database,
      schema: props.schema,
      table: props.table,
      action: 'update',
      keys,
      values: { [col.name]: after },
    })
    rows.value[stateEdit.row][stateEdit.col] = after
    toast(t('已更新 1 行', '1 row updated'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    saving.value = false
    editing.value = null
  }
}

async function setNull(rowIndex, colIndex) {
  if (!editable.value) return
  const col = columns.value[colIndex]
  try {
    await api.post('/api/db/row', {
      assetId: props.assetId,
      database: props.database,
      schema: props.schema,
      table: props.table,
      action: 'update',
      keys: rowKey(rowIndex),
      values: { [col.name]: null },
    })
    rows.value[rowIndex][colIndex] = null
    toast(t('已设为 NULL', 'Set to NULL'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function cellMenu(event, rowIndex, colIndex) {
  selected.value = rowIndex
  const col = columns.value[colIndex]
  const value = rows.value[rowIndex][colIndex]
  showContextMenu(event, [
    { label: t('复制值', 'Copy value'), action: () => navigator.clipboard?.writeText(cellText(value) ?? '') },
    { label: t('查看 Blob 内容', 'View blob'), hidden: !isBlobAt(rowIndex, colIndex), action: () => openBlob(rowIndex, colIndex) },
    { label: t('编辑', 'Edit'), disabled: !editable.value, action: () => startEdit(rowIndex, colIndex) },
    { label: t('设为 NULL', 'Set NULL'), disabled: !editable.value, action: () => setNull(rowIndex, colIndex) },
    { sep: true },
    {
      label: t('复制行 JSON', 'Copy row as JSON'),
      action: () => {
        const obj = {}
        columns.value.forEach((c, i) => (obj[c.name] = rows.value[rowIndex][i]))
        navigator.clipboard?.writeText(JSON.stringify(obj, null, 2))
      },
    },
    {
      label: t('删除该行', 'Delete row'),
      danger: true,
      disabled: !editable.value,
      action: () => deleteRow(rowIndex),
    },
  ])
}

async function deleteRow(rowIndex = selected.value) {
  if (rowIndex < 0) {
    toast(t('请先选择一行', 'Select a row first'), 'info')
    return
  }
  if (!editable.value) {
    toast(t('该表没有主键，无法安全删除', 'No primary key; row editing disabled'), 'error')
    return
  }
  const ok = await confirmDialog({
    title: t('删除行', 'Delete row'),
    message: t(`确定删除第 ${offset.value + rowIndex + 1} 行吗？`, `Delete row ${offset.value + rowIndex + 1}?`),
    danger: true,
    okText: t('删除', 'Delete'),
  })
  if (!ok) return
  try {
    await api.post('/api/db/row', {
      assetId: props.assetId,
      database: props.database,
      schema: props.schema,
      table: props.table,
      action: 'delete',
      keys: rowKey(rowIndex),
    })
    toast(t('已删除', 'Deleted'), 'success')
    selected.value = -1
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function openAddRow() {
  const values = {}
  const nulls = {}
  for (const col of columns.value) {
    if (col.extra?.includes('auto_increment')) continue
    values[col.name] = ''
    nulls[col.name] = col.nullable && col.default === null
  }
  newRow.value = values
  newRowNull.value = nulls
  addOpen.value = true
}

async function insertRow() {
  const values = {}
  for (const [key, value] of Object.entries(newRow.value)) {
    if (newRowNull.value[key]) values[key] = null
    else if (value !== '') values[key] = value
  }
  saving.value = true
  try {
    await api.post('/api/db/row', {
      assetId: props.assetId,
      database: props.database,
      schema: props.schema,
      table: props.table,
      action: 'insert',
      values,
    })
    toast(t('已插入 1 行', '1 row inserted'), 'success')
    addOpen.value = false
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    saving.value = false
  }
}

function toggleOne(index) {
  checked.value = { ...checked.value, [index]: !checked.value[index] }
}

function toggleAll() {
  if (allChecked.value) {
    checked.value = {}
  } else {
    const next = {}
    rows.value.forEach((_, i) => (next[i] = true))
    checked.value = next
  }
}

function openBlob(rowIndex, colIndex) {
  const value = rows.value[rowIndex]?.[colIndex]
  if (!value || typeof value !== 'object' || value.__type !== 'blob') return
  blobTarget.value = {
    table: props.table,
    column: columns.value[colIndex].name,
    rowKeys: rowKey(rowIndex),
    database: props.database,
    schema: props.schema,
  }
}

async function batchDelete() {
  const indexes = checkedIndexes.value
  if (!indexes.length) {
    toast(t('请先勾选行', 'Select rows first'), 'info')
    return
  }
  if (!editable.value) {
    toast(t('该表没有主键，无法批量删除', 'No primary key; batch delete disabled'), 'error')
    return
  }
  const ok = await confirmDialog({
    title: t('批量删除', 'Batch delete'),
    message: t(`确定删除选中的 ${indexes.length} 行吗？`, `Delete ${indexes.length} selected row(s)?`),
    danger: true,
    okText: t('删除', 'Delete'),
  })
  if (!ok) return
  saving.value = true
  try {
    const res = await api.post('/api/db/rows', {
      assetId: props.assetId,
      database: props.database,
      schema: props.schema,
      table: props.table,
      action: 'delete',
      rows: indexes.map((i) => rowKey(i)),
    })
    toast(t(`已删除 ${res.affected} 行`, `${res.affected} row(s) deleted`), 'success')
    checked.value = {}
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    saving.value = false
  }
}

function openBatchUpdate() {
  if (!checkedIndexes.value.length) {
    toast(t('请先勾选行', 'Select rows first'), 'info')
    return
  }
  batchColumn.value = columns.value[0]?.name || ''
  batchValue.value = ''
  batchOpen.value = true
}

async function applyBatchUpdate() {
  if (!batchColumn.value) return
  saving.value = true
  try {
    const res = await api.post('/api/db/rows', {
      assetId: props.assetId,
      database: props.database,
      schema: props.schema,
      table: props.table,
      action: 'update',
      rows: checkedIndexes.value.map((i) => rowKey(i)),
      values: { [batchColumn.value]: batchValue.value === '' ? null : batchValue.value },
    })
    toast(t(`已更新 ${res.affected} 行`, `${res.affected} row(s) updated`), 'success')
    batchOpen.value = false
    checked.value = {}
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    saving.value = false
  }
}

async function openDdl() {
  showDdl.value = true
  ddlLoading.value = true
  try {
    const res = await api.post('/api/db/ddl', {
      assetId: props.assetId,
      database: props.database,
      schema: props.schema,
      table: props.table,
    })
    ddl.value = res.ddl || '-- (empty)'
  } catch (err) {
    ddl.value = '-- ' + String(err.message || err)
  } finally {
    ddlLoading.value = false
  }
}

function exportMenu(event) {
  showContextMenu(event, [
    { label: 'CSV', action: () => exportAs('csv') },
    { label: t('制表符 TXT', 'TXT (tab)'), action: () => exportAs('txt') },
    { label: 'Excel XLSX', action: () => exportAs('xlsx') },
    { label: 'JSON', action: () => exportAs('json') },
    { label: t('SQL INSERT', 'SQL INSERT'), action: () => exportAs('sql') },
  ])
}

async function exportAs(format) {
  try {
    await postDownload(
      '/api/db/export',
      {
        assetId: props.assetId,
        database: props.database,
        schema: props.schema,
        table: props.table,
        format,
        where: where.value || undefined,
        limit: 10000,
      },
      `${props.table}.${format}`,
    )
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function display(value) {
  if (value === null || value === undefined) return null
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'object') return cellText(value)
  return String(value)
}

function isNumericCol(col) {
  return /int|decimal|numeric|float|double|real|serial/i.test(col.type || '')
}

function isNullAt(rowIndex, colIndex) {
  return rows.value[rowIndex]?.[colIndex] === null
}

function isBlobAt(rowIndex, colIndex) {
  const value = rows.value[rowIndex]?.[colIndex]
  return value && typeof value === 'object' && value.__type === 'blob'
}

function copySql() {
  navigator.clipboard?.writeText(data.value?.sql || '')
  toast(t('SQL 已复制', 'SQL copied'), 'success')
}

function copyDdl() {
  navigator.clipboard?.writeText(ddl.value || '')
  toast(t('DDL 已复制', 'DDL copied'), 'success')
}

watch(
  () => [props.table, props.database, props.schema],
  () => {
    offset.value = 0
    where.value = ''
    whereInput.value = ''
    orderBy.value = ''
    selected.value = -1
    load()
  },
)
onMounted(load)
</script>

<template>
  <div style="display: flex; flex-direction: column; flex: 1; min-height: 0">
    <div class="toolbar">
      <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="load"><RefreshCw :size="14" :class="{ 'tool-spin': loading }" /></button>
      <button class="icon-btn sm" :title="t('新增行', 'Add row')" :disabled="!editable" @click="openAddRow"><Plus :size="15" /></button>
      <button class="icon-btn sm" :title="t('删除行', 'Delete row')" :disabled="!editable || selected < 0" @click="deleteRow()"><Trash2 :size="14" /></button>
      <button class="icon-btn sm" :title="t('批量删除选中行', 'Batch delete selected')" :disabled="!editable || !checkedIndexes.length" @click="batchDelete"><Trash2 :size="14" style="color: var(--danger, #e05d5d)" /></button>
      <button class="icon-btn sm" :title="t('批量更新选中行', 'Batch update selected')" :disabled="!editable || !checkedIndexes.length" @click="openBatchUpdate"><Pencil :size="14" /></button>
      <div class="sep" />
      <button class="icon-btn sm" :title="t('设计表结构', 'Design table')" :disabled="readOnly" @click="schemaOpen = true"><Table2 :size="14" /></button>
      <button class="icon-btn sm" :title="t('表同步', 'Table sync')" :disabled="readOnly" @click="syncOpen = true"><ArrowRightLeft :size="14" /></button>
      <div class="sep" />
      <div class="searchbox" style="width: 250px">
        <span class="icon"><Filter :size="13" /></span>
        <input
          v-model="whereInput"
          class="input"
          style="height: 26px"
          :placeholder="t('WHERE 条件，回车应用', 'WHERE clause, press Enter')"
          @keyup.enter="applyFilter"
        />
      </div>
      <button class="btn ghost sm" @click="applyFilter">{{ t('筛选', 'Filter') }}</button>
      <div class="sep" />
      <button class="icon-btn sm" :title="t('导出', 'Export')" @click="exportMenu"><Download :size="14" /></button>
      <button class="icon-btn sm" :title="t('查看 DDL', 'DDL')" @click="openDdl"><FileCode2 :size="14" /></button>
      <button class="icon-btn sm" :title="t('复制 SQL', 'Copy SQL')" @click="copySql"><Copy :size="14" /></button>
      <span class="grow" style="flex: 1" />
      <span v-if="!editable" class="badge">{{ readOnly ? t('只读视图', 'Read-only view') : t('无主键 · 只读', 'No PK · read-only') }}</span>
      <span v-if="saving" class="badge accent"><Loader2 :size="11" class="tool-spin" /> {{ t('保存中', 'Saving') }}</span>
    </div>

    <div v-if="error" class="alert error" style="margin: 10px">{{ error }}</div>

    <div class="grid-wrap" :class="{ hidden: !rows.length && !loading }">
      <table class="grid">
        <thead>
          <tr>
            <th style="width: 30px; text-align: center"><input type="checkbox" :checked="allChecked" @click="toggleAll" /></th>
            <th class="rownum">#</th>
            <th v-for="(col, colIndex) in columns" :key="col.name" :class="{ sorted: orderBy === col.name }" @click="toggleSort(col)">
              <div class="th-inner">
                <span class="th-name">{{ col.name }}</span>
                <span class="th-type">{{ col.type }}</span>
                <span v-if="col.primary" class="badge" style="font-size: 9px; padding: 0 3px">PK</span>
                <span class="sort">
                  <ArrowUp v-if="orderBy === col.name && orderDir === 'ASC'" :size="11" />
                  <ArrowDown v-else-if="orderBy === col.name && orderDir === 'DESC'" :size="11" />
                </span>
              </div>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, rowIndex) in rows" :key="rowIndex" :class="{ selected: selected === rowIndex }" @click="selected = rowIndex">
            <td style="text-align: center" @click.stop><input type="checkbox" :checked="!!checked[rowIndex]" @click.stop="toggleOne(rowIndex)" /></td>
            <td class="rownum">{{ offset + rowIndex + 1 }}</td>
            <td
              v-for="(col, colIndex) in columns"
              :key="col.name"
              :class="{ num: isNumericCol(col), null: isNullAt(rowIndex, colIndex), blob: isBlobAt(rowIndex, colIndex) }"
              :title="isBlobAt(rowIndex, colIndex) ? t('点击查看 Blob 内容', 'Click to view blob') : ''"
              @dblclick="isBlobAt(rowIndex, colIndex) ? openBlob(rowIndex, colIndex) : startEdit(rowIndex, colIndex)"
              @contextmenu="cellMenu($event, rowIndex, colIndex)"
            >
              <Binary v-if="isBlobAt(rowIndex, colIndex)" :size="12" style="margin-right: 3px; vertical-align: -1px" />
              <input
                v-if="isEditing(rowIndex, colIndex)"
                v-model="editing.value"
                class="input"
                style="height: 23px; padding: 0 4px; font-size: 12px; font-family: var(--mono)"
                @keyup.enter="commitEdit"
                @keyup.esc="editing = null"
                @blur="commitEdit"
                @click.stop
                autofocus
              />
              <template v-else>{{ display(row[colIndex]) === null ? 'NULL' : display(row[colIndex]) }}</template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="!rows.length && !loading && !error" class="grid-empty">
      {{ t('表中没有数据', 'No rows in this table') }}
    </div>
    <div v-if="loading" class="center-hint" style="padding: 30px"><Loader2 :size="18" class="tool-spin" /></div>

    <div class="statusbar">
      <span class="sql-preview">{{ data?.sql || '' }}</span>
      <span class="grow" />
      <span>{{ t('共', 'Total') }} {{ total ?? '?' }} {{ t('行', 'rows') }}</span>
      <span class="pager">
        <button class="icon-btn sm" :disabled="offset === 0" @click="goPage(-1)"><ChevronLeft :size="13" /></button>
        <input class="input" :value="page" @keyup.enter="jumpPage" />
        <span class="muted">/ {{ pageCount }}</span>
        <button class="icon-btn sm" :disabled="!total || offset + limit >= total" @click="goPage(1)"><ChevronRight :size="13" /></button>
      </span>
      <select class="select" style="height: 22px; width: 78px; padding: 0 20px 0 6px" :value="limit" @change="setLimit($event.target.value)">
        <option v-for="n in [50, 100, 200, 500, 1000]" :key="n" :value="n">{{ n }}/{{ t('页', 'page') }}</option>
      </select>
      <span>{{ data?.elapsed || 0 }} ms</span>
    </div>

    <div v-if="showDdl" class="modal-mask" @mousedown.self="showDdl = false">
      <div class="modal" style="width: 720px; max-height: 80vh">
        <div class="modal-head">
          <FileCode2 :size="15" />
          <span class="modal-title mono">{{ table }} · DDL</span>
          <button class="icon-btn sm" @click="showDdl = false"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div v-if="ddlLoading" class="center-hint" style="padding: 30px"><Loader2 :size="18" class="tool-spin" /></div>
          <pre v-else class="md" style="font-family: var(--mono); font-size: 12px; white-space: pre-wrap; margin: 0">{{ ddl }}</pre>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="copyDdl">{{ t('复制', 'Copy') }}</button>
          <button class="btn primary" @click="showDdl = false">{{ t('关闭', 'Close') }}</button>
        </div>
      </div>
    </div>

    <div v-if="addOpen" class="modal-mask" @mousedown.self="addOpen = false">
      <div class="modal" style="width: 620px; max-height: 80vh">
        <div class="modal-head">
          <Plus :size="15" />
          <span class="modal-title">{{ t('新增行', 'Insert row') }} · {{ table }}</span>
          <button class="icon-btn sm" @click="addOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div v-for="col in columns.filter((c) => !c.extra?.includes('auto_increment'))" :key="col.name" class="field" style="margin-bottom: 10px">
            <label style="display: flex; align-items: center; gap: 6px">
              <span>{{ col.name }}</span>
              <span class="tiny mono">{{ col.type }}</span>
              <span class="grow" />
              <span class="tiny">{{ t('NULL', 'NULL') }}</span>
              <span class="checkbox" :class="{ on: newRowNull[col.name] }" @click="newRowNull[col.name] = !newRowNull[col.name]">
                <span v-if="newRowNull[col.name]">✓</span>
              </span>
            </label>
            <input v-model="newRow[col.name]" class="input" :disabled="newRowNull[col.name]" :placeholder="col.default ?? ''" />
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="addOpen = false">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" :disabled="saving" @click="insertRow">{{ t('插入', 'Insert') }}</button>
        </div>
      </div>
    </div>

    <BlobDialog
      v-if="blobTarget"
      :asset-id="assetId"
      :table="blobTarget.table"
      :column="blobTarget.column"
      :row-keys="blobTarget.rowKeys"
      :database="blobTarget.database"
      :schema="blobTarget.schema"
      @close="blobTarget = null"
    />

    <DbSchemaDialog
      v-if="schemaOpen"
      :asset-id="assetId"
      :database="database"
      :schema="schema"
      :table="table"
      :dialect="dialect"
      @close="schemaOpen = false"
      @saved="schemaOpen = false; load()"
    />

    <DbSyncDialog
      v-if="syncOpen"
      :asset-id="assetId"
      :database="database"
      :schema="schema"
      :table="table"
      :dialect="dialect"
      @close="syncOpen = false"
    />

    <div v-if="batchOpen" class="modal-mask" @mousedown.self="batchOpen = false">
      <div class="modal" style="width: 420px">
        <div class="modal-head">
          <Pencil :size="15" />
          <span class="modal-title">{{ t('批量更新', 'Batch update') }} · {{ checkedIndexes.length }} {{ t('行', 'rows') }}</span>
          <button class="icon-btn sm" @click="batchOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="field" style="margin-bottom: 10px">
            <label>{{ t('字段', 'Column') }}</label>
            <select v-model="batchColumn" class="select">
              <option v-for="col in columns" :key="col.name" :value="col.name">{{ col.name }} · {{ col.type }}</option>
            </select>
          </div>
          <div class="field">
            <label>{{ t('新值（留空写 NULL）', 'New value (empty = NULL)') }}</label>
            <input v-model="batchValue" class="input mono" />
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="batchOpen = false">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" :disabled="saving" @click="applyBatchUpdate">{{ t('应用', 'Apply') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
