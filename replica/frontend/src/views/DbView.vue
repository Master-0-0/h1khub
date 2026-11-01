<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  Activity, ChevronDown, ChevronRight, Database, Eye, FileUp, FunctionSquare, Loader2, Plus,
  RefreshCw, Save, Search, Table2, TerminalSquare, Trash2, X,
} from 'lucide-vue-next'
import { api } from '../lib/api'
import { assetMap, state, t, toast } from '../lib/store'
import { confirmDialog, promptDialog, showContextMenu } from '../lib/ui'
import DbTable from './DbTable.vue'
import DbQuery from './DbQuery.vue'
import DbImportDialog from '../components/DbImportDialog.vue'
import DbRealtimeDialog from '../components/DbRealtimeDialog.vue'
import DbSchemaDialog from '../components/DbSchemaDialog.vue'

const props = defineProps({ tab: { type: Object, required: true } })

const loading = ref(true)
const error = ref('')
const dialect = ref('')
const version = ref('')
const databases = ref([])
const schemas = ref([])
const currentDb = ref('')
const objectsByKey = reactive({})
const loadingKeys = reactive({})
const search = ref('')
const expanded = reactive({})
const innerTabs = ref([])
const activeInnerId = ref(null)
const objectTabSeq = ref(0)
const importOpen = ref(false)
const realtimeOpen = ref(false)
const createTableOpen = ref(false)
const objectEditor = ref(null)
const objectBusy = ref(false)

const asset = computed(() => assetMap.value.get(props.tab.assetId))
const isPostgres = computed(() => dialect.value === 'postgresql')
const isSqlite = computed(() => dialect.value === 'sqlite')
// PostgreSQL and DuckDB both expose named schemas; DuckDB's default is "main".
const isSchemaTree = computed(() => ['postgresql', 'duckdb'].includes(dialect.value))
const defaultSchema = computed(() => (dialect.value === 'duckdb' ? 'main' : 'public'))

const sections = computed(() => {
  if (isSchemaTree.value) {
    return schemas.value.map((schema) => ({
      key: `pg:${schema}`,
      database: currentDb.value,
      schema,
      label: schema,
      objects: objectsByKey[`${currentDb.value}|${schema}`] || null,
    }))
  }
  return [
    {
      key: `db:${currentDb.value}`,
      database: currentDb.value,
      schema: '',
      label: currentDb.value,
      objects: objectsByKey[currentDb.value] || null,
    },
  ]
})

const activeInner = computed(() => innerTabs.value.find((t2) => t2.id === activeInnerId.value) || null)

async function loadOverview() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.post('/api/db/overview', { assetId: props.tab.assetId })
    dialect.value = res.dialect
    version.value = res.version
    databases.value = res.databases || []
    schemas.value = res.schemas || []
    const cfg = asset.value?.config || {}
    // Prefer the configured database, then the file the asset points at (so an
    // attached DuckDB file does not shadow the primary one), then the first
    // non-system catalog.
    const systemDbs = ['information_schema', 'mysql', 'performance_schema', 'sys', 'postgres', 'system', 'temp']
    const primary = (res.databases || []).find((d) => d.file && cfg.file && d.file === cfg.file)
    currentDb.value = cfg.database
      || primary?.name
      || (res.databases || []).find((d) => !systemDbs.includes(d.name))?.name
      || res.databases?.[0]?.name
      || ''
    await Promise.all(sections.value.map((section) => loadSection(section)))
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

async function loadSection(section) {
  const key = isSchemaTree.value ? `${currentDb.value}|${section.schema}` : currentDb.value
  if (!key || loadingKeys[key]) return
  loadingKeys[key] = true
  try {
    const res = await api.post('/api/db/objects', {
      assetId: props.tab.assetId,
      database: currentDb.value,
      schema: isSchemaTree.value ? section.schema : undefined,
    })
    objectsByKey[key] = res
  } catch (err) {
    objectsByKey[key] = { error: String(err.message || err), tables: [], views: [], routines: [] }
  } finally {
    loadingKeys[key] = false
  }
}

function sectionKey(section) {
  return isSchemaTree.value ? `${currentDb.value}|${section.schema}` : currentDb.value
}

function isOpen(key, fallback = false) {
  return expanded[key] ?? fallback
}

function toggle(key) {
  expanded[key] = !isOpen(key)
}

async function changeDatabase(name) {
  currentDb.value = name
  for (const section of sections.value) await loadSection(section)
}

function openTable(section, name, isView = false) {
  const key = `table:${section.database}:${section.schema}:${name}`
  const existing = innerTabs.value.find((it) => it.key === key)
  if (existing) {
    activeInnerId.value = existing.id
    return
  }
  const id = `it_${++objectTabSeq.value}`
  innerTabs.value.push({
    id,
    key,
    kind: 'table',
    title: name,
    database: section.database,
    schema: section.schema,
    table: name,
    readOnly: isView,
  })
  activeInnerId.value = id
}

function openQuery(sql = '', database = currentDb.value, name = '') {
  const id = `it_${++objectTabSeq.value}`
  const count = innerTabs.value.filter((x) => x.kind === 'query').length + 1
  innerTabs.value.push({
    id,
    key: `query:${id}`,
    kind: 'query',
    title: name || `${t('查询', 'Query')} ${count}`,
    sql,
    database,
  })
  activeInnerId.value = id
}

function openSavedQuery(asset) {
  openQuery(asset.config?.sql || '', asset.config?.database || currentDb.value, asset.name)
}

function closeInner(id) {
  const idx = innerTabs.value.findIndex((x) => x.id === id)
  if (idx < 0) return
  innerTabs.value.splice(idx, 1)
  if (activeInnerId.value === id) {
    activeInnerId.value = innerTabs.value[Math.min(idx, innerTabs.value.length - 1)]?.id || null
  }
}

const savedQueries = computed(() =>
  state.assets.filter((a) => a.typeName === 'DBQuery' && (a.config?.assetId === props.tab.assetId || !a.config?.assetId)),
)

const filteredSections = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return sections.value
  return sections.value.map((section) => {
    const objs = section.objects || {}
    return {
      ...section,
      flat: true,
      matchTables: (objs.tables || []).filter((tb) => tb.name.toLowerCase().includes(q)),
      matchViews: (objs.views || []).filter((vw) => vw.name.toLowerCase().includes(q)),
    }
  })
})

function tableCount(section) {
  return section.objects?.tables?.length || 0
}
function viewCount(section) {
  return section.objects?.views?.length || 0
}
function routineCount(section) {
  return section.objects?.routines?.length || 0
}

const stats = computed(() => {
  const all = Object.values(objectsByKey)
  const tables = all.reduce((n, o) => n + (o?.tables?.length || 0), 0)
  const views = all.reduce((n, o) => n + (o?.views?.length || 0), 0)
  return { tables, views }
})

async function openObjectEditor(section, name, kind, routineType = 'PROCEDURE') {
  objectEditor.value = { open: true, name, kind, routineType, database: section.database, schema: section.schema, sql: '', loading: true }
  try {
    const res = await api.post('/api/db/object-def', {
      assetId: props.tab.assetId,
      database: section.database,
      schema: section.schema,
      name,
      kind,
      routineType,
    })
    objectEditor.value.sql = res.definition || ''
    objectEditor.value.loading = false
  } catch (err) {
    objectEditor.value.loading = false
    toast(String(err.message || err), 'error')
  }
}

async function saveObject() {
  const obj = objectEditor.value
  if (!obj) return
  objectBusy.value = true
  try {
    await api.post('/api/db/object-save', {
      assetId: props.tab.assetId,
      database: obj.database,
      schema: obj.schema,
      name: obj.name,
      kind: obj.kind,
      routineType: obj.routineType,
      sql: obj.sql,
    })
    toast(t('定义已保存', 'Definition saved'), 'success')
    objectEditor.value = null
    Object.keys(objectsByKey).forEach((k) => delete objectsByKey[k])
    await loadOverview()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    objectBusy.value = false
  }
}

async function dropObject() {
  const obj = objectEditor.value
  if (!obj) return
  const ok = await confirmDialog({
    title: t('删除对象', 'Drop object'),
    message: t(`确定删除 ${obj.kind}「${obj.name}」吗？`, `Drop ${obj.kind} "${obj.name}"?`),
    danger: true,
    okText: t('删除', 'Drop'),
  })
  if (!ok) return
  objectBusy.value = true
  try {
    await api.post('/api/db/object-drop', {
      assetId: props.tab.assetId,
      database: obj.database,
      schema: obj.schema,
      name: obj.name,
      kind: obj.kind,
      routineType: obj.routineType,
    })
    toast(t('已删除', 'Dropped'), 'success')
    objectEditor.value = null
    Object.keys(objectsByKey).forEach((k) => delete objectsByKey[k])
    await loadOverview()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    objectBusy.value = false
  }
}

function objectMenu(event, section, name, kind, routineType = 'PROCEDURE') {
  showContextMenu(event, [
    { label: t('查看 / 编辑定义', 'View / edit definition'), action: () => openObjectEditor(section, name, kind, routineType) },
    { label: t('复制名称', 'Copy name'), action: () => navigator.clipboard?.writeText(name) },
    { sep: true },
    { label: t('删除', 'Drop'), danger: true, action: () => { openObjectEditor(section, name, kind, routineType); setTimeout(dropObject, 350) } },
  ])
}

async function afterImport() {
  objectsByKey && Object.keys(objectsByKey).forEach((k) => delete objectsByKey[k])
  await loadOverview()
  toast(t('导入完成，对象树已刷新', 'Import finished; tree refreshed'), 'success')
}

async function afterCreateTable(payload) {
  createTableOpen.value = false
  objectsByKey && Object.keys(objectsByKey).forEach((k) => delete objectsByKey[k])
  await loadOverview()
  const tableName = payload?.table
  if (tableName) {
    const section = sections.value.find((s2) => isSchemaTree.value ? s2.schema === defaultSchema.value : true) || sections.value[0]
    if (section) openTable(section, tableName, false)
  }
}

async function reloadAll() {
  objectsByKey && Object.keys(objectsByKey).forEach((k) => delete objectsByKey[k])
  await loadOverview()
  toast(t('已刷新对象树', 'Object tree refreshed'), 'success')
}

async function attachDatabase() {
  const path = await promptDialog({
    title: t('附加 DuckDB 数据库', 'Attach DuckDB database'),
    message: t('数据库文件路径', 'Database file path'),
    placeholder: '/home/user/other.duckdb',
  })
  if (!path) return
  try {
    const res = await api.post('/api/db/attach', { assetId: props.tab.assetId, path })
    const names = (res.attached || []).map((a) => a.alias).join(', ')
    toast(t('已附加：', 'Attached: ') + names, 'success')
    await reloadAll()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

watch(
  () => props.tab.assetId,
  () => {
    innerTabs.value = []
    activeInnerId.value = null
    Object.keys(objectsByKey).forEach((k) => delete objectsByKey[k])
    loadOverview()
  },
)

onMounted(loadOverview)
</script>

<template>
  <div class="view">
    <aside class="asset-panel" style="width: 270px; background: var(--bg-panel)">
      <div class="panel-head" style="height: 42px; gap: 4px">
        <Database :size="15" style="color: var(--accent)" />
        <select v-if="databases.length > 1" class="select" style="height: 26px; flex: 1; font-size: 12px" :value="currentDb" @change="changeDatabase($event.target.value)">
          <option v-for="db in databases" :key="db.name" :value="db.name">{{ db.name }}</option>
        </select>
        <span v-else class="panel-title" style="font-size: 12.5px">{{ currentDb || asset?.name }}</span>
        <button v-if="dialect === 'duckdb'" class="icon-btn sm" :title="t('附加数据库文件', 'Attach database file')" @click="attachDatabase"><Plus :size="13" /></button>
        <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="reloadAll"><RefreshCw :size="13" :class="{ 'tool-spin': loading }" /></button>
      </div>
      <div class="panel-search" style="padding-top: 2px">
        <div class="searchbox">
          <span class="icon"><Search :size="13" /></span>
          <input v-model="search" class="input" style="height: 26px" :placeholder="t('筛选表 / 视图', 'Filter tables / views')" />
        </div>
      </div>
      <div class="tree" style="padding-top: 0">
        <div v-if="loading" class="center-hint" style="padding: 30px"><Loader2 :size="16" class="tool-spin" /></div>
        <template v-else>
          <template v-for="section in filteredSections" :key="section.key">
            <div v-if="isSchemaTree && !search" class="tree-row" style="height: 26px" @click="toggle('schema:' + section.key)">
              <span class="tree-caret">
                <ChevronDown v-if="isOpen('schema:' + section.key, true)" :size="12" />
                <ChevronRight v-else :size="12" />
              </span>
              <span class="tree-icon" style="color: var(--text-3)"><Database :size="13" /></span>
              <span class="tree-name" style="font-weight: 500">{{ section.label }}</span>
            </div>
            <template v-if="search || !isSchemaTree || isOpen('schema:' + section.key, true)">
              <!-- tables -->
              <div class="tree-row" :style="{ paddingLeft: (isSchemaTree ? 18 : 6) + 'px' }" @click="toggle('tables:' + section.key)">
                <span class="tree-caret">
                  <ChevronDown v-if="isOpen('tables:' + section.key, true)" :size="12" />
                  <ChevronRight v-else :size="12" />
                </span>
                <span class="tree-icon" style="color: #5aa9e6"><Table2 :size="13" /></span>
                <span class="tree-name">{{ t('表', 'Tables') }}</span>
                <span class="tree-badge">{{ tableCount(section) }}</span>
              </div>
              <template v-if="isOpen('tables:' + section.key, true)">
                <div
                  v-for="tb in (section.flat ? section.matchTables : section.objects?.tables) || []"
                  :key="tb.name"
                  class="tree-row"
                  :style="{ paddingLeft: (isSchemaTree ? 32 : 20) + 'px' }"
                  :title="tb.name"
                  @click="openTable(section, tb.name, false)"
                >
                  <span class="tree-caret" />
                  <span class="tree-icon" style="color: #7ec699"><Table2 :size="12" /></span>
                  <span class="tree-name">{{ tb.name }}</span>
                  <span v-if="tb.rows" class="tree-badge">{{ tb.rows }}</span>
                </div>
                <div v-if="search && !section.matchTables?.length" class="tiny" style="padding: 3px 32px">{{ t('无匹配表', 'No matching tables') }}</div>
              </template>

              <!-- views -->
              <div class="tree-row" :style="{ paddingLeft: (isSchemaTree ? 18 : 6) + 'px' }" @click="toggle('views:' + section.key)">
                <span class="tree-caret">
                  <ChevronDown v-if="isOpen('views:' + section.key, true)" :size="12" />
                  <ChevronRight v-else :size="12" />
                </span>
                <span class="tree-icon" style="color: #b07cff"><Eye :size="13" /></span>
                <span class="tree-name">{{ t('视图', 'Views') }}</span>
                <span class="tree-badge">{{ viewCount(section) }}</span>
              </div>
              <template v-if="isOpen('views:' + section.key, true)">
                <div
                  v-for="vw in (section.flat ? section.matchViews : section.objects?.views) || []"
                  :key="vw.name"
                  class="tree-row"
                  :style="{ paddingLeft: (isSchemaTree ? 32 : 20) + 'px' }"
                  @click="openTable(section, vw.name, true)"
                  @contextmenu="objectMenu($event, section, vw.name, 'view')"
                >
                  <span class="tree-caret" />
                  <span class="tree-icon" style="color: #b07cff"><Eye :size="12" /></span>
                  <span class="tree-name">{{ vw.name }}</span>
                </div>
              </template>

              <!-- routines -->
              <div v-if="!search" class="tree-row" :style="{ paddingLeft: (isSchemaTree ? 18 : 6) + 'px' }" @click="toggle('routines:' + section.key)">
                <span class="tree-caret">
                  <ChevronDown v-if="isOpen('routines:' + section.key)" :size="12" />
                  <ChevronRight v-else :size="12" />
                </span>
                <span class="tree-icon" style="color: #e0a340"><FunctionSquare :size="13" /></span>
                <span class="tree-name">{{ t('存储过程 / 函数', 'Routines') }}</span>
                <span class="tree-badge">{{ routineCount(section) }}</span>
              </div>
              <template v-if="!search && isOpen('routines:' + section.key)">
                <div v-for="rt in section.objects?.routines || []" :key="rt.name" class="tree-row" :style="{ paddingLeft: (isSchemaTree ? 32 : 20) + 'px' }" @contextmenu="objectMenu($event, section, rt.name, 'routine', rt.kind)">
                  <span class="tree-caret" />
                  <span class="tree-icon" style="color: #e0a340"><FunctionSquare :size="12" /></span>
                  <span class="tree-name">{{ rt.name }}</span>
                  <span class="tree-badge">{{ rt.kind }}</span>
                </div>
              </template>
            </template>
          </template>

          <div v-if="savedQueries.length" class="tree-row" style="height: 26px; padding-left: 6px" @click="toggle('queries')">
            <span class="tree-caret">
              <ChevronDown v-if="isOpen('queries', true)" :size="12" />
              <ChevronRight v-else :size="12" />
            </span>
            <span class="tree-icon" style="color: #7ec699"><TerminalSquare :size="13" /></span>
            <span class="tree-name">{{ t('查询', 'Queries') }}</span>
            <span class="tree-badge">{{ savedQueries.length }}</span>
          </div>
          <template v-if="isOpen('queries', true)">
            <div v-for="q in savedQueries" :key="q.id" class="tree-row" style="padding-left: 20px" @click="openSavedQuery(q)">
              <span class="tree-caret" />
              <span class="tree-icon" style="color: #7ec699"><TerminalSquare :size="12" /></span>
              <span class="tree-name">{{ q.name }}</span>
            </div>
          </template>
        </template>
      </div>
      <div class="panel-foot" style="flex-wrap: wrap; gap: 4px">
        <button class="btn ghost sm" style="flex: 1; justify-content: flex-start" @click="openQuery()"><Plus :size="13" /> {{ t('新建查询', 'New query') }}</button>
        <button class="btn ghost sm" :title="t('新建表', 'Create table')" @click="createTableOpen = true"><Table2 :size="13" /></button>
        <button class="btn ghost sm" :title="t('导入 SQL', 'Import SQL')" @click="importOpen = true"><FileUp :size="13" /></button>
        <button class="btn ghost sm" :title="t('实时信息', 'Realtime info')" @click="realtimeOpen = true"><Activity :size="13" /></button>
        <button class="btn ghost sm" @click="reloadAll">{{ t('刷新', 'Refresh') }}</button>
      </div>
    </aside>

    <div class="workspace" style="flex: 1; min-width: 0">
      <div v-if="innerTabs.length" class="subtabs">
        <div
          v-for="it in innerTabs"
          :key="it.id"
          class="subtab"
          :class="{ active: it.id === activeInnerId }"
          @click="activeInnerId = it.id"
        >
          <Table2 v-if="it.kind === 'table'" :size="12" />
          <TerminalSquare v-else :size="12" />
          <span>{{ it.title }}</span>
          <span v-if="it.readOnly" class="badge" style="font-size: 9px; padding: 0 3px">{{ t('视图', 'view') }}</span>
          <span class="close" @click.stop="closeInner(it.id)"><X :size="11" /></span>
        </div>
        <div style="display: flex; align-items: center; gap: 2px; padding: 0 6px">
          <button class="icon-btn sm" :title="t('新建查询', 'New query')" @click="openQuery()"><Plus :size="14" /></button>
        </div>
      </div>

      <template v-if="activeInner">
        <DbTable
          v-if="activeInner.kind === 'table'"
          :key="activeInner.id"
          :asset-id="props.tab.assetId"
          :database="activeInner.database"
          :schema="activeInner.schema"
          :table="activeInner.table"
          :read-only="activeInner.readOnly"
          :dialect="dialect"
        />
        <DbQuery
          v-else
          :key="activeInner.id"
          :asset-id="props.tab.assetId"
          :database="activeInner.database"
          :dialect="dialect"
          :initial-sql="activeInner.sql"
          :name="activeInner.title"
        />
      </template>

      <div v-else class="empty-state">
        <div class="icon-wrap"><Database :size="28" /></div>
        <div class="big">{{ asset?.name || t('数据库', 'Database') }}</div>
        <div class="muted" style="font-size: 12px">{{ version }}</div>
        <div class="row" style="gap: 18px; margin-top: 6px; color: var(--text-2); font-size: 12px">
          <span>{{ t('表', 'Tables') }} <b>{{ stats.tables }}</b></span>
          <span>{{ t('视图', 'Views') }} <b>{{ stats.views }}</b></span>
          <span>{{ t('数据库', 'Databases') }} <b>{{ databases.length }}</b></span>
        </div>
        <div class="row" style="gap: 8px; margin-top: 10px">
          <button class="btn primary sm" @click="openQuery()"><Plus :size="13" /> {{ t('新建 SQL 查询', 'New SQL query') }}</button>
          <button class="btn sm" @click="reloadAll"><RefreshCw :size="13" /> {{ t('刷新对象', 'Refresh') }}</button>
        </div>
        <div class="tiny" style="margin-top: 14px">{{ t('在左侧对象树中点击表名即可浏览数据', 'Click a table in the tree to browse its data') }}</div>
      </div>

      <div v-if="error" class="alert error" style="margin: 12px">{{ error }}</div>
    </div>

    <div v-if="objectEditor" class="modal-mask" @mousedown.self="objectEditor = null">
      <div class="modal" style="width: 820px; height: 80vh; display: flex; flex-direction: column">
        <div class="modal-head">
          <FunctionSquare :size="15" />
          <span class="modal-title">{{ objectEditor.kind === 'view' ? t('视图定义', 'View definition') : t('例程定义', 'Routine definition') }} · <span class="mono">{{ objectEditor.name }}</span></span>
          <span class="badge">{{ objectEditor.routineType }}</span>
          <span class="grow" style="flex: 1" />
          <button class="icon-btn sm" @click="objectEditor = null"><X :size="14" /></button>
        </div>
        <div class="modal-body" style="flex: 1; min-height: 0; display: flex; flex-direction: column">
          <div v-if="objectEditor.loading" class="center-hint" style="padding: 30px"><Loader2 :size="18" class="tool-spin" /></div>
          <textarea v-else v-model="objectEditor.sql" class="textarea mono" style="flex: 1; resize: none; font-size: 12px; background: var(--bg)" spellcheck="false" />
          <div class="tiny" style="margin-top: 6px">{{ t('保存会重建视图/例程；删除不可撤销。', 'Saving recreates the object; dropping cannot be undone.') }}</div>
        </div>
        <div class="modal-foot">
          <button class="btn danger" :disabled="objectBusy" @click="dropObject"><Trash2 :size="13" /> {{ t('删除', 'Drop') }}</button>
          <span class="grow" />
          <button class="btn" @click="objectEditor = null">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" :disabled="objectBusy || objectEditor.loading" @click="saveObject"><Save :size="13" /> {{ t('保存定义', 'Save') }}</button>
        </div>
      </div>
    </div>

    <DbImportDialog
      v-if="importOpen"
      :asset-id="props.tab.assetId"
      :database="currentDb"
      :dialect="dialect"
      @close="importOpen = false"
      @imported="afterImport"
    />
    <DbRealtimeDialog
      v-if="realtimeOpen"
      :asset-id="props.tab.assetId"
      :database="currentDb"
      @close="realtimeOpen = false"
    />
    <DbSchemaDialog
      v-if="createTableOpen"
      :asset-id="props.tab.assetId"
      :database="currentDb"
      :schema="defaultSchema"
      table=""
      :dialect="dialect"
      @close="createTableOpen = false"
      @saved="afterCreateTable"
    />
  </div>
</template>
