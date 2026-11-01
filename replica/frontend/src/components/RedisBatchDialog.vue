<script setup>
import { computed, onMounted, ref } from 'vue'
import { Layers, Loader2, Play, Search, X } from 'lucide-vue-next'
import { api } from '../lib/api'
import { t, toast } from '../lib/store'
import { confirmDialog } from '../lib/ui'

const props = defineProps({
  assetId: { type: String, required: true },
  db: { type: Number, default: 0 },
})
const emit = defineEmits(['close', 'done'])

const open = ref(true)
const pattern = ref('*')
const keys = ref([])
const scanning = ref(false)
const op = ref('delete')
const renameMode = ref('prefix')
const renameValue = ref('')
const findText = ref('')
const replaceText = ref('')
const setValue = ref('')
const ttl = ref('')
const running = ref(false)
const result = ref(null)
const error = ref('')

const selectedCount = computed(() => keys.value.length)

async function preview() {
  scanning.value = true
  error.value = ''
  result.value = null
  try {
    const res = await api.post('/api/redis/scan-keys', {
      assetId: props.assetId,
      db: props.db,
      pattern: pattern.value || '*',
      limit: 1000,
    })
    keys.value = res.keys || []
    toast(t(`匹配 ${keys.value.length} 个键`, `${keys.value.length} matching key(s)`), 'info')
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    scanning.value = false
  }
}

async function execute() {
  if (!keys.value.length) {
    toast(t('请先预览并确认要操作的键', 'Preview the keys first'), 'info')
    return
  }
  if (op.value === 'delete') {
    const ok = await confirmDialog({
      title: t('批量删除', 'Batch delete'),
      message: t(`确定删除 ${keys.value.length} 个键吗？此操作不可撤销。`, `Delete ${keys.value.length} keys? This cannot be undone.`),
      danger: true,
      okText: t('删除', 'Delete'),
    })
    if (!ok) return
  }
  running.value = true
  error.value = ''
  result.value = null
  try {
    let res
    if (op.value === 'delete') {
      res = await api.post('/api/redis/batch-delete', { assetId: props.assetId, db: props.db, keys: keys.value })
    } else if (op.value === 'rename') {
      res = await api.post('/api/redis/batch-rename', {
        assetId: props.assetId,
        db: props.db,
        keys: keys.value,
        rule: { mode: renameMode.value, value: renameValue.value, find: findText.value, replace: replaceText.value },
      })
    } else if (op.value === 'set') {
      res = await api.post('/api/redis/batch-set', {
        assetId: props.assetId,
        db: props.db,
        keys: keys.value,
        value: setValue.value,
        ttl: ttl.value === '' ? null : Number(ttl.value),
      })
    } else {
      res = await api.post('/api/redis/batch-expire', {
        assetId: props.assetId,
        db: props.db,
        keys: keys.value,
        ttl: Number(ttl.value || 0),
      })
    }
    result.value = res
    toast(t('批量操作完成', 'Batch operation finished'), 'success')
    emit('done', res)
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    running.value = false
  }
}

function close() {
  open.value = false
  emit('close')
}

onMounted(preview)
</script>

<template>
  <div v-if="open" class="modal-mask" @mousedown.self="close">
    <div class="modal" style="width: 760px; max-height: 86vh; display: flex; flex-direction: column">
      <div class="modal-head">
        <Layers :size="15" />
        <span class="modal-title">{{ t('Redis 批量操作', 'Redis batch operations') }}</span>
        <span class="badge">db{{ db }}</span>
        <span class="grow" style="flex: 1" />
        <button class="icon-btn sm" @click="close"><X :size="14" /></button>
      </div>
      <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
        <div class="row" style="gap: 8px; align-items: flex-end">
          <div class="field" style="flex: 1">
            <label>{{ t('键模式（glob）', 'Key pattern (glob)') }}</label>
            <input v-model="pattern" class="input mono" placeholder="user:*" @keyup.enter="preview" />
          </div>
          <button class="btn" :disabled="scanning" @click="preview">
            <Loader2 v-if="scanning" :size="13" class="tool-spin" /><Search v-else :size="13" />
            {{ t('预览匹配', 'Preview') }}
          </button>
        </div>

        <div class="alert info" style="margin-top: 10px">
          {{ t('匹配', 'Matched') }} <b>{{ selectedCount }}</b> {{ t('个键（上限 1000）', 'keys (limit 1000)') }}
          <span v-if="keys.length" class="mono tiny" style="margin-left: 8px">{{ keys.slice(0, 6).join(', ') }}<span v-if="keys.length > 6"> …</span></span>
        </div>

        <div class="row" style="gap: 6px; margin-top: 12px; flex-wrap: wrap">
          <button class="btn sm" :class="{ primary: op === 'delete' }" @click="op = 'delete'">{{ t('删除', 'Delete') }}</button>
          <button class="btn sm" :class="{ primary: op === 'rename' }" @click="op = 'rename'">{{ t('重命名', 'Rename') }}</button>
          <button class="btn sm" :class="{ primary: op === 'set' }" @click="op = 'set'">{{ t('设置值', 'Set value') }}</button>
          <button class="btn sm" :class="{ primary: op === 'expire' }" @click="op = 'expire'">{{ t('设置 TTL', 'Set TTL') }}</button>
        </div>

        <div v-if="op === 'rename'" class="field-row" style="margin-top: 10px">
          <div class="field" style="flex: 1">
            <label>{{ t('方式', 'Mode') }}</label>
            <select v-model="renameMode" class="select">
              <option value="prefix">{{ t('加前缀', 'Add prefix') }}</option>
              <option value="suffix">{{ t('加后缀', 'Add suffix') }}</option>
              <option value="replace">{{ t('替换', 'Replace') }}</option>
              <option value="strip">{{ t('去掉前缀', 'Strip prefix') }}</option>
            </select>
          </div>
          <div v-if="renameMode === 'replace'" class="field" style="flex: 2">
            <label>{{ t('查找 / 替换为', 'Find / replace with') }}</label>
            <div class="row" style="gap: 6px">
              <input v-model="findText" class="input mono" style="flex: 1" placeholder="old" />
              <input v-model="replaceText" class="input mono" style="flex: 1" placeholder="new" />
            </div>
          </div>
          <div v-else class="field" style="flex: 2">
            <label>{{ renameMode === 'strip' ? t('要去掉的前缀', 'Prefix to strip') : t('前缀 / 后缀内容', 'Prefix / suffix') }}</label>
            <input v-model="renameValue" class="input mono" placeholder="tmp:" />
          </div>
        </div>

        <div v-if="op === 'set'" class="field-row" style="margin-top: 10px">
          <div class="field" style="flex: 2"><label>{{ t('新值（仅 string 类型）', 'New value (string keys only)') }}</label><input v-model="setValue" class="input mono" /></div>
          <div class="field"><label>TTL{{ t('（秒，可空）', ' (s, optional)') }}</label><input v-model="ttl" type="number" class="input" /></div>
        </div>

        <div v-if="op === 'expire'" class="field" style="margin-top: 10px; max-width: 240px">
          <label>{{ t('TTL（秒，0 = 永久）', 'TTL (s, 0 = persist)') }}</label>
          <input v-model="ttl" type="number" class="input" placeholder="3600" />
        </div>

        <div v-if="error" class="alert error" style="margin-top: 10px">{{ error }}</div>

        <div v-if="result" class="alert info" style="margin-top: 12px; font-size: 12px">
          <template v-if="op === 'delete'">{{ t('已删除', 'Deleted') }} {{ result.deleted }} / {{ result.requested }}</template>
          <template v-else-if="op === 'rename'">{{ t('已重命名', 'Renamed') }} {{ result.renamed }} · {{ t('失败', 'failed') }} {{ result.failed }}</template>
          <template v-else>{{ t('已更新', 'Updated') }} {{ result.updated }} · {{ t('失败', 'failed') }} {{ result.failed }}</template>
        </div>
        <div v-if="result?.results?.length" style="margin-top: 8px; max-height: 200px; overflow: auto; border: 1px solid var(--border); border-radius: 6px">
          <table class="grid">
            <thead><tr><th>{{ t('键', 'Key') }}</th><th>{{ t('结果', 'Result') }}</th></tr></thead>
            <tbody>
              <tr v-for="(row, i) in result.results.filter((r) => !r.ok)" :key="i">
                <td class="mono tiny">{{ row.key }}</td>
                <td class="tiny" style="color: var(--red)">{{ row.error }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="close">{{ t('关闭', 'Close') }}</button>
        <button class="btn primary" :disabled="running || !keys.length" @click="execute">
          <Loader2 v-if="running" :size="13" class="tool-spin" /><Play v-else :size="13" />
          {{ running ? t('执行中…', 'Running…') : t('执行', 'Execute') }}
        </button>
      </div>
    </div>
  </div>
</template>
