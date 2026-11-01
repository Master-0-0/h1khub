<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { FolderOpen, Link2, Star, X } from 'lucide-vue-next'
import FilePane from '../components/FilePane.vue'
import { api } from '../lib/api'
import { saveSettings, state, t, toast } from '../lib/store'

defineProps({ tab: { type: Object, required: true } })
const sshAssets = computed(() => state.assets.filter((a) => a.typeName === 'SSH'))
const remoteId = ref('')
const sessionId = ref('')
const localPath = ref('')
const remotePath = ref('')
const localKey = ref(0)
const remoteKey = ref(0)
const favorites = computed(() => state.settings.general?.localFavorites || [])

async function startRemote() {
  if (!remoteId.value) return
  try {
    const res = await api.post('/api/sessions/start', { assetId: remoteId.value, cols: 80, rows: 24 })
    sessionId.value = res.session.id
    remoteKey.value++
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}
async function pickRemote() {
  if (sessionId.value) {
    try { await api.post(`/api/sessions/${sessionId.value}/close`) } catch {}
    sessionId.value = ''
  }
  await startRemote()
}
async function addFavorite() {
  const list = new Set([...(state.settings.general?.localFavorites || []), localPath.value || '~'])
  await saveSettings({ general: { localFavorites: [...list] } })
  toast(t(`已收藏 ${localPath.value || '~'}`, `Favorited ${localPath.value || '~'}`), 'success')
}
async function removeFavorite(p) {
  await saveSettings({ general: { localFavorites: (state.settings.general?.localFavorites || []).filter((x) => x !== p) } })
}
function openFavorite(p) {
  localPath.value = p
  localKey.value++
}
function syncPath() {
  if (!localPath.value) return
  remoteKey.value++
  toast(t('已把本地路径同步到远程窗格', 'Remote pane set to local path'), 'info')
}
onMounted(() => {
  remoteId.value = sshAssets.value[0]?.id || ''
  if (remoteId.value) startRemote()
})
onBeforeUnmount(() => {
  if (sessionId.value) api.post(`/api/sessions/${sessionId.value}/close`).catch(() => {})
})
</script>

<template>
  <div class="view" style="display: flex; flex-direction: column">
    <div class="toolbar" style="height: 38px; gap: 8px; flex-wrap: wrap">
      <FolderOpen :size="15" style="color: var(--accent)" />
      <b style="font-size: 12.5px">{{ t('本地 / 远程文件', 'Local / remote files') }}</b>
      <span class="badge">{{ favorites.length }} {{ t('收藏', 'favorites') }}</span>
      <span class="grow" style="flex: 1" />
      <select v-model="remoteId" class="select" style="height: 26px; width: 170px; font-size: 11.5px" @change="pickRemote">
        <option value="">{{ t('选择 SSH 资产', 'Choose SSH asset') }}</option>
        <option v-for="a in sshAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
      </select>
      <button class="btn sm" :disabled="!sessionId" @click="syncPath"><Link2 :size="12" /> {{ t('同步路径', 'Sync path') }}</button>
      <button class="btn sm" @click="addFavorite"><Star :size="12" /> {{ t('收藏当前目录', 'Favorite current') }}</button>
    </div>
    <div v-if="favorites.length" class="row" style="gap: 6px; padding: 6px 10px; flex-wrap: wrap; border-bottom: 1px solid var(--border)">
      <span v-for="fav in favorites" :key="fav" class="badge" style="gap: 5px; cursor: pointer" @click="openFavorite(fav)">
        {{ fav }} <X :size="10" @click.stop="removeFavorite(fav)" />
      </span>
    </div>
    <div style="flex: 1; min-height: 0; display: flex">
      <div style="flex: 1; min-width: 0; border-right: 1px solid var(--border); display: flex; flex-direction: column">
        <div class="toolbar" style="height: 30px; background: var(--bg-panel); font-size: 11.5px"><b>{{ t('本地', 'Local') }}</b><span class="grow" /><span class="tiny mono">{{ localPath }}</span></div>
        <FilePane :key="localKey" backend="local" :initial-path="localPath || '~'" title="Local" style="flex: 1; min-height: 0" @path-change="(p) => (localPath = p)" />
      </div>
      <div style="flex: 1; min-width: 0; display: flex; flex-direction: column">
        <div class="toolbar" style="height: 30px; background: var(--bg-panel); font-size: 11.5px"><b>{{ t('远程', 'Remote') }}</b><span class="grow" /><span class="tiny mono">{{ remotePath }}</span></div>
        <FilePane v-if="sessionId" :key="remoteKey" backend="sftp" :session-id="sessionId" :initial-path="localPath || '~'" title="Remote" style="flex: 1; min-height: 0" @path-change="(p) => (remotePath = p)" />
        <div v-else class="center-hint" style="flex: 1">{{ t('选择 SSH 资产以打开远程窗格', 'Pick an SSH asset for the remote pane') }}</div>
      </div>
    </div>
  </div>
</template>
