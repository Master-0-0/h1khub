<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Activity, Boxes, Container, Download, FileText, FolderOpen, Image as ImageIcon, KeyRound, Layers,
  ListOrdered, Loader2, Network, PackagePlus, Pause, Pencil, Play, RefreshCw, RotateCw, Save, Search,
  Square, TerminalSquare, Trash2, Upload, X,
} from 'lucide-vue-next'
import { api, postDownload, uploadTo } from '../lib/api'
import { assetMap, openTab, t, toast } from '../lib/store'
import { confirmDialog, promptDialog } from '../lib/ui'
import { formatTime } from '../lib/format'
import ContainerFiles from '../components/ContainerFiles.vue'

const props = defineProps({ tab: { type: Object, required: true } })

const pane = ref('containers')
const loading = ref(false)
const error = ref('')
const containers = ref([])
const images = ref([])
const volumes = ref([])
const networks = ref([])
const stats = ref({})
const search = ref('')
const autoRefresh = ref(false)
const logsOpen = ref(false)
const logsContainer = ref(null)
const logsText = ref('')
const logsLoading = ref(false)
const inspectOpen = ref(false)
const inspectText = ref('')
const pullImage = ref('')
const pulling = ref(false)
const buildTag = ref('')
const buildDir = ref('')
const buildLoading = ref(false)
const historyOpen = ref(false)
const historyImage = ref('')
const historyRows = ref([])
const imageLoadInput = ref(null)
const filesOpen = ref(false)
const filesContainer = ref(null)
const topOpen = ref(false)
const topData = ref(null)
const createOpen = ref(false)
const createForm = ref({ image: '', name: '', command: '', ports: '', env: '', volumes: '', restart: '', network: '', start: true })
const creating = ref(false)
const registryOpen = ref(false)
const registryForm = ref({ registry: '', username: '', password: '' })
const registryLoading = ref(false)
const registryResult = ref(null)
const composeDir = ref('/tmp')
const composeFile = ref('docker-compose.yml')
const composeContent = ref('')
const composeOutput = ref('')
const composeServices = ref([])
const composeLoading = ref(false)
const composeBusy = ref('')
let timer = null

const asset = computed(() => (props.tab.assetId ? assetMap.value.get(props.tab.assetId) : null))
const isRemote = computed(() => (asset.value?.config?.mode || 'local') === 'ssh')

const filteredContainers = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return containers.value
  return containers.value.filter((c) => `${c.name} ${c.image} ${c.status}`.toLowerCase().includes(q))
})

async function call(action, payload = {}) {
  return api.post(`/api/docker/${action}`, { assetId: props.tab.assetId || null, ...payload })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (pane.value === 'containers') {
      const res = await call('containers')
      containers.value = res.containers || []
      try {
        const sres = await call('stats')
        const map = {}
        for (const row of sres.stats || []) map[row.id?.slice(0, 12)] = row
        stats.value = map
      } catch {}
    } else if (pane.value === 'images') {
      const res = await call('images')
      images.value = res.images || []
    } else if (pane.value === 'volumes') {
      const res = await call('volumes')
      volumes.value = res.volumes || []
    } else if (pane.value === 'networks') {
      const res = await call('networks')
      networks.value = res.networks || []
    }
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

async function action(container, act) {
  const label = { start: t('启动', 'Start'), stop: t('停止', 'Stop'), restart: t('重启', 'Restart'), remove: t('删除', 'Remove') }[act]
  if (act === 'remove') {
    const ok = await confirmDialog({
      title: t('删除容器', 'Remove container'),
      message: t(`确定删除容器「${container.name}」吗？`, `Remove container "${container.name}"?`),
      danger: true,
      okText: t('删除', 'Remove'),
    })
    if (!ok) return
  }
  try {
    await call('action', { container: container.id, action: act, force: act === 'remove' })
    toast(`${label} ${container.name}`, 'success')
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function showLogs(container) {
  logsContainer.value = container
  logsOpen.value = true
  logsText.value = ''
  logsLoading.value = true
  try {
    const res = await call('logs', { container: container.id, tail: 800 })
    logsText.value = res.logs || ''
  } catch (err) {
    logsText.value = String(err.message || err)
  } finally {
    logsLoading.value = false
  }
}

async function showInspect(container) {
  inspectOpen.value = true
  inspectText.value = '…'
  try {
    const res = await call('inspect', { container: container.id })
    inspectText.value = JSON.stringify(res.inspect, null, 2)
  } catch (err) {
    inspectText.value = String(err.message || err)
  }
}

async function openFiles(container) {
  filesContainer.value = container
  filesOpen.value = true
}

async function renameContainer(container) {
  const name = await promptDialog({
    title: t('重命名容器', 'Rename container'),
    message: t('新名称', 'New name'),
    value: container.name,
  })
  if (!name || name === container.name) return
  try {
    await call('container-rename', { container: container.id, name })
    toast(t('已重命名', 'Renamed'), 'success')
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function showTop(container) {
  topOpen.value = true
  topData.value = null
  try {
    topData.value = await call('container-top', { container: container.id })
  } catch (err) {
    topData.value = { error: String(err.message || err), header: [], rows: [] }
  }
}

async function createContainer() {
  const form = createForm.value
  if (!form.image.trim()) {
    toast(t('镜像不能为空', 'Image is required'), 'info')
    return
  }
  creating.value = true
  try {
    const env = {}
    for (const line of String(form.env || '').split('\n')) {
      const idx = line.indexOf('=')
      if (idx > 0) env[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
    }
    const res = await call('container-create', {
      image: form.image.trim(),
      name: form.name.trim(),
      command: form.command.trim(),
      ports: String(form.ports || '').split(',').map((x) => x.trim()).filter(Boolean),
      env,
      volumes: String(form.volumes || '').split(',').map((x) => x.trim()).filter(Boolean),
      restart: form.restart.trim(),
      network: form.network.trim(),
      start: form.start,
    })
    toast(`${t('容器已创建', 'Container created')} ${res.id?.slice(0, 12) || ''}`, 'success')
    createOpen.value = false
    createForm.value = { image: '', name: '', command: '', ports: '', env: '', volumes: '', restart: '', network: '', start: true }
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    creating.value = false
  }
}

async function doRegistryLogin() {
  registryLoading.value = true
  registryResult.value = null
  try {
    registryResult.value = await call('registry-login', {
      registry: registryForm.value.registry.trim(),
      username: registryForm.value.username.trim(),
      password: registryForm.value.password,
    })
    toast(t('仓库登录成功', 'Registry login succeeded'), 'success')
  } catch (err) {
    registryResult.value = { error: String(err.message || err) }
  } finally {
    registryLoading.value = false
  }
}

async function composeLoad() {
  composeLoading.value = true
  composeOutput.value = ''
  try {
    const res = await call('compose-read', { dir: composeDir.value, file: composeFile.value })
    composeContent.value = res.content || ''
    composeOutput.value = `${t('已载入', 'Loaded')} ${res.path}`
  } catch (err) {
    composeOutput.value = String(err.message || err)
  } finally {
    composeLoading.value = false
  }
}

async function composeSave() {
  composeBusy.value = 'save'
  try {
    const res = await call('compose-write', { dir: composeDir.value, file: composeFile.value, content: composeContent.value })
    toast(t('compose 文件已保存', 'Compose file saved'), 'success')
    composeOutput.value = `${t('已写入', 'Wrote')} ${res.path} (${res.size} bytes)`
  } catch (err) {
    composeOutput.value = String(err.message || err)
  } finally {
    composeBusy.value = ''
  }
}

async function composeAction(action) {
  composeBusy.value = action
  composeOutput.value = ''
  try {
    const res = await call('compose-action', { dir: composeDir.value, file: composeFile.value, action })
    composeOutput.value = res.output || `${action} ok`
    toast(`compose ${action} ${t('完成', 'done')}`, 'success')
    await composePs()
  } catch (err) {
    composeOutput.value = String(err.message || err)
  } finally {
    composeBusy.value = ''
  }
}

async function composePs() {
  try {
    const res = await call('compose-ps', { dir: composeDir.value, file: composeFile.value })
    composeServices.value = res.services || []
    if (res.raw && !res.services?.length) composeOutput.value = res.raw
  } catch (err) {
    composeServices.value = []
    composeOutput.value = String(err.message || err)
  }
}

async function openTerminal(container) {
  try {
    const res = await api.post('/api/sessions/start', {
      kind: 'docker-exec',
      container: container.id,
      assetId: props.tab.assetId || null,
      cols: 120,
      rows: 30,
    })
    openTab({
      kind: 'terminal',
      key: `docker-term:${container.id}:${Date.now()}`,
      title: `${container.name}`,
      assetId: props.tab.assetId || null,
      icon: 'container',
      params: { sessionId: res.session.id, typeName: 'Docker', container: container.name },
    })
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function buildImage() {
  if (!buildTag.value.trim() || !buildDir.value.trim()) {
    toast(t('请填写镜像标签与构建目录', 'Tag and build directory are required'), 'info')
    return
  }
  buildLoading.value = true
  try {
    await call('image-build', { tag: buildTag.value.trim(), dir: buildDir.value.trim() })
    toast(t('镜像构建完成', 'Image built'), 'success')
    buildTag.value = ''
    buildDir.value = ''
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    buildLoading.value = false
  }
}

async function showHistory(image) {
  historyOpen.value = true
  historyImage.value = `${image.repository}:${image.tag}`
  historyRows.value = []
  try {
    const res = await call('image-history', { image: historyImage.value })
    historyRows.value = res.history || []
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function saveImage(image) {
  try {
    await postDownload('/api/docker/image/save', { assetId: props.tab.assetId || null, image: `${image.repository}:${image.tag}` }, `${image.repository}_${image.tag}.tar`)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function loadImageFiles(files) {
  if (!files?.length) return
  try {
    const res = await uploadTo('/api/docker/image/load', { assetId: props.tab.assetId || '' }, files[0])
    toast(t(`镜像已导入（${res.size} 字节）`, `Image loaded (${res.size} bytes)`), 'success')
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function pruneImages() {
  const ok = await confirmDialog({
    title: t('清理悬空镜像', 'Prune images'),
    message: t('删除未被使用的悬空镜像，不影响容器。', 'Remove dangling images.'),
    okText: t('清理', 'Prune'),
  })
  if (!ok) return
  try {
    await call('image-prune', {})
    toast(t('已清理悬空镜像', 'Dangling images pruned'), 'success')
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function removeImage(image) {
  const ok = await confirmDialog({
    title: t('删除镜像', 'Remove image'),
    message: `rmi ${image.repository}:${image.tag}?`,
    danger: true,
    okText: t('删除', 'Remove'),
  })
  if (!ok) return
  try {
    await call('image-remove', { image: `${image.repository}:${image.tag}` })
    toast(t('镜像已删除', 'Image removed'), 'success')
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function doPull() {
  const name = pullImage.value.trim()
  if (!name) return
  pulling.value = true
  try {
    await call('pull', { image: name })
    toast(t('镜像已拉取', 'Image pulled'), 'success')
    pullImage.value = ''
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    pulling.value = false
  }
}

// --- 本地 OCI registry: publish a local image, pull it back through Docker ---
const localRegOpen = ref(false)
const localRegStatus = ref({ running: false, port: 5050, url: '', images: [] })
const localRegPort = ref(5050)
const localRegForm = ref({ image: '', repo: 'h1khub/local', tag: 'latest' })
const localRegBusy = ref('')
const localRegResult = ref(null)

function localReg(action, payload = {}) {
  return api.post('/api/docker/local-registry', { assetId: props.tab.assetId || null, action, ...payload })
}

async function refreshLocalRegistry() {
  try {
    localRegStatus.value = await localReg('status')
    localRegPort.value = localRegStatus.value.port || 5050
    if (!localRegForm.value.image && images.value.length) {
      const first = images.value[0]
      localRegForm.value.image = `${first.repository}:${first.tag}`
    }
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function openLocalRegistry() {
  localRegOpen.value = true
  localRegResult.value = null
  await refreshLocalRegistry()
}

async function startLocalRegistry() {
  localRegBusy.value = 'start'
  try {
    localRegStatus.value = await localReg('start', { port: Number(localRegPort.value) || 5050 })
    toast(t('本地 registry 已启动', 'Local registry started'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    localRegBusy.value = ''
  }
}

async function stopLocalRegistry() {
  localRegBusy.value = 'stop'
  try {
    localRegStatus.value = await localReg('stop')
    toast(t('本地 registry 已停止', 'Local registry stopped'), 'info')
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    localRegBusy.value = ''
  }
}

async function publishLocalImage() {
  const form = localRegForm.value
  if (!form.image) return toast(t('请选择本地镜像', 'Choose a local image'), 'info')
  localRegBusy.value = 'publish'
  try {
    const entry = await localReg('publish', { image: form.image, repo: form.repo, tag: form.tag })
    localRegResult.value = entry
    toast(t(`已发布 ${entry.name}:${entry.tag} (${entry.digest?.slice(0, 19)}…)`, 'Image published'), 'success')
    await refreshLocalRegistry()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    localRegBusy.value = ''
  }
}

async function pullLocalImage(item) {
  localRegBusy.value = `pull:${item.name}:${item.tag}`
  try {
    const res = await localReg('pull', { repo: item.name, tag: item.tag })
    localRegResult.value = { ...item, ...res }
    toast(t(`已拉取 ${res.image}`, `Pulled ${res.image}`), 'success')
    await load()
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    localRegBusy.value = ''
  }
}

function stateClass(state) {
  if (state === 'running') return 'var(--green)'
  if (state === 'paused') return 'var(--orange)'
  return 'var(--text-3)'
}

watch(autoRefresh, (on) => {
  clearInterval(timer)
  if (on) timer = setInterval(load, 5000)
})
watch(pane, (value) => {
  if (value === 'compose') {
    composeOutput.value = ''
    composeServices.value = []
    return
  }
  load()
})
onMounted(load)
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="view" style="flex-direction: column">
    <div class="toolbar">
      <Container :size="15" style="color: var(--accent)" />
      <span style="font-weight: 600; font-size: 12.5px">{{ asset?.name || t('本机 Docker', 'Local Docker') }}</span>
      <span class="badge">{{ isRemote ? t('SSH 远程', 'remote SSH') : 'local socket' }}</span>
      <div class="sep" />
      <div class="seg">
        <button :class="{ active: pane === 'containers' }" @click="pane = 'containers'">{{ t('容器', 'Containers') }}</button>
        <button :class="{ active: pane === 'images' }" @click="pane = 'images'">{{ t('镜像', 'Images') }}</button>
        <button :class="{ active: pane === 'volumes' }" @click="pane = 'volumes'">{{ t('卷', 'Volumes') }}</button>
        <button :class="{ active: pane === 'networks' }" @click="pane = 'networks'">{{ t('网络', 'Networks') }}</button>
        <button :class="{ active: pane === 'compose' }" @click="pane = 'compose'">Compose</button>
      </div>
      <span class="grow" style="flex: 1" />
      <div v-if="pane === 'containers'" class="searchbox" style="width: 200px">
        <span class="icon"><Search :size="13" /></span>
        <input v-model="search" class="input" style="height: 26px" :placeholder="t('筛选容器', 'Filter containers')" />
      </div>
      <button v-if="pane === 'containers'" class="btn sm" @click="createOpen = true"><PackagePlus :size="13" /> {{ t('创建容器', 'Create') }}</button>
      <button v-if="pane === 'containers'" class="btn sm" @click="registryOpen = true"><KeyRound :size="13" /> {{ t('登录仓库', 'Registry') }}</button>
      <button class="icon-btn sm" :class="{ active: autoRefresh }" :title="t('自动刷新（5 秒）', 'Auto refresh (5s)')" @click="autoRefresh = !autoRefresh"><Activity :size="14" /></button>
      <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="load"><RefreshCw :size="14" :class="{ 'tool-spin': loading }" /></button>
    </div>

    <div v-if="error" class="alert error" style="margin: 12px">{{ error }}</div>

    <template v-if="pane === 'containers'">
      <div v-if="!filteredContainers.length && !loading" class="empty-state">
        <div class="icon-wrap"><Container :size="26" /></div>
        <div class="big">{{ t('没有容器', 'No containers') }}</div>
        <div class="tiny">{{ t('确认 Docker 服务在运行，且当前用户有 socket 权限', 'Make sure Docker is running and the socket is accessible') }}</div>
      </div>
      <div v-else class="docker-cards">
        <div v-for="c in filteredContainers" :key="c.id" class="dcard">
          <div class="dc-head">
            <span :style="{ width: '8px', height: '8px', borderRadius: '50%', background: stateClass(c.state) }" />
            <span class="dc-name" :title="c.name">{{ c.name || c.id?.slice(0, 12) }}</span>
            <span class="badge" :class="c.state === 'running' ? 'green' : ''">{{ c.state }}</span>
          </div>
          <div class="dc-image" :title="c.image">{{ c.image }}</div>
          <div class="dc-meta">
            <span>{{ c.status }}</span>
          </div>
          <div v-if="c.ports" class="dc-meta"><span class="mono tiny">{{ c.ports }}</span></div>
          <div v-if="stats[c.id?.slice(0, 12)]" class="dc-meta">
            <span>CPU {{ stats[c.id.slice(0, 12)].cpu }}</span>
            <span>{{ stats[c.id.slice(0, 12)].mem }}</span>
          </div>
          <div class="dc-actions">
            <button v-if="c.state !== 'running'" class="btn sm ghost" :title="t('启动', 'Start')" @click="action(c, 'start')"><Play :size="12" /></button>
            <button v-else class="btn sm ghost" :title="t('停止', 'Stop')" @click="action(c, 'stop')"><Square :size="12" /></button>
            <button class="btn sm ghost" :title="t('重启', 'Restart')" @click="action(c, 'restart')"><RotateCw :size="12" /></button>
            <button v-if="c.state === 'running'" class="btn sm ghost" :title="t('暂停', 'Pause')" @click="action(c, 'pause')"><Pause :size="12" /></button>
            <button v-if="c.state === 'paused'" class="btn sm ghost" :title="t('恢复', 'Unpause')" @click="action(c, 'unpause')"><Play :size="12" /></button>
            <button class="btn sm ghost" :title="t('终端', 'Terminal')" @click="openTerminal(c)"><TerminalSquare :size="12" /></button>
            <button class="btn sm ghost" :title="t('容器文件', 'Container files')" @click="openFiles(c)"><FolderOpen :size="12" /></button>
            <button class="btn sm ghost" :title="t('重命名', 'Rename')" @click="renameContainer(c)"><Pencil :size="12" /></button>
            <button class="btn sm ghost" :title="t('进程', 'Processes')" @click="showTop(c)"><ListOrdered :size="12" /></button>
            <button class="btn sm ghost" :title="t('日志', 'Logs')" @click="showLogs(c)"><FileText :size="12" /></button>
            <button class="btn sm ghost" :title="t('详情', 'Inspect')" @click="showInspect(c)"><Layers :size="12" /></button>
            <span class="grow" style="flex: 1" />
            <button class="btn sm ghost" style="color: var(--red)" :title="t('删除', 'Remove')" @click="action(c, 'remove')"><Trash2 :size="12" /></button>
          </div>
        </div>
      </div>
    </template>

    <template v-else-if="pane === 'images'">
      <div class="toolbar" style="height: 38px; background: var(--bg-panel); gap: 6px; flex-wrap: wrap">
        <input v-model="pullImage" class="input" style="max-width: 260px" :placeholder="t('拉取镜像，例如 nginx:latest', 'Pull image, e.g. nginx:latest')" />
        <button class="btn sm" :disabled="pulling" @click="doPull">
          <Loader2 v-if="pulling" :size="12" class="tool-spin" />
          {{ t('拉取', 'Pull') }}
        </button>
        <button class="btn sm" @click="openLocalRegistry"><Boxes :size="12" /> {{ t('本地仓库', 'Local registry') }}</button>
        <div class="sep" />
        <input v-model="buildTag" class="input mono" style="width: 170px" :placeholder="t('构建标签', 'image:tag')" />
        <input v-model="buildDir" class="input mono" style="width: 160px" :placeholder="t('构建目录', 'build context')" />
        <button class="btn sm" :disabled="buildLoading" @click="buildImage">
          <Loader2 v-if="buildLoading" :size="12" class="tool-spin" /><PackagePlus v-else :size="12" /> {{ t('构建', 'Build') }}
        </button>
        <button class="btn sm" @click="imageLoadInput?.click()"><Upload :size="12" /> {{ t('导入', 'Load') }}</button>
        <input ref="imageLoadInput" type="file" accept=".tar" class="hidden" @change="loadImageFiles([...$event.target.files]); $event.target.value=''" />
        <button class="btn sm" @click="pruneImages">{{ t('清理', 'Prune') }}</button>
      </div>
      <div style="flex: 1; overflow: auto">
        <table class="grid">
          <thead><tr><th>{{ t('仓库', 'Repository') }}</th><th>Tag</th><th>ID</th><th>{{ t('大小', 'Size') }}</th><th>{{ t('创建', 'Created') }}</th><th /></tr></thead>
          <tbody>
            <tr v-for="img in images" :key="img.id + img.tag">
              <td>{{ img.repository }}</td>
              <td>{{ img.tag }}</td>
              <td class="mono">{{ img.id }}</td>
              <td>{{ img.size }}</td>
              <td>{{ img.createdSince }}</td>
              <td style="width: 110px; white-space: nowrap">
                <button class="icon-btn sm" :title="t('历史', 'History')" @click="showHistory(img)"><ListOrdered :size="13" /></button>
                <button class="icon-btn sm" :title="t('导出', 'Save')" :disabled="img.tag === '<none>'" @click="saveImage(img)"><Download :size="13" /></button>
                <button class="icon-btn sm" :title="t('删除', 'Remove')" @click="removeImage(img)"><Trash2 :size="13" /></button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <template v-else-if="pane === 'compose'">
      <div class="toolbar" style="height: 38px; background: var(--bg-panel); gap: 6px; flex-wrap: wrap">
        <span class="tiny">{{ t('项目目录', 'Project dir') }}</span>
        <input v-model="composeDir" class="input mono" style="width: 220px; height: 26px; font-size: 11.5px" placeholder="/tmp/project" />
        <input v-model="composeFile" class="input mono" style="width: 160px; height: 26px; font-size: 11.5px" />
        <button class="btn sm" :disabled="composeLoading" @click="composeLoad"><FolderOpen :size="12" /> {{ t('读取', 'Load') }}</button>
        <button class="btn sm" :disabled="composeBusy === 'save'" @click="composeSave"><Save :size="12" /> {{ t('保存', 'Save') }}</button>
        <div class="sep" />
        <button class="btn sm" :disabled="!!composeBusy" @click="composeAction('up')"><Play :size="12" /> up</button>
        <button class="btn sm" :disabled="!!composeBusy" @click="composeAction('down')"><Square :size="12" /> down</button>
        <button class="btn sm" :disabled="!!composeBusy" @click="composeAction('stop')"><Square :size="12" /> stop</button>
        <button class="btn sm" :disabled="!!composeBusy" @click="composeAction('restart')"><RotateCw :size="12" /> restart</button>
        <button class="btn sm" :disabled="!!composeBusy" @click="composePs"><RefreshCw :size="12" /> ps</button>
        <span v-if="composeBusy" class="tiny"><Loader2 :size="12" class="tool-spin" /> {{ composeBusy }}</span>
      </div>
      <div style="flex: 1; display: flex; min-height: 0">
        <div style="flex: 1.4; display: flex; flex-direction: column; min-width: 0; border-right: 1px solid var(--border)">
          <div class="tiny" style="padding: 6px 10px; border-bottom: 1px solid var(--border)">{{ t('compose 文件（保存后写入项目目录）', 'Compose file (saved to the project dir)') }}</div>
          <textarea v-model="composeContent" class="textarea mono" style="flex: 1; border: 0; border-radius: 0; resize: none; padding: 10px; font-size: 11.5px; background: var(--bg)" spellcheck="false" placeholder="services:
  web:
    image: nginx:latest" />
        </div>
        <div style="flex: 1; display: flex; flex-direction: column; min-width: 0">
          <div class="tiny" style="padding: 6px 10px; border-bottom: 1px solid var(--border)">{{ t('服务状态', 'Services') }}</div>
          <div style="flex: 1; overflow: auto">
            <table class="grid">
              <thead><tr><th>{{ t('名称', 'Name') }}</th><th>{{ t('服务', 'Service') }}</th><th>{{ t('状态', 'State') }}</th><th>{{ t('端口', 'Ports') }}</th></tr></thead>
              <tbody>
                <tr v-for="(svc, i) in composeServices" :key="i">
                  <td class="tiny">{{ svc.Name || svc.name || '-' }}</td>
                  <td class="tiny">{{ svc.Service || svc.service || '-' }}</td>
                  <td class="tiny">{{ svc.State || svc.state || '-' }}</td>
                  <td class="tiny mono">{{ svc.Publishers?.map((p) => `${p.PublishedPort}->${p.TargetPort}`).join(', ') || svc.Ports || '' }}</td>
                </tr>
                <tr v-if="!composeServices.length"><td colspan="4" class="tiny" style="text-align: center; padding: 16px">{{ t('没有运行中的 compose 服务', 'No running compose services') }}</td></tr>
              </tbody>
            </table>
          </div>
          <div class="tiny" style="padding: 6px 10px; border-top: 1px solid var(--border)">{{ t('输出', 'Output') }}</div>
          <pre class="mono" style="margin: 0; max-height: 160px; overflow: auto; padding: 8px 10px; font-size: 11px; white-space: pre-wrap; background: var(--bg-deep)">{{ composeOutput }}</pre>
        </div>
      </div>
    </template>

    <template v-else-if="pane === 'volumes'">
      <div style="flex: 1; overflow: auto; padding: 12px">
        <table class="grid">
          <thead><tr><th>{{ t('名称', 'Name') }}</th><th>Driver</th><th>Mountpoint</th></tr></thead>
          <tbody>
            <tr v-for="v in volumes" :key="v.name"><td>{{ v.name }}</td><td>{{ v.driver }}</td><td class="mono">{{ v.mountpoint }}</td></tr>
          </tbody>
        </table>
      </div>
    </template>

    <template v-else>
      <div style="flex: 1; overflow: auto; padding: 12px">
        <table class="grid">
          <thead><tr><th>{{ t('名称', 'Name') }}</th><th>ID</th><th>Driver</th><th>Scope</th></tr></thead>
          <tbody>
            <tr v-for="n in networks" :key="n.id"><td>{{ n.name }}</td><td class="mono">{{ n.id?.slice(0, 12) }}</td><td>{{ n.driver }}</td><td>{{ n.scope }}</td></tr>
          </tbody>
        </table>
      </div>
    </template>

    <div v-if="logsOpen" class="modal-mask" @mousedown.self="logsOpen = false">
      <div class="modal" style="width: 860px; height: 78vh">
        <div class="modal-head">
          <FileText :size="15" />
          <span class="modal-title">{{ t('容器日志', 'Container logs') }} · {{ logsContainer?.name }}</span>
          <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="showLogs(logsContainer)"><RefreshCw :size="14" /></button>
          <button class="icon-btn sm" @click="logsOpen = false"><X :size="14" /></button>
        </div>
        <div style="flex: 1; overflow: auto; background: var(--bg-deep); padding: 10px 12px">
          <div v-if="logsLoading" class="center-hint"><Loader2 :size="18" class="tool-spin" /></div>
          <pre v-else class="mono" style="margin: 0; font-size: 11.5px; white-space: pre-wrap; word-break: break-word">{{ logsText }}</pre>
        </div>
      </div>
    </div>

    <div v-if="inspectOpen" class="modal-mask" @mousedown.self="inspectOpen = false">
      <div class="modal" style="width: 760px; height: 80vh">
        <div class="modal-head">
          <Layers :size="15" />
          <span class="modal-title">Inspect · {{ logsContainer?.name }}</span>
          <button class="icon-btn sm" @click="inspectOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body" style="padding: 0">
          <pre class="mono" style="margin: 0; padding: 12px; font-size: 11.5px; white-space: pre-wrap; word-break: break-word">{{ inspectText }}</pre>
        </div>
      </div>
    </div>
    <div v-if="filesOpen" class="modal-mask" @mousedown.self="filesOpen = false">
      <div class="modal" style="width: 940px; height: 82vh; display: flex; flex-direction: column">
        <div class="modal-head">
          <FolderOpen :size="15" />
          <span class="modal-title">{{ t('容器文件', 'Container files') }} · {{ filesContainer?.name }}</span>
          <span class="badge mono">{{ filesContainer?.id?.slice(0, 12) }}</span>
          <span class="grow" style="flex: 1" />
          <button class="icon-btn sm" @click="filesOpen = false"><X :size="14" /></button>
        </div>
        <ContainerFiles
          v-if="filesOpen"
          :asset-id="tab.assetId || ''"
          :container="filesContainer?.id"
          :title="t('容器内文件', 'Files in container')"
          @close="filesOpen = false"
        />
      </div>
    </div>

    <div v-if="createOpen" class="modal-mask" @mousedown.self="createOpen = false">
      <div class="modal" style="width: 620px; max-height: 88vh; display: flex; flex-direction: column">
        <div class="modal-head">
          <PackagePlus :size="15" />
          <span class="modal-title">{{ t('创建容器', 'Create container') }}</span>
          <button class="icon-btn sm" @click="createOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
          <div class="field-row">
            <div class="field" style="flex: 2"><label>{{ t('镜像', 'Image') }}</label><input v-model="createForm.image" class="input mono" placeholder="alpine:latest" /></div>
            <div class="field" style="flex: 1"><label>{{ t('名称', 'Name') }}</label><input v-model="createForm.name" class="input mono" placeholder="my-container" /></div>
          </div>
          <div class="field" style="margin-top: 10px"><label>{{ t('命令', 'Command') }}</label><input v-model="createForm.command" class="input mono" placeholder="sh -c 'sleep 300'" /></div>
          <div class="field-row" style="margin-top: 10px">
            <div class="field"><label>{{ t('端口映射', 'Ports') }}</label><input v-model="createForm.ports" class="input mono" placeholder="8080:80, 5432:5432" /></div>
            <div class="field"><label>{{ t('重启策略', 'Restart') }}</label>
              <select v-model="createForm.restart" class="select">
                <option value="">no</option><option value="always">always</option><option value="unless-stopped">unless-stopped</option><option value="on-failure">on-failure</option>
              </select>
            </div>
          </div>
          <div class="field" style="margin-top: 10px"><label>{{ t('环境变量（每行 KEY=VALUE）', 'Env (KEY=VALUE per line)') }}</label><textarea v-model="createForm.env" class="textarea mono" style="height: 70px; font-size: 12px" placeholder="TZ=Asia/Shanghai" /></div>
          <div class="field" style="margin-top: 10px"><label>{{ t('卷（逗号分隔）', 'Volumes (comma separated)') }}</label><input v-model="createForm.volumes" class="input mono" placeholder="/data:/data" /></div>
          <div class="field" style="margin-top: 10px"><label>{{ t('网络', 'Network') }}</label><input v-model="createForm.network" class="input mono" placeholder="bridge" /></div>
          <label class="row" style="gap: 6px; margin-top: 12px; font-size: 12px; cursor: pointer"><input v-model="createForm.start" type="checkbox" /> {{ t('创建后立即启动', 'Start after create') }}</label>
          <div class="alert info" style="margin-top: 12px">{{ t('创建会真实启动容器；镜像必须已在本地，不会自动拉取。', 'This creates a real container; the image must exist locally.') }}</div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="createOpen = false">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" :disabled="creating" @click="createContainer">
            <Loader2 v-if="creating" :size="13" class="tool-spin" /><PackagePlus v-else :size="13" /> {{ t('创建', 'Create') }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="localRegOpen" class="modal-mask" @mousedown.self="localRegOpen = false">
      <div class="modal" style="width: 680px; max-height: 84vh; display: flex; flex-direction: column">
        <div class="modal-head">
          <Boxes :size="15" />
          <span class="modal-title">{{ t('本地 OCI 镜像仓库', 'Local OCI registry') }}</span>
          <span class="badge" :class="localRegStatus.running ? 'green' : ''">{{ localRegStatus.running ? t('运行中', 'running') : t('已停止', 'stopped') }}</span>
          <span class="grow" style="flex: 1" />
          <button class="icon-btn sm" @click="localRegOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
          <div class="alert info">
            {{ t('Docker Hub 不可达时，可把本地已有镜像发布到内置 registry，再由 Docker 拉取；全程不访问外网。', 'Publish a local image into the bundled registry, then pull it back with Docker — no internet access required.') }}
          </div>
          <div class="field-row" style="margin-top: 12px; align-items: end">
            <div class="field" style="flex: 0.6">
              <label>{{ t('端口', 'Port') }}</label>
              <input v-model.number="localRegPort" type="number" class="input" :disabled="localRegStatus.running" />
            </div>
            <div class="field" style="flex: 2">
              <label>{{ t('地址', 'Address') }}</label>
              <input class="input mono" :value="localRegStatus.url || `http://127.0.0.1:${localRegPort}`" readonly />
            </div>
            <button v-if="!localRegStatus.running" class="btn sm primary" :disabled="localRegBusy === 'start'" @click="startLocalRegistry">
              <Loader2 v-if="localRegBusy === 'start'" :size="12" class="tool-spin" /> {{ t('启动', 'Start') }}
            </button>
            <button v-else class="btn sm" :disabled="localRegBusy === 'stop'" @click="stopLocalRegistry">{{ t('停止', 'Stop') }}</button>
          </div>

          <div style="margin-top: 16px; font-weight: 600; font-size: 12.5px">{{ t('发布本地镜像', 'Publish a local image') }}</div>
          <div class="field-row" style="margin-top: 8px; align-items: end">
            <div class="field" style="flex: 1.6">
              <label>{{ t('本地镜像', 'Local image') }}</label>
              <select v-model="localRegForm.image" class="select">
                <option v-for="img in images" :key="`${img.repository}:${img.tag}`" :value="`${img.repository}:${img.tag}`">{{ img.repository }}:{{ img.tag }}</option>
              </select>
            </div>
            <div class="field" style="flex: 1.2">
              <label>{{ t('仓库名', 'Repository') }}</label>
              <input v-model="localRegForm.repo" class="input mono" placeholder="h1khub/local" />
            </div>
            <div class="field" style="flex: 0.7">
              <label>Tag</label>
              <input v-model="localRegForm.tag" class="input mono" placeholder="latest" />
            </div>
            <button class="btn sm primary" :disabled="localRegBusy === 'publish' || !localRegForm.image" @click="publishLocalImage">
              <Loader2 v-if="localRegBusy === 'publish'" :size="12" class="tool-spin" /> {{ t('发布', 'Publish') }}
            </button>
          </div>

          <div style="margin-top: 16px; font-weight: 600; font-size: 12.5px">{{ t('仓库中的镜像', 'Published images') }}</div>
          <table class="grid" style="margin-top: 6px">
            <thead><tr><th>{{ t('镜像', 'Image') }}</th><th>Digest</th><th>{{ t('层', 'Layers') }}</th><th /></tr></thead>
            <tbody>
              <tr v-for="item in localRegStatus.images" :key="`${item.name}:${item.tag}`">
                <td class="mono tiny">{{ item.name }}:{{ item.tag }}</td>
                <td class="mono tiny" :title="item.digest">{{ (item.digest || '').slice(0, 23) }}…</td>
                <td class="tiny">{{ item.layers }}</td>
                <td style="text-align: right">
                  <button class="btn sm" :disabled="!localRegStatus.running || localRegBusy === `pull:${item.name}:${item.tag}`" @click="pullLocalImage(item)">
                    <Loader2 v-if="localRegBusy === `pull:${item.name}:${item.tag}`" :size="12" class="tool-spin" /> {{ t('拉取到 Docker', 'Pull') }}
                  </button>
                </td>
              </tr>
              <tr v-if="!localRegStatus.images.length"><td colspan="4" class="tiny" style="text-align: center">{{ t('还没有发布镜像', 'Nothing published yet') }}</td></tr>
            </tbody>
          </table>

          <div v-if="localRegResult" class="alert info" style="margin-top: 12px">
            <template v-if="localRegResult.digest">
              Digest <span class="mono">{{ localRegResult.digest }}</span>
            </template>
            <template v-else-if="localRegResult.image">
              {{ t('已拉取', 'Pulled') }} <span class="mono">{{ localRegResult.image }}</span>
              · ID <span class="mono">{{ (localRegResult.imageId || '').slice(0, 19) }}</span>
            </template>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="localRegOpen = false">{{ t('关闭', 'Close') }}</button>
        </div>
      </div>
    </div>

    <div v-if="registryOpen" class="modal-mask" @mousedown.self="registryOpen = false">
      <div class="modal" style="width: 460px">
        <div class="modal-head">
          <KeyRound :size="15" />
          <span class="modal-title">{{ t('登录镜像仓库', 'Registry login') }}</span>
          <button class="icon-btn sm" @click="registryOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="field"><label>{{ t('仓库地址（留空为 Docker Hub）', 'Registry (empty = Docker Hub)') }}</label><input v-model="registryForm.registry" class="input mono" placeholder="registry.example.com" /></div>
          <div class="field" style="margin-top: 10px"><label>{{ t('用户名', 'Username') }}</label><input v-model="registryForm.username" class="input" /></div>
          <div class="field" style="margin-top: 10px"><label>{{ t('口令 / Token', 'Password / token') }}</label><input v-model="registryForm.password" type="password" class="input" /></div>
          <div v-if="registryResult" class="alert" :class="registryResult.error ? 'error' : 'info'" style="margin-top: 12px">
            {{ registryResult.error || `${t('已登录', 'Logged in')} ${registryResult.registry} (${registryResult.username})` }}
          </div>
          <div class="tiny" style="margin-top: 10px">{{ t('口令通过 --password-stdin 传递，不写入命令行历史。', 'The password is passed via --password-stdin.') }}</div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="registryOpen = false">{{ t('关闭', 'Close') }}</button>
          <button class="btn primary" :disabled="registryLoading" @click="doRegistryLogin">
            <Loader2 v-if="registryLoading" :size="13" class="tool-spin" /> {{ t('登录', 'Login') }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="historyOpen" class="modal-mask" @mousedown.self="historyOpen = false">
      <div class="modal" style="width: 760px; max-height: 80vh; display: flex; flex-direction: column">
        <div class="modal-head">
          <ListOrdered :size="15" />
          <span class="modal-title">{{ t('镜像历史', 'Image history') }} · <span class="mono">{{ historyImage }}</span></span>
          <span class="grow" style="flex: 1" />
          <button class="icon-btn sm" @click="historyOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
          <table class="grid">
            <thead><tr><th>ID</th><th>{{ t('创建', 'Created') }}</th><th>{{ t('大小', 'Size') }}</th><th>{{ t('说明', 'Comment') }}</th><th>Tags</th></tr></thead>
            <tbody>
              <tr v-for="(row, i) in historyRows" :key="i">
                <td class="mono tiny">{{ (row.id || '').slice(0, 12) }}</td>
                <td class="tiny">{{ row.created }}</td>
                <td class="tiny">{{ row.size }}</td>
                <td class="tiny">{{ row.comment || '-' }}</td>
                <td class="tiny mono">{{ row.tags }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div v-if="topOpen" class="modal-mask" @mousedown.self="topOpen = false">
      <div class="modal" style="width: 820px; max-height: 80vh; display: flex; flex-direction: column">
        <div class="modal-head">
          <ListOrdered :size="15" />
          <span class="modal-title">{{ t('容器进程', 'Container processes') }}</span>
          <span class="grow" style="flex: 1" />
          <button class="icon-btn sm" @click="topOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body" style="flex: 1; min-height: 0; overflow: auto">
          <div v-if="!topData" class="center-hint" style="padding: 30px"><Loader2 :size="18" class="tool-spin" /></div>
          <div v-else-if="topData.error" class="alert error">{{ topData.error }}</div>
          <table v-else class="grid">
            <thead><tr><th v-for="h in topData.header" :key="h">{{ h }}</th></tr></thead>
            <tbody><tr v-for="(row, i) in topData.rows" :key="i"><td v-for="(cell, j) in row" :key="j" class="mono tiny">{{ cell }}</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>
