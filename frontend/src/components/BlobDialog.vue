<script setup>
import { computed, ref, watch } from 'vue'
import { Binary, Download, Loader2, X } from 'lucide-vue-next'
import { api, postDownload } from '../lib/api'
import { t, toast } from '../lib/store'

const props = defineProps({
  assetId: { type: String, required: true },
  table: { type: String, required: true },
  column: { type: String, required: true },
  rowKeys: { type: Object, default: () => ({}) },
  database: { type: String, default: '' },
  schema: { type: String, default: '' },
})

const emit = defineEmits(['close'])

const loading = ref(false)
const error = ref('')
const blob = ref(null)
const mode = ref('text')
const open = ref(true)

const sizeText = computed(() => {
  if (!blob.value) return ''
  const n = blob.value.size || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
})

async function load() {
  loading.value = true
  error.value = ''
  blob.value = null
  try {
    blob.value = await api.post('/api/db/blob', {
      assetId: props.assetId,
      database: props.database,
      schema: props.schema,
      table: props.table,
      column: props.column,
      keys: props.rowKeys,
    })
    mode.value = blob.value?.text ? 'text' : 'hex'
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

async function download() {
  try {
    await postDownload(
      '/api/db/blob/download',
      {
        assetId: props.assetId,
        database: props.database,
        schema: props.schema,
        table: props.table,
        column: props.column,
        keys: props.rowKeys,
        filename: `${props.table}-${props.column}.bin`,
      },
      `${props.table}-${props.column}.bin`,
    )
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function close() {
  open.value = false
  emit('close')
}

watch(() => [props.table, props.column, props.rowKeys], load, { immediate: true })
</script>

<template>
  <div v-if="open" class="modal-mask" @mousedown.self="close">
    <div class="modal" style="width: 780px; max-height: 82vh; display: flex; flex-direction: column">
      <div class="modal-head">
        <Binary :size="15" />
        <span class="modal-title mono">{{ table }}.{{ column }}</span>
        <span class="badge">{{ sizeText }}</span>
        <span class="grow" style="flex: 1" />
        <button class="icon-btn sm" :title="t('下载原始内容', 'Download raw')" @click="download"><Download :size="14" /></button>
        <button class="icon-btn sm" @click="close"><X :size="14" /></button>
      </div>
      <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
        <div v-if="loading" class="center-hint" style="padding: 40px"><Loader2 :size="20" class="tool-spin" /></div>
        <div v-else-if="error" class="alert error">{{ error }}</div>
        <template v-else-if="blob">
          <div v-if="blob.null" class="alert info">{{ t('该字段为 NULL', 'This column is NULL') }}</div>
          <template v-else>
            <div class="toolbar" style="padding: 0 0 8px; background: transparent; border: 0">
              <button class="btn sm" :class="{ primary: mode === 'text' }" :disabled="!blob.text" @click="mode = 'text'">{{ t('文本', 'Text') }}</button>
              <button class="btn sm" :class="{ primary: mode === 'hex' }" @click="mode = 'hex'">{{ t('十六进制', 'Hex') }}</button>
              <span v-if="blob.truncated" class="badge orange">{{ t('仅显示前 256 KB', 'first 256 KB only') }}</span>
              <span class="grow" style="flex: 1" />
              <span class="tiny">{{ blob.kind === 'binary' ? t('二进制', 'binary') : t('文本', 'text') }}</span>
            </div>
            <pre v-if="mode === 'text'" class="mono" style="white-space: pre-wrap; word-break: break-all; margin: 0; font-size: 12px">{{ blob.text }}</pre>
            <pre v-else class="mono" style="white-space: pre-wrap; word-break: break-all; margin: 0; font-size: 12px">{{ (blob.hex || '').replace(/(.{64})/g, '$1\n') }}</pre>
          </template>
        </template>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="download"><Download :size="13" /> {{ t('下载原始文件', 'Download raw file') }}</button>
        <button class="btn primary" @click="close">{{ t('关闭', 'Close') }}</button>
      </div>
    </div>
  </div>
</template>
