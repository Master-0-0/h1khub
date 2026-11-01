<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Eye, EyeOff, Loader2, Lock, ShieldCheck } from 'lucide-vue-next'
import { state, t, unlockWithPassword } from '../lib/store'

const password = ref('')
const show = ref(false)
const busy = ref(false)
const error = ref('')
const cooldown = ref(0)
const input = ref(null)
let timer = null

const lockedAtText = computed(() => {
  const raw = state.lock.lockedAt
  if (!raw) return t('刚刚', 'just now')
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return raw
  return d.toLocaleString()
})

const canSubmit = computed(() => !!password.value && !busy.value && cooldown.value <= 0)

function startCooldown(seconds) {
  const value = Math.max(0, Number(seconds) || 0)
  if (value <= 0) return
  cooldown.value = Math.ceil(value)
  if (timer) clearInterval(timer)
  timer = setInterval(() => {
    cooldown.value = Math.max(0, cooldown.value - 1)
    if (cooldown.value <= 0 && timer) {
      clearInterval(timer)
      timer = null
    }
  }, 1000)
}

async function submit() {
  if (!canSubmit.value) return
  busy.value = true
  error.value = ''
  try {
    await unlockWithPassword(password.value)
    password.value = ''
  } catch (err) {
    const info = err?.payload?.error || {}
    error.value = info.message || String(err?.message || err)
    if (typeof info.failedAttempts === 'number') state.lock.failedAttempts = info.failedAttempts
    state.lock.retryAfter = info.retryAfter || 0
    startCooldown(info.retryAfter || 0)
    if (input.value) {
      input.value.focus()
      input.value.select()
    }
  } finally {
    busy.value = false
  }
}

function trapFocus(event) {
  if (event.key !== 'Tab') return
  const focusables = [...event.currentTarget.querySelectorAll('input, button, [href], select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((el) => !el.disabled && el.offsetParent !== null)
  if (!focusables.length) return
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function resetPassword() {
  // The original offers "重置密码并退出登录" against the cloud account.  The
  // replica is local-only, so the only safe recovery is documented in
  // docs/reserved-slots.md: stop the backend and clear lock.passwordHash.
  error.value = t(
    '忘记口令：请停止本地服务后清除 data.db 中的 lock.passwordHash（详见 docs/reserved-slots.md#90-锁屏与托盘）。',
    'Forgot the password? Stop the local service and clear lock.passwordHash in data.db (see docs/reserved-slots.md#90).'
  )
}

onMounted(async () => {
  await nextTick()
  input.value?.focus()
  if (state.lock.retryAfter > 0) startCooldown(state.lock.retryAfter)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  timer = null
})
</script>

<template>
  <div class="lock-screen" role="dialog" aria-modal="true" aria-label="锁屏" data-testid="lock-screen" @keydown="trapFocus">
    <div class="lock-card">
      <div class="lock-logo"><Lock :size="28" /></div>
      <h2>{{ t('H1KHub 已锁定', 'H1KHub is locked') }}</h2>
      <p class="lock-subtitle">{{ t('请输入锁屏密码继续', 'Enter the screen-lock password to continue') }}</p>

      <form class="lock-form" @submit.prevent="submit">
        <div class="lock-input-wrap">
          <input
            ref="input"
            v-model="password"
            class="input lock-password"
            :type="show ? 'text' : 'password'"
            :placeholder="t('锁屏密码', 'Password')"
            autocomplete="current-password"
            spellcheck="false"
            autofocus
            :disabled="busy"
            data-testid="lock-password"
          />
          <button
            class="btn ghost sm lock-reveal"
            type="button"
            :title="show ? t('隐藏密码', 'Hide password') : t('显示密码', 'Show password')"
            @click="show = !show"
          >
            <EyeOff v-if="show" :size="14" />
            <Eye v-else :size="14" />
          </button>
        </div>
        <button class="btn primary lock-unlock" type="submit" :disabled="!canSubmit" data-testid="lock-unlock">
          <Loader2 v-if="busy" :size="14" class="tool-spin" />
          <ShieldCheck v-else :size="14" />
          {{ busy ? t('验证中…', 'Verifying…') : t('解锁', 'Unlock') }}
        </button>
      </form>

      <div v-if="error" class="lock-error" data-testid="lock-error">{{ error }}</div>
      <div v-if="cooldown > 0" class="lock-countdown">
        {{ t(`请等待 ${cooldown} 秒后重试`, `Try again in ${cooldown}s`) }}
      </div>

      <div class="lock-meta">
        <span>{{ t('锁定于', 'Locked at') }} {{ lockedAtText }}</span>
        <span>·</span>
        <span>{{ t('错误尝试', 'Failed attempts') }} {{ state.lock.failedAttempts || 0 }}</span>
      </div>

      <button class="btn ghost sm lock-forgot" type="button" @click="resetPassword">
        {{ t('忘记口令？', 'Forgot password?') }}
      </button>

      <div class="lock-foot">
        {{ t(
          '锁屏只覆盖当前浏览器界面；本地会话令牌与正在运行的终端/数据库会话不受影响。',
          'The lock only covers this browser UI; the local session token and running terminal/database sessions are untouched.'
        ) }}
      </div>
    </div>
  </div>
</template>
