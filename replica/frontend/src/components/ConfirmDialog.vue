<script setup>
import { onBeforeUnmount, onMounted } from 'vue'
import { AlertTriangle, X } from 'lucide-vue-next'
import { dialogState } from '../lib/ui'
import { t } from '../lib/store'

function cancel() {
  dialogState.confirm?.resolve(false)
}
function accept() {
  dialogState.confirm?.resolve(true)
}
function onKey(event) {
  if (!dialogState.confirm) return
  if (event.key === 'Escape') cancel()
  if (event.key === 'Enter') accept()
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div v-if="dialogState.confirm" class="modal-mask" @mousedown.self="cancel">
    <div class="modal" style="width: 420px">
      <div class="modal-head">
        <span v-if="dialogState.confirm.danger" style="color: var(--red); display: grid"><AlertTriangle :size="17" /></span>
        <span class="modal-title">{{ dialogState.confirm.title }}</span>
        <button class="icon-btn sm" @click="cancel"><X :size="14" /></button>
      </div>
      <div class="modal-body">
        <div style="line-height: 1.6; font-size: 12.5px">{{ dialogState.confirm.message }}</div>
        <div v-if="dialogState.confirm.detail" class="alert info mt12" style="white-space: pre-wrap; font-family: var(--mono); font-size: 11.5px">
          {{ dialogState.confirm.detail }}
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="cancel">{{ dialogState.confirm.cancelText || t('取消', 'Cancel') }}</button>
        <button class="btn" :class="dialogState.confirm.danger ? 'danger' : 'primary'" @click="accept">
          {{ dialogState.confirm.okText || t('确定', 'OK') }}
        </button>
      </div>
    </div>
  </div>
</template>
