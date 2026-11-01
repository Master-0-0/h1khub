<script setup>
import { computed, ref } from 'vue'
import { ArrowRightLeft, Database, Loader2, Play, X } from 'lucide-vue-next'
import { api } from '../lib/api'
import { state, t, toast } from '../lib/store'
import { confirmDialog } from '../lib/ui'

const props = defineProps({
  assetId: { type: String, required: true },
  database: { type: String, default: '' },
  schema: { type: String, default: '' },
  table: { type: String, required: true },
  dialect: { type: String, default: 'mysql' },
})
const emit = defineEmits(['close'])

const dbAssets = computed(() => state.assets.filter((a) => a.typeName === 'DB'))
const targetAssetId = ref(props.assetId)
const targetDatabase = ref(props.database)
const targetSchema = ref(props.schema)
const targetTable = ref(`${props.table}_copy`)
const mode = ref('append')
const limit = ref(10000)
const tab = ref('schema')
const loading = ref('')
const error = ref('')
const diff = ref(null)
const dataPreview = ref(null)
const result = ref(null)
const open = ref(true)

const source = computed(() => ({ assetId: props.assetId, database: props.database, schema: props.schema, table: props.table }))
const target = computed(() => ({ assetId: targetAssetId.value, database: targetDatabase.value, schema: targetSchema.value, table: targetTable.value.trim() }))

async function action(kind) {
  if (!target.value.table) {
    toast(t('请填写目标表名', 'Target table is required'), 'info')
    return
  }
  loading.value = kind
  error.value = ''
  result.value = null
  try {
    if (kind === 'diff') {
      diff.value = await api.post('/api/db/sync/diff', { source: source.value, target: target.value })
    } else if (kind === 'apply') {
      const ok = await confirmDialog({
        title: t('应用结构变更', 'Apply schema changes'),
        message: t('将在目标表执行结构差异语句，可能包含删除字段/索引。', 'This will apply the schema diff to the target table.'),
        danger: true,
        okText: t('执行', 'Apply'),
      })
      if (!ok) return
      result.value = await api.post('/api/db/sync/apply', { source: source.value, target: target.value })
      toast(t('结构同步完成', 'Schema sync finished'), 'success')
    } else if (kind === 'preview') {
      dataPreview.value = await api.post('/api/db/sync/data/preview', { source: source.value, target: target.value })
    } else if (kind === 'copy') {
      const ok = await confirmDialog({
        title: t('复制数据', 'Copy data'),
        message: mode.value === 'replace'
          ? t('replace 会先清空目标表再插入。', 'replace clears the target table first.')
          : t('将向目标表追加数据。', 'Rows will be appended to the target table.'),
        danger: mode.value === 'replace',
        okText: t('执行', 'Run'),
      })
      if (!ok) return
      result.value = await api.post('/api/db/sync/data', { source: source.value, target: target.value, mode: mode.value, limit: Number(limit.value) })
      toast(t('数据同步完成', 'Data sync finished'), 'success')
    }
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = ''
  }
}

function close() {
  open.value = false
  emit('close')
}
</script>

<template>
  <div v-if="open" class="modal-mask" @mousedown.self="close">
    <div class="modal" style="width: 860px; max-height: 88vh; display: flex; flex-direction: column">
      <div class="modal-head">
        <ArrowRightLeft :size="15" />
        <span class="modal-title">{{ t('表同步', 'Table sync') }}</span>
        <span class="badge mono">{{ props.table }}</span>
        <span class="grow" style="flex: 1" />
        <button class="icon-btn sm" @click="close"><X :size="14" /></button>
      </div>
      <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
        <div class="row" style="gap: 12px; align-items: flex-end; flex-wrap: wrap">
          <div class="field" style="min-width: 180px">
            <label>{{ t('目标数据库资产', 'Target DB asset') }}</label>
            <select v-model="targetAssetId" class="select">
              <option v-for="a in dbAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
            </select>
          </div>
          <div class="field" style="width: 120px"><label>{{ t('库', 'Database') }}</label><input v-model="targetDatabase" class="input mono" /></div>
          <div v-if="dialect === 'postgresql'" class="field" style="width: 110px"><label>Schema</label><input v-model="targetSchema" class="input mono" /></div>
          <div class="field" style="flex: 1; min-width: 160px"><label>{{ t('目标表', 'Target table') }}</label><input v-model="targetTable" class="input mono" /></div>
        </div>
        <div class="row" style="gap: 6px; margin-top: 12px">
          <button class="btn sm" :class="{ primary: tab === 'schema' }" @click="tab = 'schema'">{{ t('结构', 'Schema') }}</button>
          <button class="btn sm" :class="{ primary: tab === 'data' }" @click="tab = 'data'">{{ t('数据', 'Data') }}</button>
        </div>

        <div v-if="tab === 'schema'" style="margin-top: 10px">
          <div class="row" style="gap: 8px">
            <button class="btn" :disabled="!!loading" @click="action('diff')">
              <Loader2 v-if="loading === 'diff'" :size="13" class="tool-spin" /><Database :size="13" /> {{ t('预览差异', 'Preview diff') }}
            </button>
            <button class="btn primary" :disabled="!!loading || !diff?.statements?.length" @click="action('apply')">
              <Loader2 v-if="loading === 'apply'" :size="13" class="tool-spin" /><Play :size="13" /> {{ t('应用结构', 'Apply schema') }}
            </button>
          </div>
          <div v-if="diff" style="margin-top: 10px">
            <div class="alert info" style="font-size: 12px">
              {{ t('源列', 'source cols') }} {{ diff.source?.columns }} · {{ t('目标列', 'target cols') }} {{ diff.target?.columns }} ·
              {{ t('变更', 'changes') }} {{ diff.changes?.length || 0 }}
            </div>
            <div v-if="diff.changes?.length" style="margin-top: 8px; border: 1px solid var(--border); border-radius: 6px; max-height: 180px; overflow: auto">
              <table class="grid">
                <thead><tr><th style="width: 130px">{{ t('类型', 'Kind') }}</th><th>{{ t('对象', 'Object') }}</th></tr></thead>
                <tbody>
                  <tr v-for="(c, i) in diff.changes" :key="i"><td class="tiny">{{ c.kind }}</td><td class="mono tiny">{{ c.column || c.index || c.name || c.table }}</td></tr>
                </tbody>
              </table>
            </div>
            <pre v-if="diff.statements?.length" class="mono" style="margin-top: 8px; padding: 10px; background: var(--bg-deep); border-radius: 6px; font-size: 11px; white-space: pre-wrap">{{ diff.statements.join('\n') }}</pre>
            <div v-for="(w, i) in diff.warnings || []" :key="i" class="alert warn" style="margin-top: 6px">{{ w }}</div>
          </div>
        </div>

        <div v-else style="margin-top: 10px">
          <div class="row" style="gap: 8px; align-items: flex-end; flex-wrap: wrap">
            <button class="btn" :disabled="!!loading" @click="action('preview')">{{ t('预览数据', 'Preview data') }}</button>
            <div class="field" style="width: 130px">
              <label>{{ t('模式', 'Mode') }}</label>
              <select v-model="mode" class="select"><option value="append">append</option><option value="replace">replace</option></select>
            </div>
            <div class="field" style="width: 130px"><label>{{ t('最大行数', 'Limit') }}</label><input v-model.number="limit" type="number" class="input" /></div>
            <button class="btn primary" :disabled="!!loading" @click="action('copy')">
              <Loader2 v-if="loading === 'copy'" :size="13" class="tool-spin" /><Play :size="13" /> {{ t('复制数据', 'Copy data') }}
            </button>
          </div>
          <div v-if="dataPreview" class="alert info" style="margin-top: 10px; font-size: 12px">
            {{ t('源行数', 'source rows') }} {{ dataPreview.sourceRows }} · {{ t('目标行数', 'target rows') }} {{ dataPreview.targetRows }}
            · {{ t('公共列', 'common columns') }} {{ (dataPreview.columns || []).join(', ') }}
          </div>
        </div>

        <div v-if="result" class="alert info" style="margin-top: 12px; font-size: 12px">
          <template v-if="result.executed">{{ t('已执行', 'Executed') }} {{ result.executed.length }} {{ t('条结构语句', 'schema statements') }}</template>
          <template v-else>{{ t('已插入', 'Inserted') }} {{ result.inserted }} · {{ t('目标行数', 'target rows') }} {{ result.targetRows }}<span v-if="result.deleted"> · {{ t('清空', 'deleted') }} {{ result.deleted }}</span></template>
        </div>
        <div v-if="error" class="alert error" style="margin-top: 10px">{{ error }}</div>
        <div class="tiny" style="margin-top: 10px">
          {{ t('结构同步按表进行：目标表不存在时自动建表；数据同步按同名列复制。', 'Schema sync is per table; data sync copies matching columns.') }}
        </div>
      </div>
      <div class="modal-foot"><button class="btn" @click="close">{{ t('关闭', 'Close') }}</button></div>
    </div>
  </div>
</template>
