<script setup>
import { computed } from 'vue'
import { FolderTree, ShieldAlert } from 'lucide-vue-next'
import FilePane from '../components/FilePane.vue'
import { assetMap, t } from '../lib/store'

const props = defineProps({ tab: { type: Object, required: true } })
const asset = computed(() => assetMap.value.get(props.tab.assetId))
const cfg = computed(() => asset.value?.config || {})
const protocol = computed(() => (cfg.value.protocol || 'ftp').toUpperCase())
</script>

<template>
  <div class="view" style="flex-direction: column; min-height: 0">
    <div class="toolbar">
      <FolderTree :size="14" style="color: var(--accent)" />
      <b style="font-size: 12.5px">{{ asset?.name || t('FTP', 'FTP') }}</b>
      <span class="badge">{{ protocol }}</span>
      <span class="tiny mono">{{ cfg.host }}:{{ cfg.port }}</span>
      <span class="grow" style="flex: 1" />
      <span v-if="protocol === 'FTP'" class="tiny row" style="gap: 4px; color: var(--warning, #f0a35e)">
        <ShieldAlert :size="12" /> {{ t('明文传输，不加密', 'Plaintext transfer (no encryption)') }}
      </span>
      <span class="tiny">{{ t('双击编辑 · 右键更多操作', 'Double-click to edit · right-click for more') }}</span>
    </div>
    <div style="flex: 1; min-height: 0; display: flex">
      <FilePane :asset-id="tab.assetId" backend="ftp" :title="t('远程文件', 'Remote files')" />
    </div>
  </div>
</template>
