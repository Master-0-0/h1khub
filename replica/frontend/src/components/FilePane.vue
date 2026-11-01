<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import {
  Archive, ArrowLeft, ArrowUp, Download, File, FileArchive, FileCode2, FileImage, FileText, FileUp,
  Folder, FolderPlus, FolderTree, HardDriveDownload, Loader2, MoreHorizontal, PackageOpen, Pencil,
  RefreshCw, Search, Trash2, Upload, X,
} from 'lucide-vue-next'
import { api, postDownload, uploadTo } from '../lib/api'
import { state, t, toast } from '../lib/store'
import { confirmDialog, promptDialog, showContextMenu } from '../lib/ui'
import { formatBytes, shortTime } from '../lib/format'

const props = defineProps({
  sessionId: { type: String, default: '' },
  assetId: { type: String, default: '' },
  backend: { type: String, default: 'sftp' },
  title: { type: String, default: '' },
  initialPath: { type: String, default: '~' },
})
const emit = defineEmits(['path-change'])

const apiBase = computed(() =>
  props.backend === 'ftp' ? '/api/ftp' : props.backend === 'local' ? '/api/fs' : '/api/sftp',
)
const refParams = computed(() =>
  props.backend === 'ftp' ? { assetId: props.assetId } : props.backend === 'local' ? {} : { sessionId: props.sessionId },
)
const canScp = computed(() => props.backend === 'sftp' && !!props.assetId)
const canChmod = computed(() => props.backend === 'sftp')
const canArchive = computed(() => props.backend !== 'ftp')

const path = ref('~')
const pathInput = ref('~')
const entries = ref([])
const parent = ref('/')
const home = ref('~')
const loading = ref(false)
const error = ref('')
const selection = ref([])
const anchorPath = ref('')
const search = ref('')
const searchOpen = ref(false)
const showHidden = ref(true)
const uploading = ref(false)
const fileInput = ref(null)
const scpInput = ref(null)
const transferOpen = ref(false)
const transferEntry = ref(null)
const transferTargetId = ref('')
const transferTargetPath = ref('')
const transferOverwrite = ref(true)
const transferBusy = ref(false)
const editState = ref(null)
const saving = ref(false)
const dropActive = ref(false)

const selectedPaths = computed(() => new Set(selection.value.map((entry) => entry.path)))
const selectedSingleArchive = computed(
  () => selection.value.length === 1 && !selection.value[0].isDir && !!archiveSuffix(selection.value[0].name),
)

const visible = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return entries.value
  return entries.value.filter((e) => e.name.toLowerCase().includes(q))
})

const fileIcon = (entry) => {
  if (entry.isDir) return Folder
  const name = entry.name.toLowerCase()
  if (/\.(png|jpe?g|gif|webp|svg|bmp|ico)$/.test(name)) return FileImage
  if (/\.(js|ts|vue|py|go|rs|java|c|cpp|h|sh|sql|json|ya?ml|toml|xml|html|css)$/.test(name)) return FileCode2
  if (/\.(txt|md|log|conf|ini|env|csv)$/.test(name)) return FileText
  if (archiveSuffix(name)) return FileArchive
  return File
}

// ----------------------------------------------------------- archive helpers
const ARCHIVE_SUFFIXES = [
  '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tbz', '.tar.xz', '.txz', '.tar.zst', '.tzst',
  '.tar', '.zip', '.7z', '.rar', '.gz', '.bz2', '.xz',
]

function archiveSuffix(name) {
  const lower = String(name || '').toLowerCase()
  return ARCHIVE_SUFFIXES.find((suffix) => lower.endsWith(suffix)) || ''
}

function archiveStem(name) {
  const suffix = archiveSuffix(name)
  if (!suffix) return name
  return name.slice(0, name.length - suffix.length) || 'extracted'
}

const FALLBACK_FORMATS = [
  { id: 'tar.gz', label: 'tar.gz (gzip)', ext: '.tar.gz', available: true, missingTools: [] },
  { id: 'zip', label: 'zip', ext: '.zip', available: true, missingTools: [] },
  { id: 'tar.bz2', label: 'tar.bz2 (bzip2)', ext: '.tar.bz2', available: true, missingTools: [] },
  { id: 'tar.xz', label: 'tar.xz (xz)', ext: '.tar.xz', available: true, missingTools: [] },
  { id: 'tar', label: 'tar', ext: '.tar', available: true, missingTools: [] },
  { id: '7z', label: '7z', ext: '.7z', available: false, missingTools: ['7z'] },
]

const compressFormats = ref(FALLBACK_FORMATS.map((item) => ({ ...item })))
const formatsLoading = ref(false)

// compress dialog
const compressOpen = ref(false)
const compressBusy = ref(false)
const compressNameTouched = ref(false)
const compressForm = ref({ format: 'tar.gz', name: '', targetDir: '', overwrite: false })

// extract dialog
const extractOpen = ref(false)
const extractBusy = ref(false)
const extractLoading = ref(false)
const extractTarget = ref(null)
const extractInfo = ref(null)
const extractError = ref('')
const extractMode = ref('folder')
const extractForm = ref({ targetDir: '', create: true, overwrite: true })

function isSelected(entry) {
  return selectedPaths.value.has(entry.path)
}

function defaultArchiveName(items, format) {
  const info =
    compressFormats.value.find((item) => item.id === format) ||
    FALLBACK_FORMATS.find((item) => item.id === format)
  const ext = info?.ext || '.tar.gz'
  let base
  if (items.length === 1) {
    base = items[0].isDir ? items[0].name : archiveStem(items[0].name)
  } else {
    base = path.value.split('/').filter(Boolean).pop() || 'archive'
  }
  base = String(base || 'archive').replace(/[/\\]/g, '_').trim() || 'archive'
  return base + ext
}

async function loadFormats() {
  if (!canArchive.value) return
  formatsLoading.value = true
  try {
    const res = await api.post(`${apiBase.value}/archive/formats`, { ...refParams.value })
    if (res?.formats?.length) compressFormats.value = res.formats
  } catch {
    compressFormats.value = FALLBACK_FORMATS.map((item) => ({ ...item }))
  } finally {
    formatsLoading.value = false
  }
}

async function openCompressDialog() {
  if (!canArchive.value || !selection.value.length) return
  const items = [...selection.value]
  compressForm.value = {
    format: compressForm.value.format || 'tar.gz',
    name: defaultArchiveName(items, compressForm.value.format || 'tar.gz'),
    targetDir: path.value,
    overwrite: false,
  }
  compressNameTouched.value = false
  compressOpen.value = true
  await loadFormats()
  const usable = compressFormats.value.filter((item) => item.available)
  if (usable.length && !usable.some((item) => item.id === compressForm.value.format)) {
    compressForm.value.format = usable[0].id
    if (!compressNameTouched.value) compressForm.value.name = defaultArchiveName(items, usable[0].id)
  }
}

function onArchiveNameInput() {
  compressNameTouched.value = true
}

function onCompressFormatChange() {
  if (!compressNameTouched.value) {
    compressForm.value.name = defaultArchiveName(selection.value, compressForm.value.format)
  }
}

async function submitCompress() {
  if (compressBusy.value) return
  const name = compressForm.value.name.trim()
  if (!name) {
    toast(t('请填写归档名称', 'Enter an archive name'), 'info')
    return
  }
  compressBusy.value = true
  try {
    const res = await api.post(`${apiBase.value}/archive/compress`, {
      ...refParams.value,
      paths: selection.value.map((entry) => entry.path),
      targetDir: compressForm.value.targetDir.trim() || path.value,
      name,
      format: compressForm.value.format,
      overwrite: compressForm.value.overwrite,
    })
    toast(
      t(`已创建 ${res.name}（${res.sizeText}）`, `Created ${res.name} (${res.sizeText})`),
      'success',
    )
    compressOpen.value = false
    selection.value = []
    anchorPath.value = ''
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    compressBusy.value = false
  }
}

function joinPath(dir, name) {
  if (!dir) return name
  if (dir === '/') return '/' + name
  return dir.replace(/\/+$/, '') + '/' + name
}

function applyExtractMode() {
  const entry = extractTarget.value
  if (!entry) return
  if (extractMode.value === 'here') {
    extractForm.value.targetDir = path.value
    extractForm.value.create = false
  } else {
    extractForm.value.targetDir = joinPath(path.value, archiveStem(entry.name))
    extractForm.value.create = true
  }
}

async function openExtractDialog(entry) {
  if (!canArchive.value || !entry || entry.isDir) return
  extractTarget.value = entry
  extractMode.value = 'folder'
  extractForm.value = {
    targetDir: joinPath(path.value, archiveStem(entry.name)),
    create: true,
    overwrite: true,
  }
  extractInfo.value = null
  extractError.value = ''
  extractOpen.value = true
  extractLoading.value = true
  try {
    extractInfo.value = await api.post(`${apiBase.value}/archive/inspect`, {
      ...refParams.value,
      path: entry.path,
    })
    if (extractInfo.value?.partial) {
      // Still fine: the backend re-checks what it listed and the host tool
      // keeps its own traversal protection for the unscanned tail.
    }
  } catch (err) {
    extractError.value = String(err.message || err)
  } finally {
    extractLoading.value = false
  }
}

async function openExtractHere(entry) {
  await openExtractDialog(entry)
  extractMode.value = 'here'
  applyExtractMode()
}

async function submitExtract() {
  if (extractBusy.value || !extractTarget.value) return
  if (extractInfo.value?.unsafe) {
    toast(t('归档包含越界路径，已拒绝解压', 'Archive contains unsafe paths; extraction refused'), 'error')
    return
  }
  extractBusy.value = true
  try {
    const res = await api.post(`${apiBase.value}/archive/extract`, {
      ...refParams.value,
      path: extractTarget.value.path,
      targetDir: extractForm.value.targetDir.trim(),
      create: extractForm.value.create,
      overwrite: extractForm.value.overwrite,
    })
    toast(
      t(`已解压 ${res.members} 项到 ${res.targetDir}`, `Extracted ${res.members} item(s) to ${res.targetDir}`),
      'success',
    )
    extractOpen.value = false
    selection.value = []
    anchorPath.value = ''
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    extractBusy.value = false
  }
}

// ------------------------------------------------------------------ listing
async function load(target) {
  if (props.backend !== 'local' && !props.sessionId && !props.assetId) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.post(`${apiBase.value}/list`, {
      ...refParams.value,
      path: target ?? path.value ?? '~',
      showHidden: showHidden.value,
    })
    entries.value = res.entries || []
    path.value = res.path
    pathInput.value = res.path
    emit('path-change', res.path)
    parent.value = res.parent || '/'
    home.value = res.home || res.path
    selection.value = []
    anchorPath.value = ''
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

function enter(entry) {
  if (entry.isDir) {
    load(entry.path)
    return
  }
  openEditor(entry)
}

function onRowClick(event, entry) {
  if (event.shiftKey && anchorPath.value) {
    const list = visible.value
    const from = list.findIndex((item) => item.path === anchorPath.value)
    const to = list.findIndex((item) => item.path === entry.path)
    if (from >= 0 && to >= 0) {
      const [start, end] = from < to ? [from, to] : [to, from]
      selection.value = list.slice(start, end + 1)
      return
    }
  }
  if (event.ctrlKey || event.metaKey) {
    selection.value = isSelected(entry)
      ? selection.value.filter((item) => item.path !== entry.path)
      : [...selection.value, entry]
    anchorPath.value = entry.path
    return
  }
  selection.value = [entry]
  anchorPath.value = entry.path
}

function clearSelection() {
  selection.value = []
  anchorPath.value = ''
}

async function openEditor(entry) {
  try {
    const res = await api.post(`${apiBase.value}/read`, { ...refParams.value, path: entry.path })
    editState.value = { path: res.path, content: res.content, encoding: res.encoding, name: entry.name, readOnly: res.size > 5 * 1024 * 1024 }
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function saveEditor() {
  if (!editState.value) return
  saving.value = true
  try {
    await api.post(`${apiBase.value}/write`, {
      ...refParams.value,
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

async function uploadFiles(files) {
  if (!files?.length) return
  const huge = files.filter((f) => f.size > 2 * 1024 * 1024 * 1024)
  if (huge.length) {
    const proceed = await confirmDialog({
      title: t('大文件上传', 'Large file upload'),
      message: t(
        `${huge.map((f) => f.name).join(', ')} 超过 2 GB。当前上传不支持断点续传，中断后需要重新上传。`,
        'Files over 2 GB have no resume support and must restart after an interruption.',
      ),
      okText: t('继续', 'Continue'),
    })
    if (!proceed) return
  }
  uploading.value = true
  let ok = 0
  try {
    for (const file of files) {
      try {
        await uploadTo(`${apiBase.value}/upload`, { ...refParams.value, path: joinPath(path.value, file.name) }, file)
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

async function download(entry) {
  try {
    await postDownload(
      `${apiBase.value}/download`,
      { ...refParams.value, path: entry.path },
      entry.name,
    )
  } catch (err) {
    const text = String(err.message || err)
    toast(
      entry.size > 512 * 1024 * 1024
        ? t(`下载失败：${text}。大文件目前不支持断点续传，请重试。`, `Download failed: ${text}. Large downloads cannot resume yet.`)
        : text,
      'error',
    )
  }
}

const sshTargets = computed(() => state.assets.filter((a) => a.typeName === 'SSH' && a.id !== props.assetId))

function openTransfer(entry) {
  transferEntry.value = entry
  transferTargetId.value = sshTargets.value[0]?.id || ''
  transferTargetPath.value = entry.path
  transferOverwrite.value = true
  transferOpen.value = true
}

async function runTransfer() {
  if (!transferTargetId.value || !transferTargetPath.value.trim()) {
    toast(t('请选择目标服务器并填写路径', 'Choose a target server and path'), 'info')
    return
  }
  transferBusy.value = true
  try {
    const res = await api.post('/api/transfer', {
      fromAssetId: props.assetId,
      fromPath: transferEntry.value.path,
      toAssetId: transferTargetId.value,
      toPath: transferTargetPath.value.trim(),
      overwrite: transferOverwrite.value,
    })
    toast(t(`已传输 ${res.files} 个文件 / ${formatBytes(res.bytes)}`, `${res.files} file(s) / ${formatBytes(res.bytes)}`), 'success')
    transferOpen.value = false
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    transferBusy.value = false
  }
}

async function scpDownload(entry) {
  try {
    await postDownload('/api/scp/download', { assetId: props.assetId, path: entry.path }, entry.name)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function scpUploadFiles(files) {
  if (!files?.length) return
  uploading.value = true
  let ok = 0
  try {
    for (const file of files) {
      try {
        await uploadTo('/api/scp/upload', { assetId: props.assetId, path: joinPath(path.value, file.name) }, file)
        ok++
      } catch (err) {
        toast(`${file.name}: ${err.message || err}`, 'error')
      }
    }
    if (ok) toast(t(`已通过 SCP 上传 ${ok} 个文件`, `${ok} file(s) uploaded over SCP`), 'success')
    await load(path.value)
  } finally {
    uploading.value = false
  }
}

function onScpPick(event) {
  scpUploadFiles([...event.target.files])
  event.target.value = ''
}

async function mkdir() {
  const name = await promptDialog({
    title: t('新建文件夹', 'New folder'),
    message: t('文件夹名称', 'Folder name'),
    placeholder: 'new-folder',
  })
  if (!name) return
  try {
    await api.post(`${apiBase.value}/mkdir`, { ...refParams.value, path: joinPath(path.value, name) })
    toast(t('已创建', 'Created'), 'success')
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function rename(entry) {
  const name = await promptDialog({
    title: t('重命名', 'Rename'),
    message: t('新名称', 'New name'),
    value: entry.name,
  })
  if (!name || name === entry.name) return
  try {
    await api.post(`${apiBase.value}/rename`, {
      ...refParams.value,
      path: entry.path,
      target: joinPath(path.value, name),
    })
    toast(t('已重命名', 'Renamed'), 'success')
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function chmod(entry) {
  const mode = await promptDialog({
    title: t('修改权限', 'Change permissions'),
    message: t('八进制权限（例如 644 / 755）', 'Octal mode (e.g. 644 / 755)'),
    value: (entry.permissions || '0o644').replace(/^0o/, ''),
  })
  if (!mode) return
  try {
    await api.post(`${apiBase.value}/chmod`, { ...refParams.value, path: entry.path, mode })
    toast(t('权限已修改', 'Permissions changed'), 'success')
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function remove(entry) {
  const ok = await confirmDialog({
    title: t('删除', 'Delete'),
    message: t(`确定删除「${entry.name}」吗？目录会递归删除。`, `Delete "${entry.name}"? Folders are removed recursively.`),
    danger: true,
    okText: t('删除', 'Delete'),
  })
  if (!ok) return
  try {
    await api.post(`${apiBase.value}/delete`, { ...refParams.value, path: entry.path })
    toast(t('已删除', 'Deleted'), 'success')
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function removeSelection() {
  const items = [...selection.value]
  if (!items.length) return
  const ok = await confirmDialog({
    title: t('删除', 'Delete'),
    message: t(`确定删除选中的 ${items.length} 项吗？目录会递归删除。`, `Delete ${items.length} selected item(s)? Folders are removed recursively.`),
    danger: true,
    okText: t('删除', 'Delete'),
  })
  if (!ok) return
  try {
    for (const entry of items) {
      await api.post(`${apiBase.value}/delete`, { ...refParams.value, path: entry.path })
    }
    toast(t(`已删除 ${items.length} 项`, `${items.length} item(s) deleted`), 'success')
    selection.value = []
    await load(path.value)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function openMenu(event, entry) {
  if (!isSelected(entry)) {
    selection.value = [entry]
    anchorPath.value = entry.path
  }
  const single = selection.value.length === 1 ? selection.value[0] : null
  const archiveReady = single && !single.isDir && !!archiveSuffix(single.name)
  showContextMenu(event, [
    { label: entry.isDir ? t('打开目录', 'Open folder') : t('编辑', 'Edit'), action: () => enter(entry) },
    { label: t('下载', 'Download'), disabled: entry.isDir, action: () => download(entry) },
    { label: t('通过 SCP 下载', 'Download over SCP'), hidden: !canScp.value, disabled: entry.isDir, action: () => scpDownload(entry) },
    { label: t('传输到其他服务器…', 'Transfer to another server…'), hidden: !canScp.value, action: () => openTransfer(entry) },
    { sep: true },
    { label: t('压缩…', 'Compress…'), hidden: !canArchive.value, action: () => openCompressDialog() },
    { label: t('解压到…', 'Extract…'), hidden: !canArchive.value || !archiveReady, action: () => openExtractDialog(single) },
    { label: t('解压到当前目录', 'Extract here'), hidden: !canArchive.value || !archiveReady, action: () => openExtractHere(single) },
    { sep: true },
    { label: t('重命名', 'Rename'), action: () => rename(entry) },
    { label: t('修改权限', 'Permissions'), hidden: !canChmod.value, action: () => chmod(entry) },
    { label: t('复制路径', 'Copy path'), action: () => navigator.clipboard?.writeText(entry.path) },
    { sep: true },
    { label: t('删除', 'Delete'), danger: true, action: () => remove(entry) },
  ])
}

function backgroundMenu(event) {
  showContextMenu(event, [
    { label: t('上传文件', 'Upload files'), action: () => fileInput.value?.click() },
    { label: t('通过 SCP 上传', 'Upload over SCP'), hidden: !canScp.value, action: () => scpInput.value?.click() },
    { label: t('新建文件夹', 'New folder'), action: mkdir },
    { label: t('刷新', 'Refresh'), action: () => load(path.value) },
    { sep: true },
    { label: showHidden.value ? t('隐藏隐藏文件', 'Hide dotfiles') : t('显示隐藏文件', 'Show dotfiles'), action: async () => { showHidden.value = !showHidden.value; await load(path.value) } },
  ])
}

watch(() => [props.sessionId, props.assetId, props.backend], () => load(props.initialPath || '~'))
watch(() => props.initialPath, (value) => { if (value) load(value) })
onMounted(() => load('~'))
</script>

<template>
  <div class="file-pane" @dragover.prevent="dropActive = true" @dragleave="dropActive = false" @drop.prevent="onDrop">
    <div class="file-head">
      <button class="icon-btn sm" :title="t('返回上级', 'Parent')" @click="load(parent)"><ArrowLeft :size="14" /></button>
      <button class="icon-btn sm" :title="t('主目录', 'Home')" @click="load(home)"><HardDriveDownload :size="14" /></button>
      <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="load(path)"><RefreshCw :size="14" :class="{ 'tool-spin': loading }" /></button>
      <div class="file-path">
        <input
          v-model="pathInput"
          class="input mono"
          spellcheck="false"
          @keyup.enter="load(pathInput)"
          @focus="$event.target.select()"
        />
      </div>
      <button class="icon-btn sm" :title="t('搜索', 'Search')" @click="searchOpen = !searchOpen; search = ''"><Search :size="14" /></button>
      <button class="icon-btn sm" :title="t('上传', 'Upload')" @click="fileInput?.click()"><Upload :size="14" /></button>
      <button class="icon-btn sm" :title="t('更多', 'More')" @click="backgroundMenu"><MoreHorizontal :size="14" /></button>
    </div>
    <div v-if="searchOpen" style="padding: 6px 8px; border-bottom: 1px solid var(--border)">
      <div class="searchbox">
        <span class="icon"><Search :size="13" /></span>
        <input v-model="search" class="input" :placeholder="t('筛选当前目录', 'Filter current folder')" autofocus />
      </div>
    </div>
    <div v-if="selection.length" class="file-selbar">
      <span class="tiny">{{ t('已选', 'Selected') }} <b>{{ selection.length }}</b> {{ t('项', 'item(s)') }}</span>
      <button class="btn sm" :disabled="!canArchive" @click="openCompressDialog">
        <FileArchive :size="12" /> {{ t('压缩', 'Compress') }}
      </button>
      <button v-if="selectedSingleArchive" class="btn sm" @click="openExtractDialog(selection[0])">
        <PackageOpen :size="12" /> {{ t('解压', 'Extract') }}
      </button>
      <button class="btn sm danger" @click="removeSelection">
        <Trash2 :size="12" /> {{ t('删除', 'Delete') }}
      </button>
      <span class="grow" />
      <button class="btn ghost sm" @click="clearSelection">{{ t('取消选择', 'Clear') }}</button>
    </div>
    <div class="file-cols">
      <span style="flex: 1">{{ title || t('名称', 'Name') }}</span>
      <span class="size">{{ t('大小', 'Size') }}</span>
      <span class="mtime">{{ t('修改时间', 'Modified') }}</span>
      <span class="mode">{{ t('权限', 'Mode') }}</span>
    </div>

    <div class="file-list" @contextmenu.self.prevent="backgroundMenu">
      <div v-if="error" class="alert error" style="margin: 10px">{{ error }}</div>
      <div v-if="!loading && !error && !visible.length" class="center-hint" style="padding: 30px; flex-direction: column; gap: 8px">
        <Folder :size="30" style="opacity: .4" />
        <span class="tiny">{{ t('目录为空', 'Empty folder') }}</span>
      </div>
      <div
        v-for="entry in visible"
        :key="entry.path"
        class="file-row"
        :class="{ selected: isSelected(entry) }"
        @click="onRowClick($event, entry)"
        @dblclick="enter(entry)"
        @contextmenu="openMenu($event, entry)"
      >
        <span class="file-name">
          <component :is="fileIcon(entry)" :size="14" :style="{ color: entry.isDir ? '#d9b45a' : 'var(--text-3)' }" />
          <span class="ellipsis">{{ entry.name }}</span>
          <span v-if="entry.isLink" class="tiny">→</span>
        </span>
        <span class="size">{{ entry.isDir ? '-' : formatBytes(entry.size) }}</span>
        <span class="mtime">{{ shortTime(entry.mtime) }}</span>
        <span class="mode">{{ entry.permissions?.replace('0o', '') }}</span>
      </div>
    </div>

    <div class="file-foot">
      <span>{{ t('共', 'Total') }} {{ visible.length }} {{ t('项', 'item(s)') }}</span>
      <span v-if="selection.length" class="tiny">· {{ t('已选', 'Selected') }} {{ selection.length }}</span>
      <span class="grow" />
      <span v-if="uploading" class="row" style="gap: 5px"><RefreshCw :size="11" class="tool-spin" /> {{ t('上传中…', 'Uploading…') }}</span>
      <span v-if="dropActive" style="color: var(--accent)">{{ t('松开以上传', 'Drop to upload') }}</span>
    </div>

    <input ref="fileInput" type="file" multiple class="hidden" @change="onFilePick" />
    <input ref="scpInput" type="file" multiple class="hidden" @change="onScpPick" />

    <!-- compress -->
    <div v-if="compressOpen" class="modal-mask" @mousedown.self="!compressBusy && (compressOpen = false)">
      <div class="modal" style="width: 560px">
        <div class="modal-head">
          <FileArchive :size="15" style="color: var(--accent)" />
          <span class="modal-title">{{ t('压缩选中项', 'Compress selection') }}</span>
          <button class="icon-btn sm" :disabled="compressBusy" @click="compressOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="field">
            <label>{{ t('源文件 / 文件夹', 'Sources') }}</label>
            <div class="archive-chips">
              <span v-for="entry in selection" :key="entry.path" class="badge" :title="entry.path">
                <Folder v-if="entry.isDir" :size="11" />{{ entry.name }}
              </span>
            </div>
          </div>
          <div class="row" style="gap: 10px; margin-top: 12px; align-items: flex-end">
            <div class="field" style="flex: 1">
              <label>{{ t('格式', 'Format') }} <Loader2 v-if="formatsLoading" :size="11" class="tool-spin" /></label>
              <select v-model="compressForm.format" class="select" :disabled="compressBusy || formatsLoading" @change="onCompressFormatChange">
                <option
                  v-for="item in compressFormats"
                  :key="item.id"
                  :value="item.id"
                  :disabled="!item.available"
                >
                  {{ item.label }}<template v-if="!item.available"> — {{ t('缺少', 'missing') }} {{ item.missingTools.join(', ') }}</template>
                </option>
              </select>
            </div>
            <div class="field" style="flex: 1.2">
              <label>{{ t('归档名称', 'Archive name') }}</label>
              <input
                v-model="compressForm.name"
                class="input mono"
                spellcheck="false"
                :disabled="compressBusy"
                @input="onArchiveNameInput"
                @keyup.enter="submitCompress"
              />
            </div>
          </div>
          <div class="field" style="margin-top: 10px">
            <label>{{ t('保存到目录', 'Destination folder') }}</label>
            <input v-model="compressForm.targetDir" class="input mono" spellcheck="false" :disabled="compressBusy" />
          </div>
          <label class="row" style="gap: 6px; margin-top: 12px; font-size: 12px; cursor: pointer">
            <input v-model="compressForm.overwrite" type="checkbox" :disabled="compressBusy" />
            {{ t('覆盖同名归档', 'Overwrite an existing archive') }}
          </label>
          <div class="alert info" style="margin-top: 12px; font-size: 12px">
            {{ t('在远端主机上直接调用 tar/zip/7z 打包，不经过浏览器；大目录可能需要较长时间。', 'Runs tar/zip/7z on the remote host, not through the browser; large trees may take a while.') }}
          </div>
        </div>
        <div class="modal-foot">
          <span class="tiny" style="margin-right: auto">{{ compressFormats.find((item) => item.id === compressForm.format)?.label }}</span>
          <button class="btn" :disabled="compressBusy" @click="compressOpen = false">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" :disabled="compressBusy || formatsLoading" @click="submitCompress">
            <Loader2 v-if="compressBusy" :size="13" class="tool-spin" /><Archive v-else :size="13" />
            {{ compressBusy ? t('压缩中…', 'Compressing…') : t('开始压缩', 'Compress') }}
          </button>
        </div>
      </div>
    </div>

    <!-- extract -->
    <div v-if="extractOpen" class="modal-mask" @mousedown.self="!extractBusy && (extractOpen = false)">
      <div class="modal" style="width: 600px">
        <div class="modal-head">
          <PackageOpen :size="15" style="color: var(--accent)" />
          <span class="modal-title">{{ t('解压归档', 'Extract archive') }}</span>
          <button class="icon-btn sm" :disabled="extractBusy" @click="extractOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="field">
            <label>{{ t('归档文件', 'Archive') }}</label>
            <input class="input mono" :value="extractTarget?.path" disabled />
          </div>
          <div v-if="extractLoading" class="row" style="gap: 8px; margin-top: 12px; font-size: 12px">
            <Loader2 :size="13" class="tool-spin" /> {{ t('正在读取归档信息…', 'Reading archive…') }}
          </div>
          <div v-else-if="extractError" class="alert error" style="margin-top: 12px; font-size: 12px">
            {{ extractError }}
          </div>
          <div v-else-if="extractInfo" class="archive-info">
            <span class="badge accent">{{ extractInfo.format }}</span>
            <span class="badge">{{ extractInfo.sizeText }}</span>
            <span class="badge">
              {{ extractInfo.partial ? '≥ ' : '' }}{{ extractInfo.members }} {{ t('条目', 'entries') }}
            </span>
            <span class="badge">via {{ extractInfo.tool }}</span>
          </div>
          <div v-if="extractInfo?.sample?.length" class="archive-sample mono">
            <div v-for="name in extractInfo.sample" :key="name" class="ellipsis">{{ name }}</div>
            <div v-if="extractInfo.members > extractInfo.sample.length" class="tiny">
              … {{ t('另有', 'and') }} {{ extractInfo.members - extractInfo.sample.length }} {{ t('项', 'more') }}
            </div>
          </div>
          <div v-if="extractInfo?.unsafe" class="alert error" style="margin-top: 12px; font-size: 12px">
            {{ t('归档包含越界路径，已禁止解压：', 'Archive contains unsafe paths; extraction is blocked:') }}
            <span class="mono">{{ extractInfo.unsafeSample.join(', ') }}</span>
          </div>
          <div style="margin-top: 14px; display: flex; flex-direction: column; gap: 8px">
            <label class="row" style="gap: 6px; font-size: 12px; cursor: pointer">
              <input v-model="extractMode" type="radio" value="folder" :disabled="extractBusy" @change="applyExtractMode" />
              {{ t('解压到以归档命名的新文件夹', 'Extract into a new folder named after the archive') }}
            </label>
            <label class="row" style="gap: 6px; font-size: 12px; cursor: pointer">
              <input v-model="extractMode" type="radio" value="here" :disabled="extractBusy" @change="applyExtractMode" />
              {{ t('解压到当前目录', 'Extract into the current folder') }}
            </label>
          </div>
          <div class="field" style="margin-top: 10px">
            <label>{{ t('目标目录', 'Destination folder') }}</label>
            <input v-model="extractForm.targetDir" class="input mono" spellcheck="false" :disabled="extractBusy" />
          </div>
          <label class="row" style="gap: 6px; margin-top: 10px; font-size: 12px; cursor: pointer">
            <input v-model="extractForm.create" type="checkbox" :disabled="extractBusy" />
            {{ t('目标目录不存在时自动创建', 'Create the destination folder if missing') }}
          </label>
          <label class="row" style="gap: 6px; margin-top: 12px; font-size: 12px; cursor: pointer">
            <input v-model="extractForm.overwrite" type="checkbox" :disabled="extractBusy" />
            {{ t('覆盖已存在的文件', 'Overwrite existing files') }}
          </label>
        </div>
        <div class="modal-foot">
          <span class="tiny" style="margin-right: auto">
            <template v-if="extractInfo?.memberCheck === 'partial'">{{ t('成员校验不完整', 'Member check partial') }}</template>
          </span>
          <button class="btn" :disabled="extractBusy" @click="extractOpen = false">{{ t('取消', 'Cancel') }}</button>
          <button
            class="btn primary"
            :disabled="extractBusy || extractLoading || !!extractError || extractInfo?.unsafe"
            @click="submitExtract"
          >
            <Loader2 v-if="extractBusy" :size="13" class="tool-spin" /><PackageOpen v-else :size="13" />
            {{ extractBusy ? t('解压中…', 'Extracting…') : t('开始解压', 'Extract') }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="transferOpen" class="modal-mask" @mousedown.self="transferOpen = false">
      <div class="modal" style="width: 520px">
        <div class="modal-head">
          <FolderTree :size="15" />
          <span class="modal-title">{{ t('跨服务器传输', 'Cross-server transfer') }}</span>
          <button class="icon-btn sm" @click="transferOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="field">
            <label>{{ t('源路径', 'Source path') }}</label>
            <input class="input mono" :value="transferEntry?.path" disabled />
          </div>
          <div class="field" style="margin-top: 10px">
            <label>{{ t('目标服务器', 'Target server') }}</label>
            <select v-model="transferTargetId" class="select">
              <option v-for="a in sshTargets" :key="a.id" :value="a.id">{{ a.name }}</option>
            </select>
          </div>
          <div class="field" style="margin-top: 10px">
            <label>{{ t('目标路径', 'Target path') }}</label>
            <input v-model="transferTargetPath" class="input mono" />
          </div>
          <label class="row" style="gap: 6px; margin-top: 12px; font-size: 12px; cursor: pointer">
            <input v-model="transferOverwrite" type="checkbox" /> {{ t('覆盖已存在的文件', 'Overwrite existing files') }}
          </label>
          <div class="alert info" style="margin-top: 12px; font-size: 12px">
            {{ t('目录会递归传输；文件在两个 SSH/SFTP 服务器之间直接中转，不经过浏览器。', 'Directories are recursive; bytes flow through the backend, not the browser.') }}
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="transferOpen = false">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" :disabled="transferBusy || !sshTargets.length" @click="runTransfer">
            <Loader2 v-if="transferBusy" :size="13" class="tool-spin" /><Upload v-else :size="13" /> {{ t('开始传输', 'Transfer') }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="editState" class="modal-mask" @mousedown.self="editState = null">
      <div class="modal" style="width: 780px; height: 78vh">
        <div class="modal-head">
          <FileCode2 :size="15" style="color: var(--accent)" />
          <span class="modal-title mono" style="font-size: 12px">{{ editState.path }}</span>
          <span class="badge">{{ editState.encoding }}</span>
          <button class="icon-btn sm" @click="editState = null"><X :size="14" /></button>
        </div>
        <textarea
          v-model="editState.content"
          class="textarea"
          style="flex: 1; border: 0; border-radius: 0; resize: none; padding: 12px; font-size: 12px; background: var(--bg)"
          spellcheck="false"
        />
        <div class="modal-foot">
          <span class="tiny">{{ editState.content.length }} chars</span>
          <span class="grow" />
          <button class="btn" @click="editState = null">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" :disabled="saving" @click="saveEditor">{{ t('保存', 'Save') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
