<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  ArrowLeft, Download, File, FileCode2, FileText, Folder, FolderPlus, HardDriveDownload, Loader2,
  MoreHorizontal, Pencil, RefreshCw, Search, Trash2, Upload, X,
} from 'lucide-vue-next'
import { api, postDownload, uploadTo } from '../lib/api'
import { t, toast } from '../lib/store'
import { confirmDialog, promptDialog, showContextMenu } from '../lib/ui'
import { formatBytes, shortTime } from '../lib/format'

const props = defineProps({
  assetId: { type: String, default: '' },
  container: { type: String, required: true },
  title: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const path = ref('/')
const pathInput = ref('/')
const entries = ref([])
const parent = ref('/')
const loading = ref(false)
const error = ref('')
const selected = ref(null)
const search = ref('')
const uploading = ref(false)
const fileInput = ref(null)
const editState = ref(null)
const saving = ref(false)
const dropActive = ref(false)

const visible = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return entries.value
  return entries.value.filter((e) => e.name.toLowerCase().includes(q))
})

const fileIcon = (entry) => {
  if (entry.isDir) return Folder
  const name = entry.name.toLowerCase()
  if (/\.(js|ts|vue|py|go|rs|java|c|cpp|h|sh|sql|json|ya?ml|toml|xml|html|css|conf|ini|env)$/.test(name)) return FileCode2
  if (/\.(txt|md|log|csv)$/.test(name)) return FileText
  return File
}

function joinPath(dir, name) {
  if (dir === '/') return '/' + name
  return dir.replace(/\/+$/, '') + '/' + name
}

async function call(action, payload) {
  return api.post(`/api/docker/${action}`, { assetId: props.assetId || null, container: props.container, ...payload })
}

async function load(target) {
  loading.value = true
  error.value = ''
  try {
    const res = await call('fs-list', { path: target ?? path.value ?? '/' })
    entries.value = res.entries || []
    path.value = res.path
    pathInput.value = res.path
    parent.value = res.parent || '/'
    selected.value = null
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

function enter(entry) {
  if (entry.isDir) load(entry.path)
  else openEditor(entry)
}

async function openEditor(entry) {
  try {
    const res = await call('fs-read', { path: entry.path })
    editState.value = { path: res.path, content: res.content, encoding: res.encoding, name: entry.name, size: res.size }
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function saveEditor() {
  if (!editState.value) return
  saving.value = true
  try {
    await call('fs-write', {
      path: editState.value.path,
      content: editState.value.content,
      encoding: editState.value.encoding,
    })
    toast(t('文件已保存', 'File saved'), 'success')
    editState.value = null
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    saving.value = false
  }
}

async function download(entry) {
  try {
    await postDownload(
      '/api/docker/fs/download',
      { assetId: props.assetId || null, container: props.container, path: entry.path },
      entry.name,
    )
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function uploadFiles(files) {
  if (!files?.length) return
  uploading.value = true
  let ok = 0
  try {
    for (const file of files) {
      try {
        await uploadTo(
          '/api/docker/fs/upload',
          { assetId: props.assetId || null, container: props.container, path: joinPath(path.value, file.name) },
          file,
        )
        ok++
      } catch (err) {
        toast(`${file.name}: ${err.message || err}`, 'error')
      }
    }
    if (ok) toast(t(`已上传 ${ok} 个文件`, `Uploaded ${ok} file(s)`), 'success')
    await load(path.value)
  } finally {
    uploading.value = false
  }
}

function onFilePick(event) {
  uploadFiles([...event.target.files])
  event.target.value = ''
}

function onDrop(event) {
  dropActive.value = false
  uploadFiles([...event.dataTransfer.files])
}

async function mkdir() {
  const name = await promptDialog({ title: t('新建文件夹', 'New folder'), message: t('名称', 'Name'), placeholder: 'new-folder' })
  if (!name) return
  try {
    await call('fs-mkdir', { path: joinPath(path.value, name) })
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function rename(entry) {
  const name = await promptDialog({ title: t('重命名', 'Rename'), message: t('新名称', 'New name'), value: entry.name })
  if (!name || name === entry.name) return
  try {
    await call('fs-rename', { path: entry.path, target: joinPath(path.value, name) })
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function remove(entry) {
  const ok = await confirmDialog({
    title: t('删除', 'Delete'),
    message: t(`确定删除「${entry.name}」吗？目录会递归删除。`, `Delete "${entry.name}"? Folders are recursive.`),
    danger: true,
    okText: t('删除', 'Delete'),
  })
  if (!ok) return
  try {
    await call('fs-delete', { path: entry.path })
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function openMenu(event, entry) {
  selected.value = entry
  showContextMenu(event, [
    { label: entry.isDir ? t('打开目录', 'Open') : t('编辑', 'Edit'), action: () => enter(entry) },
    { label: t('下载', 'Download'), disabled: entry.isDir, action: () => download(entry) },
    { sep: true },
    { label: t('重命名', 'Rename'), action: () => rename(entry) },
    { sep: true },
    { label: t('删除', 'Delete'), danger: true, action: () => remove(entry) },
  ])
}

onMounted(() => load('/'))
</script>

<template>
  <div style="display: flex; flex-direction: column; flex: 1; min-height: 0" @dragover.prevent="dropActive = true" @dragleave="dropActive = false" @drop.prevent="onDrop">
    <div class="toolbar" style="height: 36px">
      <button class="icon-btn sm" :title="t('上级目录', 'Parent')" @click="load(parent)"><ArrowLeft :size="14" /></button>
      <button class="icon-btn sm" :title="t('根目录', 'Root')" @click="load('/')"><HardDriveDownload :size="14" /></button>
      <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="load(path)"><RefreshCw :size="14" :class="{ 'tool-spin': loading }" /></button>
      <div class="searchbox" style="flex: 1; max-width: 420px">
        <span class="icon"><FileCode2 :size="12" /></span>
        <input v-model="pathInput" class="input mono" style="height: 24px; font-size: 11.5px" @keyup.enter="load(pathInput)" />
      </div>
      <div class="sep" />
      <button class="icon-btn sm" :title="t('上传', 'Upload')" @click="fileInput?.click()"><Upload :size="14" /></button>
      <button class="icon-btn sm" :title="t('新建文件夹', 'New folder')" @click="mkdir"><FolderPlus :size="14" /></button>
      <button class="icon-btn sm" :title="t('筛选', 'Filter')"><Search :size="14" /></button>
      <input v-model="search" class="input" style="width: 130px; height: 24px" :placeholder="t('筛选', 'filter')" />
      <span class="grow" style="flex: 1" />
      <span v-if="uploading" class="tiny"><Loader2 :size="12" class="tool-spin" /> {{ t('上传中', 'uploading') }}</span>
      <button class="icon-btn sm" :title="t('关闭', 'Close')" @click="emit('close')"><X :size="14" /></button>
    </div>

    <div class="file-cols" style="padding: 4px 10px">
      <span style="flex: 1">{{ title || t('名称', 'Name') }}</span>
      <span style="width: 80px; text-align: right">{{ t('大小', 'Size') }}</span>
      <span style="width: 110px; text-align: right">{{ t('修改', 'Modified') }}</span>
      <span style="width: 70px; text-align: right">{{ t('权限', 'Mode') }}</span>
    </div>

    <div style="flex: 1; overflow: auto; min-height: 0">
      <div v-if="error" class="alert error" style="margin: 10px">{{ error }}</div>
      <div v-if="dropActive" class="alert info" style="margin: 10px">{{ t('松开以上传到当前目录', 'Drop to upload into this folder') }}</div>
      <div v-if="loading" class="center-hint" style="padding: 30px"><Loader2 :size="18" class="tool-spin" /></div>
      <div v-else-if="!visible.length && !error" class="center-hint" style="padding: 30px; flex-direction: column">
        <Folder :size="26" style="opacity: .4" />
        <span class="tiny">{{ t('目录为空', 'Empty folder') }}</span>
      </div>
      <div
        v-for="entry in visible"
        :key="entry.path"
        class="file-row"
        :class="{ selected: selected?.path === entry.path }"
        style="padding: 5px 10px; display: flex; align-items: center; gap: 8px; cursor: default"
        @click="selected = entry"
        @dblclick="enter(entry)"
        @contextmenu="openMenu($event, entry)"
      >
        <component :is="fileIcon(entry)" :size="14" :style="{ color: entry.isDir ? '#d9b45a' : 'var(--text-3)' }" />
        <span style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ entry.name }}</span>
        <span class="tiny" style="width: 80px; text-align: right">{{ entry.isDir ? '-' : formatBytes(entry.size) }}</span>
        <span class="tiny" style="width: 110px; text-align: right">{{ shortTime(entry.mtime) }}</span>
        <span class="tiny mono" style="width: 70px; text-align: right">{{ (entry.permissions || '').replace('0o', '') }}</span>
      </div>
    </div>

    <div class="statusbar">
      <span>{{ t('共', 'Total') }} {{ visible.length }} {{ t('项', 'items') }}</span>
      <span class="grow" />
      <span class="mono tiny">{{ path }}</span>
    </div>

    <input ref="fileInput" type="file" multiple class="hidden" @change="onFilePick" />

    <div v-if="editState" class="modal-mask" @mousedown.self="editState = null">
      <div class="modal" style="width: 780px; height: 76vh">
        <div class="modal-head">
          <FileCode2 :size="15" />
          <span class="modal-title mono" style="font-size: 12px">{{ editState.path }}</span>
          <span class="badge">{{ editState.encoding }} · {{ formatBytes(editState.size) }}</span>
          <button class="icon-btn sm" @click="editState = null"><X :size="14" /></button>
        </div>
        <textarea v-model="editState.content" class="textarea" style="flex: 1; border: 0; border-radius: 0; resize: none; padding: 12px; font-size: 12px; background: var(--bg)" spellcheck="false" />
        <div class="modal-foot">
          <span class="tiny">{{ t('保存会覆盖容器内文件', 'Saving overwrites the file in the container') }}</span>
          <span class="grow" />
          <button class="btn" @click="editState = null">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" :disabled="saving" @click="saveEditor">{{ t('保存', 'Save') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
