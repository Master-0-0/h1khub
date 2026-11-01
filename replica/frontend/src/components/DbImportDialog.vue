<script setup>
import { computed, ref } from 'vue'
import { CheckCircle2, FileUp, Loader2, Upload, X, XCircle } from 'lucide-vue-next'
import { api } from '../lib/api'
import { t, toast } from '../lib/store'

const props = defineProps({
  assetId: { type: String, required: true },
  database: { type: String, default: '' },
  dialect: { type: String, default: 'mysql' },
})
const emit = defineEmits(['close', 'imported'])

const open = ref(true)
const sql = ref('')
const filename = ref('')
const stopOnError = ref(true)
const transactional = ref(true)
const running = ref(false)
const result = ref(null)
const error = ref('')
const dragOver = ref(false)

const statementCount = computed(() => {
  const text = sql.value
  if (!text.trim()) return 0
  return (text.match(/;[ \t]*(?:\r?\n|$)/g) || []).length + 1
})

function pickFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  readFile(file)
  event.target.value = ''
}

function readFile(file) {
  if (file.size > 16 * 1024 * 1024) {
    toast(t('脚本超过 16 MB', 'Script exceeds 16 MB'), 'error')
    return
  }
  filename.value = file.name
  const reader = new FileReader()
  reader.onload = () => {
    sql.value = String(reader.result || '')
    toast(t(`已载入 ${file.name}`, `Loaded ${file.name}`), 'success')
  }
  reader.readAsText(file, 'utf-8')
}

function onDrop(event) {
  dragOver.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) readFile(file)
}

async function run() {
  if (!sql.value.trim()) {
    toast(t('请粘贴或选择 .sql 脚本', 'Paste or choose a .sql script'), 'info')
    return
  }
  running.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await api.post('/api/db/import', {
      assetId: props.assetId,
      database: props.database,
      sql: sql.value,
      filename: filename.value,
      stopOnError: stopOnError.value,
      transactional: transactional.value,
    })
    const failed = result.value?.failed || 0
    toast(
      failed
        ? t(`导入完成，${failed} 条语句失败`, `Import finished with ${failed} failure(s)`)
        : t('导入成功', 'Import succeeded'),
      failed ? 'error' : 'success',
    )
    if (!failed) emit('imported', result.value)
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
</script>

<template>
  <div v-if="open" class="modal-mask" @mousedown.self="close">
    <div class="modal" style="width: 820px; max-height: 88vh; display: flex; flex-direction: column">
      <div class="modal-head">
        <FileUp :size="15" />
        <span class="modal-title">{{ t('导入 SQL 脚本', 'Import SQL script') }}</span>
        <span class="badge">{{ props.database || t('默认库', 'default') }}</span>
        <span class="grow" style="flex: 1" />
        <button class="icon-btn sm" @click="close"><X :size="14" /></button>
      </div>
      <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
        <div
          class="drop-zone"
          :class="{ over: dragOver }"
          style="border: 1px dashed var(--border-strong, var(--border)); border-radius: 8px; padding: 14px; display: flex; align-items: center; gap: 10px; margin-bottom: 10px"
          @dragover.prevent="dragOver = true"
          @dragleave="dragOver = false"
          @drop.prevent="onDrop"
        >
          <Upload :size="18" />
          <div style="flex: 1">
            <div style="font-size: 12.5px">{{ t('拖入 .sql 文件，或选择文件', 'Drop a .sql file, or choose one') }}</div>
            <div class="tiny">{{ filename || t('未选择文件；也可以直接在下方粘贴 SQL', 'No file selected; you can also paste SQL below') }}</div>
          </div>
          <label class="btn sm" style="cursor: pointer">
            {{ t('选择文件', 'Choose file') }}
            <input type="file" accept=".sql,.txt,text/plain" style="display: none" @change="pickFile" />
          </label>
        </div>

        <textarea
          v-model="sql"
          class="input mono"
          style="width: 100%; min-height: 220px; resize: vertical; font-size: 12px; line-height: 1.5"
          :placeholder="t('-- 粘贴 SQL 脚本，例如：\nCREATE TABLE demo_t (id INT PRIMARY KEY);\nINSERT INTO demo_t VALUES (1);', '-- Paste SQL, e.g.\nCREATE TABLE demo_t (id INT PRIMARY KEY);')"
        />

        <div class="row" style="gap: 18px; margin-top: 8px; align-items: center; flex-wrap: wrap">
          <label class="row" style="gap: 6px; font-size: 12px; cursor: pointer">
            <input v-model="stopOnError" type="checkbox" />
            {{ t('遇错停止', 'Stop on error') }}
          </label>
          <label class="row" style="gap: 6px; font-size: 12px; cursor: pointer">
            <input v-model="transactional" type="checkbox" :disabled="dialect === 'sqlite'" />
            {{ t('整体事务（失败回滚）', 'Wrap in a transaction') }}
          </label>
          <span class="grow" style="flex: 1" />
          <span class="tiny">{{ t('约', 'approx.') }} {{ statementCount }} {{ t('条语句', 'statements') }} · {{ sql.length }} {{ t('字符', 'chars') }}</span>
        </div>

        <div v-if="error" class="alert error" style="margin-top: 10px">{{ error }}</div>

        <template v-if="result">
          <div class="alert" :class="result.failed ? 'error' : 'info'" style="margin-top: 10px">
            {{ t('执行', 'Executed') }} {{ result.executed }}/{{ result.total ?? result.executed }}
            {{ t('条', 'statements') }} · {{ t('失败', 'failed') }} {{ result.failed }} · {{ result.elapsed }} ms
            <span v-if="result.rolledBack" class="badge red" style="margin-left: 6px">{{ t('已回滚', 'rolled back') }}</span>
          </div>
          <div style="max-height: 240px; overflow: auto; border: 1px solid var(--border); border-radius: 6px; margin-top: 8px">
            <table class="grid">
              <thead><tr><th style="width: 44px">#</th><th>{{ t('语句', 'Statement') }}</th><th style="width: 70px">{{ t('结果', 'Result') }}</th></tr></thead>
              <tbody>
                <tr v-for="item in result.statements" :key="item.index">
                  <td class="rownum">{{ item.index }}</td>
                  <td class="mono" style="font-size: 11px; max-width: 520px; white-space: pre-wrap">{{ item.sql }}</td>
                  <td>
                    <CheckCircle2 v-if="item.ok" :size="14" style="color: var(--success, #4caf50)" />
                    <span v-else :title="item.error"><XCircle :size="14" style="color: var(--danger, #e05d5d)" /></span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="close">{{ t('关闭', 'Close') }}</button>
        <button class="btn primary" :disabled="running" @click="run">
          <Loader2 v-if="running" :size="13" class="tool-spin" /><Upload v-else :size="13" />
          {{ running ? t('导入中…', 'Importing…') : t('开始导入', 'Import') }}
        </button>
      </div>
    </div>
  </div>
</template>
