<script setup>
import { computed } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import { checkUpdate, state, t } from '../lib/store'

// #91: shared "check for updates" control used by the settings About section
// and the About dialog.  It only displays the read-only /api/update/check
// result; it never downloads or installs anything.
const result = computed(() => state.updateCheck.result)
const error = computed(() => state.updateCheck.error)

async function run() {
  try {
    await checkUpdate()
  } catch {
    /* the error is rendered from state.updateCheck.error */
  }
}
</script>

<template>
  <div class="update-check-panel">
    <div class="row" style="gap: 8px; flex-wrap: wrap; align-items: center">
      <button class="btn sm update-check-btn" :disabled="state.updateCheck.checking" @click="run">
        <RefreshCw :size="13" :class="{ 'tool-spin': state.updateCheck.checking }" />
        {{ state.updateCheck.checking ? t('检查中…', 'Checking…') : t('检查更新', 'Check for updates') }}
      </button>
      <span v-if="result" class="badge update-check-badge" :class="result.hasUpdate ? 'orange' : 'green'">
        {{ result.hasUpdate ? t('有新版本', 'Update available') : t('已是最新', 'Up to date') }}
      </span>
    </div>

    <div v-if="error" class="alert error mt8 update-check-error">{{ error }}</div>

    <div v-if="result" class="update-result update-check-result">
      <div class="update-line">
        <span class="k">{{ t('当前', 'Current') }}</span>
        <span class="v">{{ result.current.version }} · build {{ result.current.build }}</span>
      </div>
      <div class="update-line">
        <span class="k">{{ t('最新', 'Latest') }}</span>
        <span class="v">{{ result.latest.version }} · build {{ result.latest.build }}</span>
      </div>
      <div class="update-line">
        <span class="k">hasUpdate</span>
        <span class="v" :class="result.hasUpdate ? 'has-update' : 'no-update'">{{ result.hasUpdate }}</span>
      </div>
      <div v-if="result.notes" class="update-notes">{{ result.notes }}</div>
      <div class="update-meta">
        {{ result.channel }} / {{ result.env }} · {{ result.manifestUrl }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.update-result { margin-top: 8px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-elev); }
.update-line { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; line-height: 1.8; }
.update-line .k { color: var(--text-3); }
.update-line .v { font-family: var(--mono); }
.update-line .has-update { color: var(--orange); }
.update-line .no-update { color: var(--green); }
.update-notes { margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--border); font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; color: var(--text-2); }
.update-meta { margin-top: 6px; font-size: 10.5px; color: var(--text-3); font-family: var(--mono); word-break: break-all; }
</style>
