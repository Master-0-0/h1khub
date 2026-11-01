<script setup>
import { computed, ref, watch } from 'vue'
import { Check, Copy, Loader2, Play, TerminalSquare, Users, X } from 'lucide-vue-next'
import { api } from '../lib/api'
import { state, t, toast } from '../lib/store'

const selected = ref(new Set())
const command = ref('')
const timeout = ref(60)
const running = ref(false)
const results = ref([])
const expanded = ref({})

const sshAssets = computed(() => state.assets.filter((a) => a.typeName === 'SSH'))

watch(
  () => state.batchOpen,
  (open) => {
    if (!open) return
    selected.value = new Set(sshAssets.value.map((a) => a.id))
    results.value = []
    expanded.value = {}
    if (!command.value) command.value = 'uname -a && uptime'
  },
)

function toggle(id) {
  const next = new Set(selected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selected.value = next
}

function toggleAll() {
  if (selected.value.size === sshAssets.value.length) selected.value = new Set()
  else selected.value = new Set(sshAssets.value.map((a) => a.id))
}

function expandedText(row) {
  const parts = []
  if (row.stdout) parts.push(row.stdout)
  if (row.stderr) parts.push('[stderr]\n' + row.stderr)
  if (row.error) parts.push('[error] ' + row.error)
  return parts.join('\n').trim() || '(no output)'
}

async function run() {
  if (!selected.value.size) {
    toast(t('请选择至少一个 SSH 资产', 'Select at least one SSH asset'), 'error')
    return
  }
  if (!command.value.trim()) {
    toast(t('命令不能为空', 'Command is required'), 'error')
    return
  }
  running.value = true
  results.value = []
  try {
    const res = await api.post('/api/batch/exec', {
      assetIds: [...selected.value],
      command: command.value.trim(),
      timeout: Number(timeout.value) || 60,
    })
    results.value = res.results || []
    toast(t(`已完成 ${res.results.length} 台主机`, `Completed on ${res.results.length} hosts`), res.results.every((r) => r.ok) ? 'success' : 'info')
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    running.value = false
  }
}

function copyAll() {
  const text = results.value
    .map((row) => `### ${row.name} (${row.ok ? 'OK' : 'FAIL'}${row.exitCode !== undefined ? ' exit=' + row.exitCode : ''})\n${expandedText(row)}`)
    .join('\n\n')
  navigator.clipboard?.writeText(text)
  toast(t('结果已复制', 'Results copied'), 'success')
}

const okCount = computed(() => results.value.filter((r) => r.ok).length)
</script>

<template>
  <div v-if="state.batchOpen" class="modal-mask" @mousedown.self="state.batchOpen = false">
    <div class="modal" style="width: 880px; height: 80vh">
      <div class="modal-head">
        <TerminalSquare :size="16" style="color: var(--accent)" />
        <span class="modal-title">{{ t('批量执行', 'Batch execution') }}</span>
        <span class="badge">{{ t('同时在多台 SSH 主机执行命令', 'Run a command on multiple SSH hosts') }}</span>
        <span class="grow" style="flex: 1" />
        <button class="icon-btn sm" @click="state.batchOpen = false"><X :size="14" /></button>
      </div>

      <div class="modal-body" style="display: flex; flex-direction: column; gap: 12px">
        <div>
          <div class="row" style="margin-bottom: 6px">
            <Users :size="14" style="color: var(--text-3)" />
            <span style="font-size: 12px; color: var(--text-2)">{{ t('目标主机', 'Targets') }} · {{ selected.size }}/{{ sshAssets.length }}</span>
            <span class="grow" style="flex: 1" />
            <button class="btn ghost sm" @click="toggleAll">
              {{ selected.size === sshAssets.length ? t('取消全选', 'Clear') : t('全选', 'Select all') }}
            </button>
          </div>
          <div style="max-height: 136px; overflow: auto; border: 1px solid var(--border); border-radius: 7px; padding: 4px; background: var(--bg-panel)">
            <div
              v-for="asset in sshAssets"
              :key="asset.id"
              class="tree-row"
              style="height: 28px"
              @click="toggle(asset.id)"
            >
              <span class="checkbox" :class="{ on: selected.has(asset.id) }">
                <Check v-if="selected.has(asset.id)" :size="11" />
              </span>
              <span class="tree-name">{{ asset.name }}</span>
              <span class="tiny">{{ asset.config?.username }}@{{ asset.config?.host }}</span>
            </div>
            <div v-if="!sshAssets.length" class="center-hint" style="padding: 18px; font-size: 12px">
              {{ t('还没有 SSH 资产', 'No SSH assets yet') }}
            </div>
          </div>
        </div>

        <div class="field">
          <label>{{ t('要执行的命令', 'Command') }}</label>
          <textarea v-model="command" class="textarea" rows="3" spellcheck="false" placeholder="uname -a && uptime" />
        </div>

        <div class="row" style="gap: 10px">
          <span class="tiny">{{ t('超时（秒）', 'Timeout (s)') }}</span>
          <input v-model.number="timeout" type="number" min="5" max="600" class="input" style="width: 90px; height: 26px" />
          <span class="grow" style="flex: 1" />
          <button class="btn primary" :disabled="running || !sshAssets.length" @click="run">
            <Loader2 v-if="running" :size="14" class="tool-spin" /><Play v-else :size="14" />
            {{ running ? t('执行中…', 'Running…') : t('开始执行', 'Run') }}
          </button>
        </div>

        <div v-if="results.length" style="flex: 1; min-height: 0; display: flex; flex-direction: column">
          <div class="row" style="margin-bottom: 6px">
            <span class="badge green">{{ okCount }} OK</span>
            <span class="badge red">{{ results.length - okCount }} FAIL</span>
            <span class="grow" style="flex: 1" />
            <button class="btn ghost sm" @click="copyAll"><Copy :size="12" /> {{ t('复制结果', 'Copy results') }}</button>
          </div>
          <div style="flex: 1; overflow: auto; border: 1px solid var(--border); border-radius: 7px">
            <div v-for="row in results" :key="row.assetId" style="border-bottom: 1px solid var(--border)">
              <div class="row" style="padding: 7px 10px; cursor: default" @click="expanded[row.assetId] = !expanded[row.assetId]">
                <span class="dot" :class="{ off: !row.ok }" />
                <span style="font-size: 12.5px">{{ row.name }}</span>
                <span class="badge" :class="row.ok ? 'green' : 'red'">{{ row.ok ? 'OK' : 'FAIL' }}</span>
                <span v-if="row.exitCode !== undefined" class="tiny">exit {{ row.exitCode }}</span>
                <span class="grow" style="flex: 1" />
                <span class="tiny">{{ row.elapsed }} ms</span>
              </div>
              <pre v-if="expanded[row.assetId]" class="mono" style="margin: 0; padding: 8px 12px; font-size: 11.5px; background: var(--bg-deep); white-space: pre-wrap; word-break: break-word; max-height: 220px; overflow: auto">{{ expandedText(row) }}</pre>
            </div>
          </div>
        </div>
      </div>

      <div class="modal-foot">
        <span class="tiny">{{ t('命令会并行发送到每台主机，请确认影响范围', 'Commands run in parallel on every host') }}</span>
        <span class="grow" style="flex: 1" />
        <button class="btn" @click="state.batchOpen = false">{{ t('关闭', 'Close') }}</button>
      </div>
    </div>
  </div>
</template>
