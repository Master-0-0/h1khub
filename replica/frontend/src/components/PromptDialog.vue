<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'
import { dialogState } from '../lib/ui'
import { t } from '../lib/store'

const value = ref('')
const inputEl = ref(null)

watch(
  () => dialogState.prompt,
  async (prompt) => {
    if (!prompt) return
    value.value = prompt.value || ''
    await nextTick()
    inputEl.value?.focus()
    inputEl.value?.select?.()
  },
  { immediate: true },
)

function cancel() {
  dialogState.prompt?.resolve(null)
}
function accept() {
  const text = String(value.value || '').trim()
  if (!text) return
  dialogState.prompt?.resolve(text)
}
function onKey(event) {
  if (!dialogState.prompt) return
  if (event.key === 'Escape') cancel()
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    accept()
  }
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div v-if="dialogState.prompt" class="modal-mask" @mousedown.self="cancel">
    <div class="modal" style="width: 440px">
      <div class="modal-head">
        <span class="modal-title">{{ dialogState.prompt.title }}</span>
        <button class="icon-btn sm" @click="cancel"><X :size="14" /></button>
      </div>
      <div class="modal-body">
        <div class="field">
          <label>{{ dialogState.prompt.message }}</label>
          <textarea
            v-if="dialogState.prompt.multiline"
            ref="inputEl"
            v-model="value"
            class="textarea"
            rows="6"
            :placeholder="dialogState.prompt.placeholder"
          />
          <input
            v-else
            ref="inputEl"
            v-model="value"
            class="input"
            :placeholder="dialogState.prompt.placeholder"
          />
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="cancel">{{ t('取消', 'Cancel') }}</button>
        <button class="btn primary" @click="accept">{{ dialogState.prompt.okText || t('确定', 'OK') }}</button>
      </div>
    </div>
  </div>
</template>
