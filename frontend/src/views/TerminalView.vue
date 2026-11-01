<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { SearchAddon } from '@xterm/addon-search'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { WebglAddon } from '@xterm/addon-webgl'
import Zmodem from 'zmodem.js/src/zmodem_browser'
import {
  ChevronLeft, ChevronRight, Clipboard, Copy, Download, FolderTree, Loader2, Megaphone, RefreshCw,
  Search, SquareTerminal, Upload, X, Eraser,
} from 'lucide-vue-next'
import { api, base64ToBytes, bytesToBase64, getToken, rpc } from '../lib/api'
import { assetMap, patchTab, state, t, toast } from '../lib/store'
import { promptDialog } from '../lib/ui'
import FilePane from '../components/FilePane.vue'
import BroadcastDialog from '../components/BroadcastDialog.vue'

const props = defineProps({
  tab: { type: Object, required: true },
})

const hostEl = ref(null)
const termEl = ref(null)
const searchEl = ref(null)
const terminal = ref(null)
const sessionId = ref(props.tab.sessionId || null)
const status = ref('connecting')
const errorMessage = ref('')
const searchOpen = ref(false)
const searchText = ref('')
const fitAddonRef = ref(null)
const searchAddonRef = ref(null)
const sftpOpen = ref(false)
const sftpWidth = ref(420)
const broadcastOpen = ref(false)
const splitDrag = ref(false)
const zmodemOpen = ref(false)
const zmodemFilesEl = ref(null)
const zmodemStatus = ref('')
let ro = null
let unregister = null
let channel = ''
let disposed = false
let connectSeq = 0
let zsentry = null
let zsession = null
let zmodemResolve = null

const asset = computed(() => (props.tab.assetId ? assetMap.value.get(props.tab.assetId) : null))
const typeName = computed(() => asset.value?.typeName || props.tab.params?.typeName || 'Shell')
const hasSftp = computed(() => ['SSH', 'FTP'].includes(typeName.value))
const target = computed(() => {
  const cfg = asset.value?.config || {}
  if (typeName.value === 'Shell') return `${cfg.shell || '$SHELL'}`
  if (typeName.value === 'Telnet') return `${cfg.host}:${cfg.port}`
  if (typeName.value === 'Serial') return `${cfg.port || cfg.device || ''} @ ${cfg.baudRate || 9600}`
  if (cfg.host) return `${cfg.username ? cfg.username + '@' : ''}${cfg.host}:${cfg.port || ''}`
  return props.tab.title
})

const xtermTheme = () => {
  const dark = (state.settings.appearance?.theme || 'dark') !== 'light'
  return dark
    ? {
        background: '#1a1b1d',
        foreground: '#d8dbe0',
        cursor: '#4c9aff',
        cursorAccent: '#1a1b1d',
        selectionBackground: 'rgba(76,154,255,0.28)',
        black: '#1a1b1d',
        red: '#e06c75',
        green: '#98c379',
        yellow: '#e5c07b',
        blue: '#61afef',
        magenta: '#c678dd',
        cyan: '#56b6c2',
        white: '#dcdfe4',
        brightBlack: '#5c6370',
        brightRed: '#ff7b86',
        brightGreen: '#b5e890',
        brightYellow: '#ffd68a',
        brightBlue: '#82c4ff',
        brightMagenta: '#dda0f0',
        brightCyan: '#7fd6e0',
        brightWhite: '#ffffff',
      }
    : {
        background: '#ffffff',
        foreground: '#24292f',
        cursor: '#236edc',
        cursorAccent: '#ffffff',
        selectionBackground: 'rgba(35,110,220,0.18)',
        black: '#24292f',
        red: '#cf222e',
        green: '#116329',
        yellow: '#9a6700',
        blue: '#0969da',
        magenta: '#8250df',
        cyan: '#1b7c83',
        white: '#6e7781',
        brightBlack: '#57606a',
        brightRed: '#a40e26',
        brightGreen: '#1a7f37',
        brightYellow: '#7d4e00',
        brightBlue: '#218bff',
        brightMagenta: '#a475f9',
        brightCyan: '#3192aa',
        brightWhite: '#8c959f',
      }
}

function createTerminal() {
  const settings = state.settings
  const term = new Terminal({
    fontFamily: settings.appearance?.terminalFontFamily || 'JetBrains Mono, Menlo, monospace',
    fontSize: settings.appearance?.terminalFontSize || 13,
    cursorBlink: settings.terminal?.cursorBlink !== false,
    cursorStyle: settings.terminal?.cursorStyle || 'block',
    scrollback: settings.terminal?.scrollback || 5000,
    allowProposedApi: true,
    theme: xtermTheme(),
    macOptionIsMeta: true,
    rightClickSelectsWord: (settings.terminal?.rightClick || 'menu') === 'select',
  })
  const fitAddon = new FitAddon()
  const searchAddon = new SearchAddon()
  term.loadAddon(fitAddon)
  term.loadAddon(searchAddon)
  term.loadAddon(new WebLinksAddon((event, uri) => window.open(uri, '_blank')))
  term.open(termEl.value)
  fitAddon.fit()
  // GPU (WebGL) renderer with automatic fallback to the DOM renderer.
  // The setting is user-controllable; context loss downgrades for the rest of
  // the session instead of breaking the terminal.
  let webglAddon = null
  const applyRenderer = () => {
    const want = state.settings.terminal?.webgl !== false
    if (want && !webglAddon) {
      try {
        webglAddon = new WebglAddon()
        webglAddon.onContextLoss(() => {
          try { webglAddon?.dispose() } catch {}
          webglAddon = null
          if (hostEl.value) hostEl.value.dataset.renderer = 'dom'
          toast(t('WebGL 上下文丢失，已回退到 DOM 渲染', 'WebGL context lost, fell back to DOM renderer'), 'info')
        })
        term.loadAddon(webglAddon)
        if (hostEl.value) hostEl.value.dataset.renderer = 'webgl'
      } catch (err) {
        webglAddon = null
        if (hostEl.value) hostEl.value.dataset.renderer = 'dom'
      }
    } else if (!want && webglAddon) {
      try { webglAddon.dispose() } catch {}
      webglAddon = null
      if (hostEl.value) hostEl.value.dataset.renderer = 'dom'
    } else if (!webglAddon && hostEl.value) {
      hostEl.value.dataset.renderer = 'dom'
    }
  }
  applyRenderer()
  terminal.value = term
  if (typeof window !== 'undefined') window.__h1khubTerm = term
  fitAddonRef.value = fitAddon
  searchAddonRef.value = searchAddon

  term.onData((data) => {
    if (status.value !== 'connected') return
    if (zsession) return // ZMODEM owns the link while a transfer is running
    rpc.send(channel, { type: 'input', data: bytesToBase64(new TextEncoder().encode(data)) }).catch(() => {})
  })
  term.onResize(({ cols, rows }) => {
    if (status.value === 'connected') rpc.send(channel, { type: 'resize', cols, rows }).catch(() => {})
  })
  term.attachCustomKeyEventHandler((event) => {
    if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'c') {
      const selection = term.getSelection()
      if (selection) {
        navigator.clipboard?.writeText(selection)
        return false
      }
    }
    if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'v') {
      navigator.clipboard?.readText().then((text) => text && term.paste(text)).catch(() => {})
      return false
    }
    return true
  })
  ro = new ResizeObserver(() => {
    const el = hostEl.value
    if (!el || el.clientWidth < 20 || el.clientHeight < 20) return
    try {
      fitAddon.fit()
    } catch {}
  })
  ro.observe(hostEl.value)
  setupZmodem(term)
}

function sendTerminalBytes(bytes) {
  if (status.value !== 'connected') return
  rpc.send(channel, { type: 'input', data: bytesToBase64(bytes) }).catch(() => {})
}

function chooseZmodemFiles() {
  return new Promise((resolve) => {
    zmodemResolve = resolve
    zmodemStatus.value = ''
    zmodemOpen.value = true
  })
}

function finishZmodemChoice(files) {
  const resolve = zmodemResolve
  zmodemResolve = null
  zmodemOpen.value = false
  if (resolve) resolve(files || [])
}

function confirmZmodemSend() {
  const el = zmodemFilesEl.value
  finishZmodemChoice(el && el.files ? Array.from(el.files) : [])
}

function cancelZmodemSend() {
  finishZmodemChoice([])
}

// ZMODEM (rz / sz) inside the terminal: the sentry watches server output,
// hands non-ZMODEM bytes to xterm, and takes over the link during a transfer.
function setupZmodem(term) {
  if (typeof Zmodem?.Sentry !== 'function') return
  try {
    zsentry = new Zmodem.Sentry({
      to_terminal: (octets) => term.write(new Uint8Array(octets)),
      sender: (octets) => sendTerminalBytes(new Uint8Array(octets)),
      on_detect: async (detection) => {
        let session
        try {
          session = detection.confirm()
        } catch (err) {
          if (typeof window !== 'undefined') window.__h1khubZmodem = { error: String(err) }
          return
        }
        if (typeof window !== 'undefined') window.__h1khubZmodem = { type: session.type, at: Date.now() }
        zsession = session
        session.on('session_end', () => { zsession = null })
        if (session.type === 'send') {
          // The remote ran `rz`: this browser is the sender.
          zmodemStatus.value = t('对端执行了 rz，请选择要发送的文件', 'Remote ran rz; choose files to send')
          const files = await chooseZmodemFiles()
          if (!files.length) {
            try { session.abort() } catch {}
            return
          }
          Zmodem.Browser.send_files(session, files, {
            on_file_complete: (file) => toast(t('已发送 ', 'Sent ') + file.name, 'success'),
          })
            .then(() => { zmodemStatus.value = t('ZMODEM 发送完成', 'ZMODEM send complete') })
            .catch(() => {})
        } else {
          // The remote ran `sz`: this browser receives and saves each offer.
          session.on('offer', (xfer) => {
            if (typeof window !== 'undefined' && window.__h1khubZmodem) window.__h1khubZmodem.offers = (window.__h1khubZmodem.offers || 0) + 1
            const name = xfer.get_details().name
            xfer.accept().then((payloads) => {
              Zmodem.Browser.save_to_disk(payloads, name)
              if (hostEl.value) hostEl.value.dataset.lastDownload = name
              zmodemStatus.value = t('已接收 ', 'Received ') + name
              toast(t('ZMODEM 已接收 ', 'ZMODEM received ') + name, 'success')
            }).catch((err) => {
              if (typeof window !== 'undefined' && window.__h1khubZmodem) window.__h1khubZmodem.acceptError = String(err)
            })
          })
          try {
            session.start()
          } catch (err) {
            if (typeof window !== 'undefined' && window.__h1khubZmodem) window.__h1khubZmodem.startError = String(err)
          }
        }
      },
      on_retract: () => { zsession = null },
    })
  } catch (err) {
    zsentry = null
  }
}

async function startSession() {
  const seq = ++connectSeq
  status.value = 'connecting'
  errorMessage.value = ''
  try {
    let sid = sessionId.value
    if (!sid) {
      const res = await api.post('/api/sessions/start', {
        assetId: props.tab.assetId || null,
        kind: props.tab.params?.kind,
        container: props.tab.params?.container,
        title: props.tab.title,
        cols: terminal.value?.cols || 120,
        rows: terminal.value?.rows || 32,
      })
      sid = res.session.id
      if (seq !== connectSeq) {
        api.post(`/api/sessions/${sid}/close`).catch(() => {})
        return
      }
      sessionId.value = sid
      patchTab(props.tab.id, { sessionId: sid })
    }
    channel = `term:${sid}`
    const queue = []
    unregister?.()
    unregister = rpc.on(channel, (event) => {
      if (event.type === 'history' || event.type === 'data') {
        const bytes = base64ToBytes(event.data)
        if (zsentry) zsentry.consume(bytes)
        else terminal.value?.write(bytes)
      } else if (event.type === 'exit') {
        status.value = 'closed'
        terminal.value?.write(`\r\n\x1b[90m[${t('会话已结束', 'session closed')}${event.code !== null && event.code !== undefined ? ' · code=' + event.code : ''}]\x1b[0m\r\n`)
      } else if (event.type === 'error') {
        toast(event.message, 'error')
      }
    })
    await rpc.open(channel)
    status.value = 'connected'
    await nextTick()
    fitAddonRef.value?.fit()
    if (terminal.value) {
      await rpc.send(channel, { type: 'resize', cols: terminal.value.cols, rows: terminal.value.rows })
      terminal.value.focus()
    }
    const pending = props.tab.params?.pendingCommand
    if (pending) {
      setTimeout(() => {
        rpc.send(channel, { type: 'input', data: bytesToBase64(new TextEncoder().encode(pending + '\n')) }).catch(() => {})
        props.tab.params.pendingCommand = ''
      }, 700)
    }
  } catch (err) {
    status.value = 'error'
    errorMessage.value = String(err.message || err)
    terminal.value?.write(`\r\n\x1b[31m${errorMessage.value}\x1b[0m\r\n`)
  }
}

async function reconnect() {
  if (sessionId.value) {
    await api.post(`/api/sessions/${sessionId.value}/close`).catch(() => {})
    sessionId.value = null
    patchTab(props.tab.id, { sessionId: null })
  }
  terminal.value?.reset()
  await startSession()
}

// --- trzsz: browser file -> backend client -> remote trz/tsz -----------------
const trzszInput = ref(null)
const trzszBusy = ref(false)

function trzszPickUpload() {
  if (!sessionId.value) return toast(t('会话尚未连接', 'Session is not connected'), 'error')
  trzszInput.value?.click()
}

async function trzszUploadFile(event) {
  const file = event.target?.files?.[0]
  if (event.target) event.target.value = ''
  if (!file) return
  const dest = await promptDialog({
    title: t('trzsz 上传', 'trzsz upload'),
    message: t('远端目标目录（需已存在）', 'Remote destination directory (must exist)'),
    value: '.',
    okText: t('上传', 'Upload'),
  })
  if (dest === null) return
  trzszBusy.value = true
  try {
    const token = await getToken()
    const params = new URLSearchParams({ sessionId: sessionId.value, dest, name: file.name })
    const res = await fetch(`/api/terminal/trzsz/upload?${params}`, {
      method: 'POST',
      headers: { 'X-H1KHub-Token': token, 'Content-Type': 'application/octet-stream' },
      body: file,
    })
    const payload = await res.json().catch(() => ({}))
    if (!res.ok || payload.error) throw new Error(payload.error || `HTTP ${res.status}`)
    toast(t(`trzsz 已上传 ${file.name}（${payload.data?.size ?? file.size} 字节）`, `trzsz uploaded ${file.name}`), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    trzszBusy.value = false
  }
}

async function trzszDownload() {
  if (!sessionId.value) return toast(t('会话尚未连接', 'Session is not connected'), 'error')
  const path = await promptDialog({
    title: t('trzsz 下载', 'trzsz download'),
    message: t('远端文件路径', 'Remote file path'),
    value: '',
    placeholder: '/tmp/report.pdf',
    okText: t('下载', 'Download'),
  })
  if (!path) return
  trzszBusy.value = true
  try {
    const token = await getToken()
    const res = await fetch('/api/terminal/trzsz/download', {
      method: 'POST',
      headers: { 'X-H1KHub-Token': token, 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: sessionId.value, path }),
    })
    if (!res.ok) {
      const payload = await res.json().catch(() => ({}))
      throw new Error(payload.error || `HTTP ${res.status}`)
    }
    const blob = await res.blob()
    const name = decodeURIComponent(res.headers.get('X-Trzsz-Filename') || '') || path.split('/').pop()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 4000)
    toast(t(`trzsz 已下载 ${name}`, `trzsz downloaded ${name}`), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  } finally {
    trzszBusy.value = false
  }
}

function clearTerminal() {
  terminal.value?.clear()
}

function copySelection() {
  const selection = terminal.value?.getSelection()
  if (selection) {
    navigator.clipboard?.writeText(selection)
    toast(t('已复制选中内容', 'Selection copied'), 'success')
  } else {
    toast(t('没有选中内容', 'Nothing selected'), 'info')
  }
}

async function pasteClipboard() {
  try {
    const text = await navigator.clipboard.readText()
    if (text) terminal.value?.paste(text)
  } catch (err) {
    toast(t('无法读取剪贴板', 'Clipboard unavailable'), 'error')
  }
}

function toggleSearch() {
  searchOpen.value = !searchOpen.value
  if (searchOpen.value) {
    nextTick(() => searchEl.value?.focus())
  } else {
    searchAddonRef.value?.clearDecorations?.()
  }
}

function findNext(backward = false) {
  if (!searchText.value) return
  if (backward) searchAddonRef.value?.findPrevious(searchText.value)
  else searchAddonRef.value?.findNext(searchText.value)
}

function onSplitDown(event) {
  splitDrag.value = true
  const startX = event.clientX
  const startW = sftpWidth.value
  const onMove = (e) => {
    const next = Math.min(Math.max(startW - (e.clientX - startX), 260), window.innerWidth * 0.6)
    sftpWidth.value = next
  }
  const onUp = () => {
    splitDrag.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    fitAddonRef.value?.fit()
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

watch(
  () => props.tab.params?.pendingToken,
  () => {
    const pending = props.tab.params?.pendingCommand
    if (pending && channel) {
      rpc.send(channel, { type: 'input', data: bytesToBase64(new TextEncoder().encode(pending + '\n')) }).catch(() => {})
      props.tab.params.pendingCommand = ''
    }
  },
)

onMounted(async () => {
  await nextTick()
  createTerminal()
  sftpOpen.value = hasSftp.value
  await startSession()
})

onBeforeUnmount(() => {
  disposed = true
  connectSeq++
  if (channel) rpc.close(channel)
  unregister?.()
  ro?.disconnect()
  zsession = null
  zsentry = null
  terminal.value?.dispose()
  terminal.value = null
})
</script>

<template>
  <div class="view" style="flex-direction: column">
    <div class="term-toolbar">
      <div class="connection-pill">
        <span class="dot" :class="{ off: status === 'closed' || status === 'error', busy: status === 'connecting' }" />
        <SquareTerminal :size="12" />
        <span class="ellipsis" style="max-width: 280px">{{ target }}</span>
      </div>
      <span class="term-status">
        <template v-if="status === 'connecting'"><Loader2 :size="12" class="tool-spin" /> {{ t('连接中…', 'Connecting…') }}</template>
        <template v-else-if="status === 'connected'">{{ t('已连接', 'Connected') }}</template>
        <template v-else-if="status === 'closed'">{{ t('已断开', 'Disconnected') }}</template>
        <template v-else>{{ t('连接失败', 'Connection failed') }}</template>
      </span>
      <span class="grow" style="flex: 1" />
      <button class="icon-btn sm" :title="t('复制选中', 'Copy selection')" @click="copySelection"><Copy :size="14" /></button>
      <button class="icon-btn sm" :title="t('粘贴', 'Paste')" @click="pasteClipboard"><Clipboard :size="14" /></button>
      <button class="icon-btn sm" :title="t('清屏', 'Clear')" @click="clearTerminal"><Eraser :size="14" /></button>
      <button class="icon-btn sm" :title="t('保存日志', 'Save log')" @click="saveLog"><Download :size="14" /></button>
      <button class="icon-btn sm" :class="{ active: searchOpen }" :title="t('搜索', 'Search')" @click="toggleSearch"><Search :size="14" /></button>
      <button class="icon-btn sm" :title="t('重新连接', 'Reconnect')" @click="reconnect"><RefreshCw :size="14" /></button>
      <button class="icon-btn sm" :title="t('命令广播', 'Command broadcast')" @click="broadcastOpen = true"><Megaphone :size="14" /></button>
      <button
        v-if="typeName === 'SSH'"
        class="icon-btn sm"
        :disabled="trzszBusy || status !== 'connected'"
        :title="t('trzsz 上传文件', 'trzsz upload')"
        @click="trzszPickUpload"
      >
        <Loader2 v-if="trzszBusy" :size="14" class="tool-spin" />
        <Upload v-else :size="14" />
      </button>
      <button
        v-if="typeName === 'SSH'"
        class="icon-btn sm"
        :disabled="trzszBusy || status !== 'connected'"
        :title="t('trzsz 下载文件', 'trzsz download')"
        @click="trzszDownload"
      >
        <Download :size="14" />
      </button>
      <input ref="trzszInput" type="file" style="display: none" @change="trzszUploadFile" />
      <button
        v-if="hasSftp"
        class="icon-btn sm"
        :class="{ active: sftpOpen }"
        :title="t('SFTP 文件面板', 'SFTP panel')"
        @click="sftpOpen = !sftpOpen; nextTick(() => fitAddonRef?.fit())"
      >
        <FolderTree :size="14" />
      </button>
    </div>

    <div v-if="searchOpen" style="display: flex; align-items: center; gap: 4px; padding: 5px 8px; border-bottom: 1px solid var(--border); background: var(--bg-panel)">
      <div class="searchbox" style="flex: 1; max-width: 320px">
        <span class="icon"><Search :size="13" /></span>
        <input ref="searchEl" v-model="searchText" class="input" :placeholder="t('在终端中查找', 'Find in terminal')" @keyup.enter="findNext(false)" @keyup.esc="searchOpen = false" />
      </div>
      <button class="icon-btn sm" @click="findNext(true)"><ChevronLeft :size="14" /></button>
      <button class="icon-btn sm" @click="findNext(false)"><ChevronRight :size="14" /></button>
      <button class="icon-btn sm" @click="searchOpen = false"><X :size="14" /></button>
    </div>

    <div class="split" style="flex: 1">
      <div ref="hostEl" class="terminal-host" @click="terminal?.focus()" @contextmenu="onTerminalContextMenu">
        <div ref="termEl" style="height: 100%" />
      </div>
      <template v-if="sftpOpen && sessionId && hasSftp">
        <div class="split-handle" :class="{ dragging: splitDrag }" @mousedown.prevent="onSplitDown" />
        <div class="split-pane" :style="{ width: sftpWidth + 'px', flex: 'none', borderLeft: '1px solid var(--border)' }">
          <FilePane :session-id="sessionId" :asset-id="props.tab.assetId || ''" :title="t('远程文件', 'Remote files')" />
        </div>
      </template>
    </div>

    <BroadcastDialog v-if="broadcastOpen" @close="broadcastOpen = false" />

    <div v-if="zmodemOpen" class="modal-mask">
      <div class="modal" style="width: 460px">
        <div class="modal-head">
          <span class="modal-title">{{ t('ZMODEM · 发送文件', 'ZMODEM · send files') }}</span>
          <button class="icon-btn sm" @click="cancelZmodemSend"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <p class="tiny" style="margin-bottom: 8px">{{ zmodemStatus || t('选择要发送到远端的文件', 'Choose files to send to the remote host') }}</p>
          <input ref="zmodemFilesEl" type="file" multiple />
        </div>
        <div class="modal-foot">
          <button class="btn" @click="cancelZmodemSend">{{ t('取消', 'Cancel') }}</button>
          <button class="btn primary" @click="confirmZmodemSend">{{ t('发送', 'Send') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
