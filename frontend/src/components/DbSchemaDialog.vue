<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  AlertTriangle, Check, Loader2, Plus, Save, Table2, Trash2, X,
} from 'lucide-vue-next'
import { api } from '../lib/api'
import { t, toast } from '../lib/store'
import { confirmDialog } from '../lib/ui'

const props = defineProps({
  assetId: { type: String, required: true },
  database: { type: String, default: '' },
  schema: { type: String, default: '' },
  table: { type: String, default: '' },
  dialect: { type: String, default: 'mysql' },
})
const emit = defineEmits(['close', 'saved'])

const isCreate = computed(() => !props.table)
const open = ref(true)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const tableName = ref(props.table || '')
const comment = ref('')
const originalComment = ref('')
const columns = ref([])
const indexes = ref([])
const foreignKeys = ref([])
const executedSql = ref([])
const tab = ref('columns')

const typeHints = computed(() => {
  if (props.dialect === 'postgresql') {
    return ['integer', 'bigint', 'smallint', 'serial', 'bigserial', 'text', 'varchar(255)', 'boolean', 'numeric(10,2)', 'timestamp', 'timestamptz', 'date', 'jsonb', 'uuid', 'bytea']
  }
  if (props.dialect === 'sqlite') {
    return ['INTEGER', 'TEXT', 'REAL', 'BLOB', 'NUMERIC', 'VARCHAR(255)', 'DATETIME']
  }
  return ['INT', 'BIGINT', 'VARCHAR(255)', 'TEXT', 'DECIMAL(10,2)', 'DATETIME', 'TIMESTAMP', 'DATE', 'TINYINT(1)', 'JSON', 'BLOB']
})

function blankColumn() {
  return {
    name: '',
    type: props.dialect === 'postgresql' ? 'text' : 'VARCHAR(255)',
    nullable: true,
    primary: false,
    default: '',
    comment: '',
    autoIncrement: false,
    _action: 'add',
    _orig: null,
    _origName: '',
  }
}

async function load() {
  if (isCreate.value) {
    columns.value = [blankColumn()]
    return
  }
  loading.value = true
  try {
    const data = await api.post('/api/db/table-schema', {
      assetId: props.assetId,
      database: props.database,
      schema: props.schema,
      table: props.table,
    })
    comment.value = data.comment || ''
    originalComment.value = data.comment || ''
    columns.value = (data.columns || []).map((col) => ({
      name: col.name,
      type: col.type,
      nullable: col.nullable,
      primary: !!col.primary,
      default: col.default ?? '',
      comment: col.comment || '',
      autoIncrement: /auto_increment|nextval/i.test(String(col.extra || '') + String(col.default || '')),
      _action: 'keep',
      _orig: col,
      _origName: col.name,
    }))
    indexes.value = (data.indexes || [])
      .filter((idx) => !idx.primary && !['PRIMARY', 'sqlite_autoindex'].includes(idx.name))
      .map((idx) => ({ ...idx, _action: 'keep' }))
    foreignKeys.value = (data.foreignKeys || []).map((fk) => ({ ...fk, _action: 'keep' }))
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

function removeColumn(index) {
  const col = columns.value[index]
  if (col._action === 'add') {
    columns.value.splice(index, 1)
  } else {
    col._action = col._action === 'drop' ? 'modify' : 'drop'
  }
}

function addColumn() {
  columns.value.push(blankColumn())
}

function addIndex() {
  indexes.value.push({ name: `idx_${tableName.value || 'table'}_${indexes.value.length + 1}`, columns: [], unique: false, _action: 'add', columnsText: '' })
}

function removeIndex(index) {
  const idx = indexes.value[index]
  if (idx._action === 'add') indexes.value.splice(index, 1)
  else idx._action = 'drop'
}

function addForeignKey() {
  foreignKeys.value.push({ name: `fk_${tableName.value || 'table'}_${foreignKeys.value.length + 1}`, columnsText: '', refTable: '', refColumnsText: '', onDelete: '', _action: 'add' })
}

function removeForeignKey(index) {
  const fk = foreignKeys.value[index]
  if (fk._action === 'add') foreignKeys.value.splice(index, 1)
  else fk._action = 'drop'
}

function changed(col) {
  if (col._action !== 'keep') return true
  const o = col._orig || {}
  return (
    col.name !== o.name ||
    col.type !== o.type ||
    col.nullable !== o.nullable ||
    String(col.default ?? '') !== String(o.default ?? '') ||
    (col.comment || '') !== (o.comment || '')
  )
}

function columnSpec(col) {
  const spec = {
    name: col.name,
    type: col.type,
    nullable: col.nullable,
    primary: col.primary,
  }
  if (col.default !== '' && col.default !== null && col.default !== undefined) spec.default = col.default
  if (col.comment) spec.comment = col.comment
  if (col.autoIncrement) spec.autoIncrement = true
  return spec
}

function buildOps() {
  const ops = []
  for (const col of columns.value) {
    if (!col.name && col._action !== 'drop') throw new Error(t('字段名不能为空', 'Column name is required'))
    if (col._action === 'drop') {
      ops.push({ kind: 'drop', name: col._origName })
      continue
    }
    if (col._action === 'add') {
      ops.push({ kind: 'add', column: columnSpec(col) })
      continue
    }
    if (!changed(col)) continue
    const spec = columnSpec(col)
    if (col.name !== col._origName) spec.oldName = col._origName
    if (props.dialect === 'postgresql') {
      const o = col._orig || {}
      if (col.type === o.type) delete spec.type
      if (col.nullable === o.nullable) delete spec.nullable
      if (String(col.default ?? '') === String(o.default ?? '')) delete spec.default
      if ((col.comment || '') === (o.comment || '')) delete spec.comment
      else spec.comment = col.comment || ''
      if (col.primary) delete spec.primary
    }
    ops.push({ kind: 'modify', column: spec })
  }
  for (const idx of indexes.value) {
    if (idx._action === 'drop') {
      ops.push({ kind: 'drop-index', name: idx.name })
      continue
    }
    const cols = idx._action === 'add'
      ? String(idx.columnsText || '').split(',').map((s) => s.trim()).filter(Boolean)
      : idx.columns
    if (idx._action === 'add') {
      if (!cols.length) throw new Error(t('索引需要至少一列', 'An index needs at least one column'))
      ops.push({ kind: 'add-index', index: { name: idx.name, columns: cols, unique: !!idx.unique } })
    }
  }
  for (const fk of foreignKeys.value) {
    if (fk._action === 'drop') {
      ops.push({ kind: 'drop-fk', name: fk.name })
      continue
    }
    if (fk._action === 'add') {
      const cols = String(fk.columnsText || '').split(',').map((s) => s.trim()).filter(Boolean)
      const refCols = String(fk.refColumnsText || '').split(',').map((s) => s.trim()).filter(Boolean)
      if (!cols.length || !fk.refTable || !refCols.length) throw new Error(t('外键需要本表列、引用表与引用列', 'Foreign key needs local columns, reference table and columns'))
      ops.push({
        kind: 'add-fk',
        fk: {
          name: fk.name,
          columns: cols,
          refTable: fk.refTable,
          refColumns: refCols,
          onDelete: fk.onDelete || '',
          onUpdate: '',
        },
      })
    }
  }
  if (!isCreate.value && comment.value !== originalComment.value) {
    ops.push({ kind: 'comment', text: comment.value })
  }
  return ops
}

async function save() {
  error.value = ''
  let ops
  try {
    ops = buildOps()
  } catch (err) {
    error.value = String(err.message || err)
    return
  }
  if (!isCreate.value && !ops.length) {
    toast(t('没有需要保存的变更', 'No changes to save'), 'info')
    return
  }
  const destructive = ops.filter((op) => ['drop', 'drop-index', 'drop-fk'].includes(op.kind))
  if (destructive.length) {
    const okConfirm = await confirmDialog({
      title: t('确认结构变更', 'Confirm schema change'),
      message: t(`将执行 ${ops.length} 项结构变更，其中 ${destructive.length} 项是删除操作。`, `${ops.length} schema change(s), ${destructive.length} destructive.`),
      detail: t('删除字段/索引/外键不可撤销，请确认已备份。', 'Dropping columns/indexes/keys cannot be undone.'),
      danger: true,
      okText: t('执行', 'Apply'),
    })
    if (!okConfirm) return
  }
  saving.value = true
  try {
    let data
    if (isCreate.value) {
      if (!tableName.value.trim()) throw new Error(t('表名不能为空', 'Table name is required'))
      data = await api.post('/api/db/schema', {
        assetId: props.assetId,
        database: props.database,
        schema: props.schema,
        table: tableName.value.trim(),
        action: 'create-table',
        spec: {
          columns: columns.value.filter((c) => c._action !== 'drop').map((c) => columnSpec(c)),
          indexes: indexes.value
            .filter((idx) => idx._action === 'add')
            .map((idx) => ({
              name: idx.name,
              unique: !!idx.unique,
              columns: String(idx.columnsText || '').split(',').map((s) => s.trim()).filter(Boolean),
            })),
          comment: comment.value || undefined,
        },
      })
    } else {
      data = await api.post('/api/db/schema', {
        assetId: props.assetId,
        database: props.database,
        schema: props.schema,
        table: props.table,
        action: 'alter',
        ops,
      })
    }
    executedSql.value = data.sql || []
    toast(t('表结构已保存', 'Schema saved'), 'success')
    emit('saved', { table: isCreate.value ? tableName.value.trim() : props.table })
    if (!isCreate.value) await load()
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    saving.value = false
  }
}

function close() {
  open.value = false
  emit('close')
}

onMounted(load)
</script>

<template>
  <div v-if="open" class="modal-mask" @mousedown.self="close">
    <div class="modal" style="width: 920px; max-height: 90vh; display: flex; flex-direction: column">
      <div class="modal-head">
        <Table2 :size="15" />
        <span class="modal-title">
          {{ isCreate ? t('新建表', 'Create table') : t('设计表', 'Design table') }}
          <span class="mono" style="color: var(--text-2)">{{ isCreate ? tableName || '…' : table }}</span>
        </span>
        <span class="badge">{{ dialect }}</span>
        <span class="grow" style="flex: 1" />
        <button class="icon-btn sm" @click="close"><X :size="14" /></button>
      </div>

      <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
        <div v-if="loading" class="center-hint" style="padding: 40px"><Loader2 :size="20" class="tool-spin" /></div>
        <template v-else>
          <div class="row" style="gap: 10px; align-items: flex-end; flex-wrap: wrap">
            <div class="field" style="flex: 1; min-width: 200px">
              <label>{{ t('表名', 'Table name') }}</label>
              <input v-model="tableName" class="input mono" :disabled="!isCreate" :placeholder="t('新表名', 'new_table')" />
            </div>
            <div class="field" style="flex: 2; min-width: 240px">
              <label>{{ t('注释', 'Comment') }}</label>
              <input v-model="comment" class="input" :placeholder="t('表说明（MySQL / PostgreSQL）', 'Table comment')" />
            </div>
          </div>

          <div class="row" style="gap: 6px; margin: 12px 0 6px">
            <button class="btn sm" :class="{ primary: tab === 'columns' }" @click="tab = 'columns'">{{ t('字段', 'Columns') }} ({{ columns.length }})</button>
            <button class="btn sm" :class="{ primary: tab === 'indexes' }" @click="tab = 'indexes'">{{ t('索引', 'Indexes') }} ({{ indexes.length }})</button>
            <button class="btn sm" :class="{ primary: tab === 'foreignKeys' }" @click="tab = 'foreignKeys'">{{ t('外键', 'Foreign keys') }} ({{ foreignKeys.length }})</button>
            <span class="grow" style="flex: 1" />
            <span v-if="dialect === 'sqlite'" class="badge orange">{{ t('SQLite 仅支持增删字段/重命名', 'SQLite: add/drop/rename only') }}</span>
          </div>

          <div v-if="tab === 'columns'">
            <div style="border: 1px solid var(--border); border-radius: 6px; overflow: auto">
              <table class="grid">
                <thead>
                  <tr>
                    <th style="width: 180px">{{ t('名称', 'Name') }}</th>
                    <th style="width: 170px">{{ t('类型', 'Type') }}</th>
                    <th style="width: 56px">{{ t('NULL', 'NULL') }}</th>
                    <th style="width: 56px">PK</th>
                    <th style="width: 150px">{{ t('默认值', 'Default') }}</th>
                    <th style="width: 150px">{{ t('注释', 'Comment') }}</th>
                    <th style="width: 46px"></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(col, index) in columns" :key="index" :class="{ 'row-deleted': col._action === 'drop' }">
                    <td>
                      <input v-model="col.name" class="input mono" style="height: 24px; font-size: 12px" :disabled="col._action === 'drop'" />
                      <span v-if="col._action === 'add'" class="badge accent" style="font-size: 9px">new</span>
                      <span v-else-if="changed(col)" class="badge orange" style="font-size: 9px">mod</span>
                    </td>
                    <td>
                      <input v-model="col.type" class="input mono" style="height: 24px; font-size: 12px" :list="`col-types-${dialect}`" :disabled="col._action === 'drop' || (col._orig && dialect === 'sqlite')" />
                    </td>
                    <td style="text-align: center">
                      <input v-model="col.nullable" type="checkbox" :disabled="col._action === 'drop'" />
                    </td>
                    <td style="text-align: center">
                      <input v-model="col.primary" type="checkbox" :disabled="col._action === 'drop' || !!col._orig" />
                    </td>
                    <td>
                      <input v-model="col.default" class="input mono" style="height: 24px; font-size: 11.5px" :disabled="col._action === 'drop'" placeholder="NULL" />
                    </td>
                    <td>
                      <input v-model="col.comment" class="input" style="height: 24px; font-size: 12px" :disabled="col._action === 'drop' || dialect === 'sqlite'" />
                    </td>
                    <td style="text-align: center">
                      <button class="icon-btn sm" :title="col._action === 'drop' ? t('撤销删除', 'Undo drop') : t('删除字段', 'Drop column')" @click="removeColumn(index)">
                        <Trash2 v-if="col._action !== 'drop'" :size="13" /><Check v-else :size="13" />
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <datalist :id="`col-types-${dialect}`">
              <option v-for="hint in typeHints" :key="hint" :value="hint" />
            </datalist>
            <button class="btn sm" style="margin-top: 8px" @click="addColumn"><Plus :size="13" /> {{ t('添加字段', 'Add column') }}</button>
          </div>

          <div v-else-if="tab === 'indexes'">
            <div style="border: 1px solid var(--border); border-radius: 6px; overflow: auto">
              <table class="grid">
                <thead>
                  <tr>
                    <th style="width: 220px">{{ t('索引名', 'Name') }}</th>
                    <th>{{ t('列（逗号分隔）', 'Columns (comma separated)') }}</th>
                    <th style="width: 70px">{{ t('唯一', 'Unique') }}</th>
                    <th style="width: 46px"></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(idx, index) in indexes" :key="index" :class="{ 'row-deleted': idx._action === 'drop' }">
                    <td><input v-model="idx.name" class="input mono" style="height: 24px; font-size: 12px" :disabled="idx._action !== 'add'" /></td>
                    <td>
                      <input
                        v-if="idx._action === 'add'"
                        v-model="idx.columnsText"
                        class="input mono"
                        style="height: 24px; font-size: 12px"
                        placeholder="col1, col2"
                      />
                      <span v-else class="mono tiny">{{ (idx.columns || []).join(', ') }}</span>
                    </td>
                    <td style="text-align: center"><input v-model="idx.unique" type="checkbox" :disabled="idx._action !== 'add'" /></td>
                    <td style="text-align: center">
                      <button class="icon-btn sm" @click="removeIndex(index)">
                        <Trash2 v-if="idx._action !== 'drop'" :size="13" /><Check v-else :size="13" />
                      </button>
                    </td>
                  </tr>
                  <tr v-if="!indexes.length"><td colspan="4" class="tiny" style="padding: 14px; text-align: center">{{ t('暂无索引', 'No indexes') }}</td></tr>
                </tbody>
              </table>
            </div>
            <button class="btn sm" style="margin-top: 8px" @click="addIndex"><Plus :size="13" /> {{ t('添加索引', 'Add index') }}</button>
          </div>

          <div v-else>
            <div style="border: 1px solid var(--border); border-radius: 6px; overflow: auto">
              <table class="grid">
                <thead>
                  <tr>
                    <th style="width: 190px">{{ t('约束名', 'Name') }}</th>
                    <th style="width: 160px">{{ t('本表列', 'Columns') }}</th>
                    <th style="width: 150px">{{ t('引用表', 'Ref table') }}</th>
                    <th style="width: 160px">{{ t('引用列', 'Ref columns') }}</th>
                    <th style="width: 110px">ON DELETE</th>
                    <th style="width: 46px"></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(fk, index) in foreignKeys" :key="index" :class="{ 'row-deleted': fk._action === 'drop' }">
                    <td><input v-model="fk.name" class="input mono" style="height: 24px; font-size: 12px" :disabled="fk._action !== 'add'" /></td>
                    <td>
                      <input v-if="fk._action === 'add'" v-model="fk.columnsText" class="input mono" style="height: 24px; font-size: 12px" placeholder="user_id" />
                      <span v-else class="mono tiny">{{ (fk.columns || []).join(', ') }}</span>
                    </td>
                    <td>
                      <input v-if="fk._action === 'add'" v-model="fk.refTable" class="input mono" style="height: 24px; font-size: 12px" placeholder="users" />
                      <span v-else class="mono tiny">{{ fk.refTable }}</span>
                    </td>
                    <td>
                      <input v-if="fk._action === 'add'" v-model="fk.refColumnsText" class="input mono" style="height: 24px; font-size: 12px" placeholder="id" />
                      <span v-else class="mono tiny">{{ (fk.refColumns || []).join(', ') }}</span>
                    </td>
                    <td>
                      <select v-if="fk._action === 'add'" v-model="fk.onDelete" class="select" style="height: 24px; font-size: 12px">
                        <option value="">—</option>
                        <option value="CASCADE">CASCADE</option>
                        <option value="RESTRICT">RESTRICT</option>
                        <option value="SET NULL">SET NULL</option>
                        <option value="NO ACTION">NO ACTION</option>
                      </select>
                      <span v-else class="tiny">{{ fk.onDelete || '—' }}</span>
                    </td>
                    <td style="text-align: center">
                      <button class="icon-btn sm" @click="removeForeignKey(index)">
                        <Trash2 v-if="fk._action !== 'drop'" :size="13" /><Check v-else :size="13" />
                      </button>
                    </td>
                  </tr>
                  <tr v-if="!foreignKeys.length"><td colspan="6" class="tiny" style="padding: 14px; text-align: center">{{ t('暂无外键', 'No foreign keys') }}</td></tr>
                </tbody>
              </table>
            </div>
            <button class="btn sm" style="margin-top: 8px" :disabled="dialect === 'sqlite'" @click="addForeignKey"><Plus :size="13" /> {{ t('添加外键', 'Add foreign key') }}</button>
            <span v-if="dialect === 'sqlite'" class="tiny" style="margin-left: 8px">{{ t('SQLite 不支持在线增删外键', 'SQLite does not support online FK changes') }}</span>
          </div>

          <div v-if="error" class="alert error" style="margin-top: 12px">{{ error }}</div>
          <div v-if="executedSql.length" class="alert info" style="margin-top: 12px; font-family: var(--mono); font-size: 11px; white-space: pre-wrap">{{ executedSql.join('\n') }}</div>
          <div v-if="columns.some((c) => c._action === 'drop')" class="alert warn" style="margin-top: 12px">
            <AlertTriangle :size="14" /> {{ t('标记为删除的字段将在保存后永久移除。', 'Columns marked for deletion are removed permanently.') }}
          </div>
        </template>
      </div>

      <div class="modal-foot">
        <button class="btn" @click="close">{{ t('关闭', 'Close') }}</button>
        <button class="btn primary" :disabled="saving || loading" @click="save">
          <Loader2 v-if="saving" :size="13" class="tool-spin" /><Save v-else :size="13" />
          {{ saving ? t('保存中…', 'Saving…') : isCreate ? t('创建表', 'Create table') : t('保存结构', 'Save schema') }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.row-deleted input { opacity: 0.45; text-decoration: line-through; }
.row-deleted { background: rgba(224, 93, 93, 0.08); }
</style>
