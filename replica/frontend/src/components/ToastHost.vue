<script setup>
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-vue-next'
import { state } from '../lib/store'

const icons = { success: CheckCircle2, error: AlertCircle, info: Info }
</script>

<template>
  <div class="toasts">
    <TransitionGroup name="toast">
      <div v-for="item in state.toasts" :key="item.id" class="toast" :class="item.kind">
        <span class="t-icon"><component :is="icons[item.kind] || Info" :size="16" /></span>
        <div class="t-msg">{{ item.message }}</div>
        <button class="icon-btn sm" @click="state.toasts = state.toasts.filter((t) => t.id !== item.id)">
          <X :size="12" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: all .18s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateX(12px); }
</style>
