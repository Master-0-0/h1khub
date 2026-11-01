<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import {
  DatabaseZap, HeartPulse, Layers, Loader2, Plus, RefreshCw, Search, Terminal, Trash2, X,
} from 'lucide-vue-next'
import { api } from '../lib/api'
import { assetMap, t, toast } from '../lib/store'
import { confirmDialog, promptDialog } from '../lib/ui'
import { formatBytes } from '../lib/format'
import RedisBatchDialog from '../components/RedisBatchDialog.vue'

const props = defineProps({ tab: { type: Object, required: true } })

const loading = ref(true)
const error = ref('')
const databases = ref([])
const currentDb = ref(0)
const keys = ref([])
const cursor = ref('0')
const scanning = ref(false)
const pattern = ref('*')
const selectedKey = ref('')
const keyInfo = ref(null)
const keyValue = ref(null)
const keyLoading = ref(false)
const pane = ref('value')
const consoleCommand = ref('')
const consoleHistory = ref([])
const consoleRunning = ref(false)
const realtime = ref(null)
const realtimeLoading = ref(false)
const newValue = ref('')
const jsonError = ref('')
const ttlInput = ref('')
const newField = ref({ field: '', value: '' })
const newMember = ref({ value: '', score: '0' })
const createOpen = ref(false)
const createForm = ref({ name: '', type: 'string' })
const batchOpen = ref(false)

const asset = computed(() => assetMap.value.get(props.tab.assetId))
const currentDbInfo = computed(() => databases.value.find((d) => d.index === currentDb.value))

const typeBadge = (type) => ({ string: 'accent', hash: 'green', list: 'orange', set: 'red', zset: 'green', stream: 'accent' }[type] || '')

async function init() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.post('/api/redis/databases', { assetId: props.tab.assetId })
    databases.value = res.databases || []
    const cfgDb = Number(asset.value?.config?.db || 0)
    currentDb.value = databases.value.some((d) => d.index === cfgDb) ? cfgDb : databases.value[0]?.index || 0
    await scan(true)
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    loading.value = false
  }
}

async function scan(reset = false) {
  if (scanning.value) return
  scanning.value = true
  try {
    const res = await api.post('/api/redis/scan', {
      assetId: props.tab.assetId,
      db: currentDb.value,
      cursor: reset ? '0' : cursor.value,
      pattern: pattern.value || '*',
      count: 300,
    })
    if (reset) keys.value = []
    const seen = new Set(keys.value.map((k) => k.key))
    for (const key of res.keys || []) if (!seen.has(key.key)) keys.value.push(key)
    cursor.value = res.cursor
    error.value = ''
  } catch (err) {
    error.value = String(err.message || err)
  } finally {
    scanning.value = false
  }
}

async function selectKey(key) {
  selectedKey.value = key
  keyLoading.value = true
  pane.value = 'value'
  try {
    const res = await api.post('/api/redis/key', { assetId: props.tab.assetId, db: currentDb.value, key })
    keyInfo.value = res.info
    keyValue.value = res.value
    const isJson = String(res.value?.type || '').toLowerCase().startsWith('rejson')
    newValue.value = isJson
      ? JSON.stringify(res.value.value ?? null, null, 2)
      : typeof res.value.value === 'string' ? res.value.value : ''
    jsonError.value = ''
    ttlInput.value = res.info?.ttl > 0 ? String(res.info.ttl) : ''
    newField.value = { field: '', value: '' }
    newMember.value = { value: '', score: '0' }
  } catch (err) {
    toast(String(err.message || err), 'error')
    keyInfo.value = null
    keyValue.value = null
  } finally {
    keyLoading.value = false
  }
}

async function refreshKey() {
  if (!selectedKey.value) return
  await selectKey(selectedKey.value)
  await scan(true)
}

async function act(action, payload = {}) {
  try {
    await api.post(`/api/redis/${action}`, { assetId: props.tab.assetId, db: currentDb.value, key: selectedKey.value, ...payload })
    await refreshKey()
    return true
  } catch (err) {
    toast(String(err.message || err), 'error')
    return false
  }
}

async function saveString() {
  if (await act('set-string', { value: newValue.value, ttl: ttlInput.value ? Number(ttlInput.value) : undefined })) {
    toast(t('已保存', 'Saved'), 'success')
  }
}

async function saveJson() {
  jsonError.value = ''
  try {
    JSON.parse(newValue.value)
  } catch (err) {
    jsonError.value = String(err.message || err)
    return
  }
  if (await act('json-set', { value: newValue.value })) {
    toast(t('JSON 已保存（JSON.SET）', 'JSON saved (JSON.SET)'), 'success')
    await refreshKey()
  }
}

async function saveTtl() {
  if (await act('expire', { ttl: Number(ttlInput.value || 0) })) toast(t('TTL 已更新', 'TTL updated'), 'success')
}

async function addHash() {
  if (!newField.value.field) return
  if (await act('hash-set', { field: newField.value.field, value: newField.value.value })) {
    newField.value = { field: '', value: '' }
  }
}

async function saveHashField(field, value) {
  await act('hash-set', { field, value })
}

async function removeHash(field) {
  await act('hash-del', { field })
}

async function addList(tail = true) {
  if (!newValue.value) return
  if (await act('list-push', { value: newValue.value, tail })) newValue.value = ''
}

async function removeListItem(index) {
  await act('list-remove', { index })
}

async function saveList(index, value) {
  await act('list-set', { index, value })
}

async function addSet() {
  if (!newMember.value.value) return
  if (await act('set-add', { member: newMember.value.value })) newMember.value.value = ''
}

async function removeSet(member) {
  await act('set-remove', { member })
}

async function addZset() {
  if (!newMember.value.value) return
  if (await act('zset-add', { member: newMember.value.value, score: Number(newMember.value.score || 0) })) newMember.value.value = ''
}

async function removeZset(member) {
  await act('zset-remove', { member })
}

async function removeKey() {
  if (!selectedKey.value) return
  const ok = await confirmDialog({
    title: t('删除键', 'Delete key'),
    message: t(`确定删除键「${selectedKey.value}」吗？`, `Delete key "${selectedKey.value}"?`),
    danger: true,
    okText: t('删除', 'Delete'),
  })
  if (!ok) return
  try {
    await api.post('/api/redis/delete', { assetId: props.tab.assetId, db: currentDb.value, keys: [selectedKey.value] })
    toast(t('已删除', 'Deleted'), 'success')
    selectedKey.value = ''
    keyInfo.value = null
    keyValue.value = null
    await scan(true)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function renameKey() {
  const name = await promptDialog({
    title: t('重命名键', 'Rename key'),
    message: t('新键名', 'New key name'),
    value: selectedKey.value,
  })
  if (!name || name === selectedKey.value) return
  try {
    await api.post('/api/redis/rename', { assetId: props.tab.assetId, db: currentDb.value, key: selectedKey.value, target: name })
    selectedKey.value = name
    await scan(true)
    await selectKey(name)
    toast(t('已重命名', 'Renamed'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function createKey() {
  if (!createForm.value.name) return
  try {
    await api.post('/api/redis/create', {
      assetId: props.tab.assetId,
      db: currentDb.value,
      key: createForm.value.name,
      type: createForm.value.type,
    })
    createOpen.value = false
    toast(t('键已创建', 'Key created'), 'success')
    await scan(true)
    await selectKey(createForm.value.name)
    createForm.value = { name: '', type: 'string' }
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function runConsole() {
  const command = consoleCommand.value.trim()
  if (!command) return
  consoleRunning.value = true
  consoleHistory.value.push({ command, output: null, error: false })
  const entry = consoleHistory.value[consoleHistory.value.length - 1]
  try {
    const res = await api.post('/api/redis/console', { assetId: props.tab.assetId, db: currentDb.value, command })
    entry.output = JSON.stringify(res.result, null, 2)
  } catch (err) {
    entry.output = String(err.message || err)
    entry.error = true
  } finally {
    consoleRunning.value = false
    consoleCommand.value = ''
  }
}

async function loadRealtime() {
  realtimeLoading.value = true
  try {
    const res = await api.post('/api/redis/realtime', { assetId: props.tab.assetId, db: currentDb.value })
    realtime.value = res
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    realtimeLoading.value = false
  }
}

function switchDb(index) {
  currentDb.value = index
  selectedKey.value = ''
  keyInfo.value = null
  keyValue.value = null
  keys.value = []
  scan(true)
}

watch(pane, (value) => {
  if (value === 'realtime' && !realtime.value) loadRealtime()
})

onMounted(init)
</script>

<template>
  <div class="view">
    <div class="redis-layout">
      <aside class="redis-side">
        <div class="redis-side-head">
          <DatabaseZap :size="15" style="color: #e05d5d" />
          <select class="select" style="height: 26px; width: 92px; font-size: 12px" :value="currentDb" @change="switchDb(Number($event.target.value))">
            <option v-for="db in databases" :key="db.index" :value="db.index">db{{ db.index }} ({{ db.keys }})</option>
          </select>
          <div class="searchbox" style="flex: 1">
            <span class="icon"><Search :size="13" /></span>
            <input v-model="pattern" class="input" style="height: 26px" placeholder="*" @keyup.enter="scan(true)" />
          </div>
          <button class="icon-btn sm" :title="t('刷新', 'Refresh')" @click="scan(true)"><RefreshCw :size="13" :class="{ 'tool-spin': scanning }" /></button>
          <button class="icon-btn sm" :title="t('批量操作', 'Batch operations')" @click="batchOpen = true"><Layers :size="14" /></button>
          <button class="icon-btn sm" :title="t('新建键', 'New key')" @click="createOpen = true"><Plus :size="14" /></button>
        </div>
        <div class="file-cols" style="height: 24px">
          <span style="flex: 1">{{ t('键', 'Key') }}</span>
          <span style="width: 58px; text-align: right">{{ t('类型', 'Type') }}</span>
          <span style="width: 44px; text-align: right">TTL</span>
        </div>
        <div class="key-list">
          <div v-if="error" class="alert error" style="margin: 8px">{{ error }}</div>
          <div v-if="loading" class="center-hint" style="padding: 24px"><Loader2 :size="16" class="tool-spin" /></div>
          <div
            v-for="key in keys"
            :key="key.key"
            class="key-row"
            :class="{ selected: selectedKey === key.key }"
            @click="selectKey(key.key)"
          >
            <span class="kname" :title="key.key">{{ key.key }}</span>
            <span class="ktype">{{ key.type }}</span>
            <span class="tiny" style="width: 44px; text-align: right">{{ key.ttl > 0 ? key.ttl + 's' : '—' }}</span>
          </div>
          <div v-if="!loading && !keys.length && !error" class="center-hint" style="padding: 20px; font-size: 12px">
            {{ t('没有匹配的键', 'No keys') }}
          </div>
          <div v-if="cursor !== '0'" style="padding: 8px; text-align: center">
            <button class="btn sm" :disabled="scanning" @click="scan(false)">
              <Loader2 v-if="scanning" :size="12" class="tool-spin" />
              {{ t('加载更多', 'Load more') }}
            </button>
          </div>
        </div>
        <div class="file-foot">
          <span>{{ currentDbInfo?.keys ?? keys.length }} {{ t('个键', 'keys') }}</span>
          <span class="grow" />
          <span v-if="currentDbInfo?.expires">TTL {{ currentDbInfo.expires }}</span>
        </div>
      </aside>

      <div class="redis-main">
        <div class="subtabs">
          <div class="subtab" :class="{ active: pane === 'value' }" @click="pane = 'value'">{{ t('值', 'Value') }}</div>
          <div class="subtab" :class="{ active: pane === 'console' }" @click="pane = 'console'"><Terminal :size="12" /> {{ t('控制台', 'Console') }}</div>
          <div class="subtab" :class="{ active: pane === 'realtime' }" @click="pane = 'realtime'"><HeartPulse :size="12" /> {{ t('实时信息', 'Realtime') }}</div>
          <span style="flex: 1" />
          <button v-if="selectedKey && pane === 'value'" class="btn ghost sm" @click="renameKey">{{ t('重命名', 'Rename') }}</button>
          <button v-if="selectedKey && pane === 'value'" class="btn ghost sm" @click="removeKey"><Trash2 :size="12" /> {{ t('删除', 'Delete') }}</button>
        </div>

        <template v-if="pane === 'value'">
          <template v-if="selectedKey">
            <div class="toolbar">
              <span class="mono" style="font-size: 12.5px; font-weight: 600">{{ selectedKey }}</span>
              <span class="badge" :class="typeBadge(keyInfo?.type)">{{ keyInfo?.type }}</span>
              <span class="tiny">{{ keyInfo?.memoryText }}</span>
              <span class="tiny">{{ keyInfo?.encoding }}</span>
              <span class="grow" style="flex: 1" />
              <span class="tiny">TTL</span>
              <input v-model="ttlInput" class="input" style="width: 80px; height: 25px" placeholder="-1" />
              <button class="btn sm ghost" @click="saveTtl">{{ t('设置', 'Set') }}</button>
              <button class="icon-btn sm" @click="refreshKey"><RefreshCw :size="13" /></button>
            </div>
            <div v-if="keyLoading" class="center-hint"><Loader2 :size="18" class="tool-spin" /></div>
            <div v-else style="flex: 1; overflow: auto; padding: 14px">
              <!-- JSON (RedisJSON module) -->
              <template v-if="String(keyValue?.type || '').toLowerCase().startsWith('rejson')">
                <div class="field">
                  <label>{{ t('JSON 文档（JSON.GET / JSON.SET）', 'JSON document (JSON.GET / JSON.SET)') }}</label>
                  <textarea v-model="newValue" class="textarea" rows="14" spellcheck="false" style="font-family: monospace; font-size: 12px" />
                </div>
                <div class="row mt12" style="gap: 8px; align-items: center">
                  <button class="btn primary sm" @click="saveJson">{{ t('保存 JSON', 'Save JSON') }}</button>
                  <span v-if="jsonError" class="tiny" style="color: #e05d5d">{{ jsonError }}</span>
                </div>
              </template>

              <!-- string -->
              <template v-else-if="keyValue?.type === 'string'">
                <div class="field">
                  <label>{{ t('字符串值', 'String value') }}</label>
                  <textarea v-model="newValue" class="textarea" rows="12" spellcheck="false" style="font-size: 12px" />
                </div>
                <div class="row mt12" style="gap: 8px">
                  <button class="btn primary sm" @click="saveString">{{ t('保存', 'Save') }}</button>
                  <span class="tiny">{{ newValue.length }} chars</span>
                </div>
              </template>

              <!-- hash -->
              <template v-else-if="keyValue?.type === 'hash'">
                <table class="kv-table">
                  <thead><tr><th>{{ t('字段', 'Field') }}</th><th>{{ t('值', 'Value') }}</th><th style="width: 40px" /></tr></thead>
                  <tbody>
                    <tr v-for="item in keyValue.value" :key="item.field">
                      <td class="key">{{ item.field }}</td>
                      <td>
                        <input
                          class="input"
                          style="height: 24px; background: transparent; border-color: transparent"
                          :value="item.value"
                          @change="saveHashField(item.field, $event.target.value)"
                        />
                      </td>
                      <td><button class="icon-btn sm" @click="removeHash(item.field)"><Trash2 :size="12" /></button></td>
                    </tr>
                  </tbody>
                </table>
                <div class="row mt8" style="gap: 8px">
                  <input v-model="newField.field" class="input" style="max-width: 180px" :placeholder="t('字段', 'Field')" />
                  <input v-model="newField.value" class="input" :placeholder="t('值', 'Value')" />
                  <button class="btn sm primary" @click="addHash"><Plus :size="12" /> {{ t('添加', 'Add') }}</button>
                </div>
              </template>

              <!-- list -->
              <template v-else-if="keyValue?.type === 'list'">
                <table class="kv-table">
                  <thead><tr><th style="width: 60px">#</th><th>{{ t('值', 'Value') }}</th><th style="width: 80px" /></tr></thead>
                  <tbody>
                    <tr v-for="item in keyValue.value" :key="item.index">
                      <td class="muted">{{ item.index }}</td>
                      <td>
                        <input class="input" style="height: 24px; background: transparent; border-color: transparent" :value="item.value" @change="saveList(item.index, $event.target.value)" />
                      </td>
                      <td class="row" style="gap: 2px">
                        <button class="icon-btn sm" @click="removeListItem(item.index)"><Trash2 :size="12" /></button>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div class="row mt8" style="gap: 8px">
                  <input v-model="newValue" class="input" :placeholder="t('追加值', 'Value to append')" />
                  <button class="btn sm primary" @click="addList(true)">RPUSH</button>
                  <button class="btn sm" @click="addList(false)">LPUSH</button>
                </div>
              </template>

              <!-- set -->
              <template v-else-if="keyValue?.type === 'set'">
                <table class="kv-table">
                  <thead><tr><th>{{ t('成员', 'Member') }}</th><th style="width: 40px" /></tr></thead>
                  <tbody>
                    <tr v-for="member in keyValue.value" :key="member">
                      <td class="key" style="width: auto">{{ member }}</td>
                      <td><button class="icon-btn sm" @click="removeSet(member)"><Trash2 :size="12" /></button></td>
                    </tr>
                  </tbody>
                </table>
                <div class="row mt8" style="gap: 8px">
                  <input v-model="newMember.value" class="input" :placeholder="t('新成员', 'New member')" />
                  <button class="btn sm primary" @click="addSet"><Plus :size="12" /> {{ t('添加', 'Add') }}</button>
                </div>
              </template>

              <!-- zset -->
              <template v-else-if="keyValue?.type === 'zset'">
                <table class="kv-table">
                  <thead><tr><th>{{ t('成员', 'Member') }}</th><th style="width: 120px">Score</th><th style="width: 40px" /></tr></thead>
                  <tbody>
                    <tr v-for="item in keyValue.value" :key="item.member">
                      <td class="key" style="width: auto">{{ item.member }}</td>
                      <td>{{ item.score }}</td>
                      <td><button class="icon-btn sm" @click="removeZset(item.member)"><Trash2 :size="12" /></button></td>
                    </tr>
                  </tbody>
                </table>
                <div class="row mt8" style="gap: 8px">
                  <input v-model="newMember.value" class="input" :placeholder="t('成员', 'Member')" />
                  <input v-model="newMember.score" class="input" style="max-width: 120px" placeholder="0" />
                  <button class="btn sm primary" @click="addZset"><Plus :size="12" /> {{ t('添加', 'Add') }}</button>
                </div>
              </template>

              <!-- stream -->
              <template v-else-if="keyValue?.type === 'stream'">
                <table class="kv-table">
                  <thead><tr><th style="width: 220px">ID</th><th>{{ t('字段', 'Fields') }}</th></tr></thead>
                  <tbody>
                    <tr v-for="item in keyValue.value" :key="item.id">
                      <td class="key">{{ item.id }}</td>
                      <td>
                        <div v-for="(value, key) in item.fields" :key="key" class="mono">{{ key }}: {{ value }}</div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </template>

              <div v-else-if="keyValue" class="alert info">{{ t('该键类型暂不支持可视编辑。', 'This key type is not editable yet.') }}</div>
            </div>
          </template>
          <div v-else class="empty-state">
            <div class="icon-wrap"><DatabaseZap :size="28" /></div>
            <div class="big">{{ asset?.name }}</div>
            <div class="muted text-sm">{{ t('在左侧选择一个键查看内容', 'Select a key on the left') }}</div>
            <div class="tiny">{{ t('共', 'total') }} {{ keys.length }} {{ t('个键', 'keys') }}</div>
          </div>
        </template>

        <template v-else-if="pane === 'console'">
          <div class="console-out" ref="consoleEl">
            <div v-if="!consoleHistory.length" class="muted">{{ t('输入 Redis 命令，例如：INFO / GET key / SCAN 0 MATCH * COUNT 100', 'Type a Redis command, e.g. INFO / GET key / SCAN 0 MATCH * COUNT 100') }}</div>
            <div v-for="(entry, index) in consoleHistory" :key="index" class="console-line">
              <div><span class="cmd">127.0.0.1:{{ asset?.config?.port || 6379 }}> </span>{{ entry.command }}</div>
              <div :class="{ err: entry.error }">{{ entry.output }}</div>
            </div>
          </div>
          <div class="console-input">
            <input v-model="consoleCommand" class="input mono" :placeholder="t('输入命令，回车执行', 'Command, press Enter')" @keyup.enter="runConsole" />
            <button class="btn primary sm" :disabled="consoleRunning" @click="runConsole">
              <Loader2 v-if="consoleRunning" :size="12" class="tool-spin" />
              {{ t('执行', 'Run') }}
            </button>
          </div>
        </template>

        <template v-else>
          <div v-if="realtimeLoading" class="center-hint"><Loader2 :size="18" class="tool-spin" /></div>
          <div v-else-if="realtime" style="padding: 16px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; overflow: auto">
            <div v-for="(value, key) in realtime.raw" :key="key" class="dcard">
              <div class="tiny">{{ key }}</div>
              <div style="font-size: 20px; font-weight: 600">{{ value }}</div>
            </div>
            <div class="dcard" style="grid-column: span 3">
              <div class="tiny">{{ t('键空间', 'Keyspace') }}</div>
              <pre class="mono" style="margin: 4px 0 0; font-size: 11.5px">{{ JSON.stringify(realtime.keyspace, null, 2) }}</pre>
            </div>
            <div class="dcard"><div class="tiny">Version</div><div>{{ realtime.version }}</div></div>
            <div class="dcard"><div class="tiny">Role</div><div>{{ realtime.roles }}</div></div>
            <div class="dcard"><div class="tiny">{{ t('运行时长', 'Uptime') }}</div><div>{{ realtime.uptime }}s</div></div>
          </div>
          <div v-else class="center-hint">{{ t('点击上方“实时信息”加载', 'Open realtime to load') }}</div>
        </template>
      </div>
    </div>

    <div v-if="createOpen" class="modal-mask" @mousedown.self="createOpen = false">
      <div class="modal" style="width: 420px">
        <div class="modal-head">
          <Plus :size="15" />
          <span class="modal-title">{{ t('新建键', 'New key') }}</span>
          <button class="icon-btn sm" @click="createOpen = false"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="field"><label>{{ t('键名', 'Key name') }}</label><input v-model="createForm.name" class="input" placeholder="user:1" /></div>
          <div class="field mt12">
            <label>{{ t('类型', 'Type') }}</label>
            <select v-model="createForm.type" class="select">
              <option v-for="ty in ['string', 'hash', 'list', 'set', 'zset', 'stream']" :key="ty" :value="ty">{{ ty }}</option>
            </select>
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="createOpen = false">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" @click="createKey">{{ t('创建', 'Create') }}</button>
        </div>
      </div>
    </div>
    <RedisBatchDialog
      v-if="batchOpen"
      :asset-id="tab.assetId"
      :db="currentDb"
      @close="batchOpen = false"
      @done="batchOpen = false; scan(true)"
    />
  </div>
</template>
