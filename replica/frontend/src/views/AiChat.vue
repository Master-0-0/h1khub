<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  AlertTriangle, ArrowUp, AtSign, Check, ChevronDown, ChevronRight, Copy, Loader2, MessageSquarePlus,
  Plus, RefreshCw, Send, Settings2, Sparkles, Square, Trash2, Undo2, Wrench, X,
} from 'lucide-vue-next'
import { api, rpc } from '../lib/api'
import { state, t, toast } from '../lib/store'
import { renderMarkdown, timeAgo } from '../lib/format'
import { confirmDialog, showContextMenu } from '../lib/ui'

const props = defineProps({ tab: { type: Object, required: true } })

const chats = computed(() => state.chats)
const messages = ref([])
const input = ref('')
const sending = ref(false)
const activeChatId = ref(props.tab.params?.chatId || null)
const messagesEl = ref(null)
const composerEl = ref(null)
const referenced = ref([])
const approvalsRemember = ref({})
const expandedTools = ref({})
const channel = 'ai'
let channelOpened = false
let unregister = null
let currentAssistant = null

const providers = computed(() => state.settings.ai?.providers || [])
const assistantMode = ref('general')
const sessions = ref([])
const contextSessionId = ref('')
const sqlAssetId = ref('')
const sqlDatabase = ref('')
const sqlText = ref('')
const dbAssets = computed(() => state.assets.filter((a) => a.typeName === 'DB'))
const providerId = ref(state.settings.ai?.activeProviderId || '')
const provider = computed(() => providers.value.find((p) => p.id === providerId.value) || null)
const model = ref(state.settings.ai?.activeModel || '')
const models = computed(() => provider.value?.models || [])
const configured = computed(() => providers.value.length > 0 && models.value.length > 0)

watch(
  () => state.settings.ai,
  (ai) => {
    if (!ai) return
    if (!providers.value.find((p) => p.id === providerId.value)) providerId.value = ai.activeProviderId || providers.value[0]?.id || ''
    if (!model.value && ai.activeModel) model.value = ai.activeModel
  },
  { deep: true },
)

watch(provider, (p) => {
  if (!p) return
  if (!p.models?.includes(model.value)) model.value = p.defaultModel || p.models?.[0] || ''
})

const currentProviderLabel = computed(() => (provider.value ? `${provider.value.name} · ${model.value || t('未选模型', 'no model')}` : t('未配置模型', 'No provider')))

function scrollToBottom() {
  nextTick(() => {
    const el = messagesEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function mapStored(messagesIn) {
  return (messagesIn || []).map((m, index) => ({
    id: `m_${m.id || index}`,
    role: m.role,
    content: m.content || '',
    reasoning: m.meta?.reasoning || '',
    toolCalls: [],
    approvals: [],
    usage: m.meta?.usage || null,
    streaming: false,
  }))
}

async function loadChat(chat) {
  if (sending.value) {
    toast(t('请先停止当前回复', 'Stop the current reply first'), 'info')
    return
  }
  activeChatId.value = chat.id
  try {
    const res = await api.get(`/api/chats/${chat.id}/messages`)
    messages.value = mapStored(res.messages)
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
  scrollToBottom()
}

function newChat() {
  if (sending.value) {
    toast(t('请先停止当前回复', 'Stop the current reply first'), 'info')
    return
  }
  activeChatId.value = null
  messages.value = []
  input.value = ''
  referenced.value = []
  nextTick(() => composerEl.value?.focus())
}

async function deleteChat(chat, event) {
  event?.stopPropagation()
  const ok = await confirmDialog({
    title: t('删除对话', 'Delete chat'),
    message: t(`确定删除「${chat.title}」吗？`, `Delete "${chat.title}"?`),
    danger: true,
    okText: t('删除', 'Delete'),
  })
  if (!ok) return
  try {
    await api.del(`/api/chats/${chat.id}`)
    state.chats = state.chats.filter((c) => c.id !== chat.id)
    if (activeChatId.value === chat.id) newChat()
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function ensureChannel() {
  if (!unregister) {
    unregister = rpc.on(channel, handleEvent)
  }
  await rpc.open(channel)
  channelOpened = true
}

function handleEvent(event) {
  if (event.type === 'chat') {
    if (event.chatId) activeChatId.value = event.chatId
    return
  }
  if (event.type === 'start') {
    return
  }
  if (event.type === 'delta') {
    if (!currentAssistant) return
    currentAssistant.content += event.text || ''
    scrollToBottom()
    return
  }
  if (event.type === 'reasoning') {
    if (!currentAssistant) return
    currentAssistant.reasoning += event.text || ''
    scrollToBottom()
    return
  }
  if (event.type === 'tool_start') {
    if (!currentAssistant) return
    currentAssistant.toolCalls.push({
      id: event.id,
      name: event.name,
      arguments: event.arguments || {},
      running: true,
      output: '',
      ok: null,
    })
    scrollToBottom()
    return
  }
  if (event.type === 'tool_result') {
    if (!currentAssistant) return
    const call = currentAssistant.toolCalls.find((c) => c.id === event.id)
    if (call) {
      call.running = false
      call.ok = event.ok
      call.output = event.output || ''
      if (call.name === 'write_file' && event.ok) {
        try {
          const parsed = JSON.parse(call.output)
          if (parsed.backupId) call.backupId = parsed.backupId
        } catch {}
      }
    }
    scrollToBottom()
    return
  }
  if (event.type === 'approval') {
    if (!currentAssistant) return
    currentAssistant.approvals.push({ ...event, status: 'pending' })
    scrollToBottom()
    return
  }
  if (event.type === 'usage') {
    if (currentAssistant) currentAssistant.usage = event.usage || null
    return
  }
  if (event.type === 'done') {
    if (currentAssistant) {
      currentAssistant.streaming = false
      if (event.content) currentAssistant.content = event.content
    }
    sending.value = false
    currentAssistant = null
    refreshChats()
    scrollToBottom()
    return
  }
  if (event.type === 'error') {
    if (currentAssistant) {
      currentAssistant.streaming = false
      currentAssistant.error = event.message
    }
    sending.value = false
    toast(event.message || t('对话失败', 'Chat failed'), 'error', 6000)
    return
  }
}

async function refreshChats() {
  try {
    const res = await api.get('/api/chats')
    state.chats = res.chats || []
  } catch {}
}

async function loadSessions() {
  try {
    const res = await api.get('/api/sessions')
    sessions.value = (res.sessions || []).filter((x) => !x.closed)
    if (!contextSessionId.value) contextSessionId.value = sessions.value[0]?.id || ''
  } catch {}
}

function setAssistant(mode) {
  assistantMode.value = mode
  if (mode === 'terminal') loadSessions()
}

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return
  if (!configured.value) {
    toast(t('请先在设置中配置模型供应商与 API Key', 'Configure a provider and API key in settings first'), 'error')
    state.settingsOpen = true
    return
  }
  if (!provider.value || !model.value) {
    toast(t('请选择模型', 'Choose a model'), 'error')
    return
  }
  if (!activeChatId.value) {
    try {
      const res = await api.post('/api/chats', { title: text.slice(0, 40) || '新对话' })
      activeChatId.value = res.chat.id
      state.chats = [res.chat, ...state.chats]
    } catch (err) {
      toast(String(err.message || err), 'error')
      return
    }
  }
  const assetIds = referenced.value.map((a) => a.id)
  messages.value.push({ id: `u_${Date.now()}`, role: 'user', content: text })
  input.value = ''
  referenced.value = []
  const assistant = {
    id: `a_${Date.now()}`,
    role: 'assistant',
    content: '',
    reasoning: '',
    toolCalls: [],
    approvals: [],
    streaming: true,
    usage: null,
    error: '',
  }
  messages.value.push(assistant)
  // keep the reactive proxy (not the raw object) so streaming mutations re-render
  currentAssistant = messages.value[messages.value.length - 1]
  sending.value = true
  scrollToBottom()
  try {
    await ensureChannel()
    await rpc.send(channel, {
      action: 'chat',
      chatId: activeChatId.value,
      prompt: text,
      providerId: providerId.value,
      model: model.value,
      assetIds,
      assistant: assistantMode.value,
      sessionId: assistantMode.value === 'terminal' ? contextSessionId.value : undefined,
      assetId: assistantMode.value === 'sql' ? sqlAssetId.value : undefined,
      database: assistantMode.value === 'sql' ? sqlDatabase.value : undefined,
      sql: assistantMode.value === 'sql' ? sqlText.value : undefined,
    })
  } catch (err) {
    assistant.streaming = false
    assistant.error = String(err.message || err)
    sending.value = false
    currentAssistant = null
  }
}

async function undoWrite(call) {
  if (!call.backupId) return
  try {
    await api.post('/api/agent/rollback', { id: call.backupId })
    call.undone = true
    toast(t('已恢复到写入前版本', 'Restored previous version'), 'success')
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

async function stop() {
  try {
    await rpc.send(channel, { action: 'cancel' })
  } catch {}
  sending.value = false
  if (currentAssistant) currentAssistant.streaming = false
}

async function decide(approval, approve) {
  approval.status = approve ? 'approved' : 'denied'
  const remember = !!approvalsRemember.value[approval.id]
  try {
    await rpc.send(channel, { action: 'approval', id: approval.id, approve, remember })
  } catch (err) {
    toast(String(err.message || err), 'error')
  }
}

function onKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    send()
  }
}

function autoGrow(event) {
  const el = event.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 180) + 'px'
}

function copyMessage(message) {
  navigator.clipboard?.writeText(message.content || '')
  toast(t('已复制', 'Copied'), 'success')
}

function mentionMenu(event) {
  const items = state.assets.length
    ? state.assets.slice(0, 40).map((a) => ({
        label: a.name,
        shortcut: a.typeName,
        action: () => {
          if (!referenced.value.find((x) => x.id === a.id)) referenced.value.push(a)
        },
      }))
    : [{ label: t('暂无资产', 'No assets'), disabled: true }]
  showContextMenu(event, items)
}

function removeRef(id) {
  referenced.value = referenced.value.filter((a) => a.id !== id)
}

function toggleTool(id) {
  expandedTools.value[id] = !expandedTools.value[id]
}

function prettyArgs(args) {
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

const EXAMPLES = computed(() => [
  { title: t('巡检一台服务器', 'Inspect a server'), prompt: t('列出所有资产，然后连接 SSH 资产检查磁盘、内存和负载，给出摘要结论。', 'List all assets, connect to the SSH asset, check disk, memory and load, then summarize.') },
  { title: t('分析慢查询', 'Analyze slow queries'), prompt: t('查看数据库资产中的表结构，执行 EXPLAIN 分析这条 SQL，并给出优化建议：SELECT * FROM users WHERE email LIKE \'%example.com\';', 'Inspect the database schema, EXPLAIN a query and suggest optimizations.') },
  { title: t('容器排障', 'Debug a container'), prompt: t('列出本机 Docker 容器，读取最近退出容器的日志，分析失败原因。', 'List local containers, read logs of the most recently exited one and explain the failure.') },
  { title: t('终端协助', 'Terminal help'), prompt: t('读取当前打开的终端内容，解释上一条命令的输出，并给出下一步操作。', 'Read the current terminal content, explain the last output and suggest next steps.') },
])

function useExample(example) {
  input.value = example.prompt
  nextTick(() => composerEl.value?.focus())
}

onMounted(async () => {
  if (props.tab.params?.chatId) {
    const chat = state.chats.find((c) => c.id === props.tab.params.chatId)
    if (chat) await loadChat(chat)
  }
  scrollToBottom()
})

onBeforeUnmount(() => {
  unregister?.()
  unregister = null
})
</script>

<template>
  <div class="view">
    <div class="ai-layout">
      <aside class="ai-side">
        <div class="ai-side-head">
          <button class="btn sm" style="flex: 1; justify-content: center" @click="newChat">
            <Plus :size="13" /> {{ t('新对话', 'New chat') }}
          </button>
        </div>
        <div class="ai-chat-list">
          <div
            v-for="chat in chats"
            :key="chat.id"
            class="ai-chat-item"
            :class="{ active: activeChatId === chat.id }"
            @click="loadChat(chat)"
          >
            <MessageSquarePlus :size="13" style="flex-shrink: 0; opacity: .7" />
            <div style="flex: 1; min-width: 0">
              <div class="ellipsis">{{ chat.title }}</div>
              <div class="tiny">{{ timeAgo(chat.updatedAt) }}</div>
            </div>
            <button class="icon-btn sm" style="opacity: .5" @click="deleteChat(chat, $event)"><Trash2 :size="11" /></button>
          </div>
          <div v-if="!chats.length" class="center-hint" style="padding: 20px; font-size: 11.5px; text-align: center">
            {{ t('暂无会话', 'No conversations') }}
          </div>
        </div>
        <div style="padding: 8px 10px; border-top: 1px solid var(--border)">
          <button class="btn ghost sm block" style="justify-content: flex-start" @click="state.settingsOpen = true">
            <Settings2 :size="13" /> {{ t('模型设置', 'Model settings') }}
          </button>
        </div>
      </aside>

      <div class="ai-main">
        <div class="toolbar" style="height: 38px">
          <Sparkles :size="15" style="color: var(--accent)" />
          <span style="font-weight: 600; font-size: 12.5px">H1KHub Agent</span>
          <div class="seg" style="margin-left: 6px">
            <button :class="{ active: assistantMode === 'general' }" @click="setAssistant('general')">{{ t('通用', 'General') }}</button>
            <button :class="{ active: assistantMode === 'terminal' }" @click="setAssistant('terminal')">{{ t('终端', 'Terminal') }}</button>
            <button :class="{ active: assistantMode === 'sql' }" @click="setAssistant('sql')">{{ t('SQL', 'SQL') }}</button>
          </div>
          <span class="badge">{{ t('工具审批已开启', 'Tool approvals on') }}</span>
          <span class="grow" style="flex: 1" />
          <select v-if="providers.length" v-model="providerId" class="select" style="height: 26px; width: 150px; font-size: 11.5px">
            <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
          <select v-if="models.length" v-model="model" class="select" style="height: 26px; width: 180px; font-size: 11.5px">
            <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
          </select>
          <span v-else class="tiny">{{ currentProviderLabel }}</span>
        </div>

        <div v-if="assistantMode === 'terminal'" class="toolbar" style="height: 32px; border-top: 0; background: var(--bg-panel)">
          <span class="tiny">{{ t('终端上下文', 'Terminal context') }}</span>
          <select v-model="contextSessionId" class="select" style="height: 24px; width: 220px; font-size: 11.5px">
            <option v-for="s in sessions" :key="s.id" :value="s.id">{{ s.title }} · {{ s.kind }}</option>
          </select>
          <button class="icon-btn sm" :title="t('刷新会话', 'Refresh sessions')" @click="loadSessions"><RefreshCw :size="13" /></button>
          <span class="grow" style="flex: 1" />
          <span class="tiny">{{ t('助手会读取所选终端最近输出并优先使用终端工具', 'The assistant reads recent output and prefers terminal tools') }}</span>
        </div>
        <div v-if="assistantMode === 'sql'" class="toolbar" style="height: 32px; border-top: 0; background: var(--bg-panel); gap: 6px">
          <span class="tiny">{{ t('数据库上下文', 'DB context') }}</span>
          <select v-model="sqlAssetId" class="select" style="height: 24px; width: 180px; font-size: 11.5px">
            <option value="">{{ t('选择数据库资产', 'Choose DB asset') }}</option>
            <option v-for="a in dbAssets" :key="a.id" :value="a.id">{{ a.name }}</option>
          </select>
          <input v-model="sqlDatabase" class="input mono" style="height: 24px; width: 110px; font-size: 11.5px" :placeholder="t('数据库', 'database')" />
          <input v-model="sqlText" class="input mono" style="height: 24px; flex: 1; font-size: 11.5px" :placeholder="t('可选：把当前 SQL 发给助手', 'Optional: current SQL')" />
        </div>

        <div v-if="!configured" class="alert warn" style="margin: 10px">
          <div class="row" style="gap: 8px">
            <AlertTriangle :size="15" />
            <div style="flex: 1">
              <b>{{ t('尚未配置模型', 'No model configured') }}</b> ·
              {{ t('请在设置中填写 OpenAI 兼容或 Anthropic 的 Base URL、API Key 与模型。', 'Add an OpenAI-compatible or Anthropic endpoint, key and model in settings.') }}
            </div>
            <button class="btn sm primary" @click="state.settingsOpen = true">{{ t('去配置', 'Configure') }}</button>
          </div>
        </div>

        <div ref="messagesEl" class="ai-messages">
          <div v-if="!messages.length" class="ai-empty">
            <div class="home-logo" style="width: 54px; height: 54px; border-radius: 16px">
              <Sparkles :size="26" />
            </div>
            <div style="text-align: center">
              <h2>{{ t('向 H1KHub 提问', 'Ask H1KHub anything') }}</h2>
              <p>{{ t('AI 可以直接操作你的 SSH、数据库、Docker 与文件，写操作会先请求审批。', 'The agent can operate your SSH, databases, Docker and files; writes require approval.') }}</p>
            </div>
            <div class="ai-hints">
              <div v-for="example in EXAMPLES" :key="example.title" class="ai-hint" @click="useExample(example)">
                <div class="h-title">{{ example.title }}</div>
                <div class="h-sub">{{ example.prompt.slice(0, 56) }}…</div>
              </div>
            </div>
          </div>

          <div v-else class="ai-inner">
            <div v-for="message in messages" :key="message.id" class="msg" :class="message.role">
              <div class="msg-avatar">
                <Sparkles v-if="message.role === 'assistant'" :size="14" />
                <span v-else>ME</span>
              </div>
              <div class="msg-body">
                <div class="msg-name">{{ message.role === 'assistant' ? 'H1KHub Agent' : t('你', 'You') }}</div>

                <details v-if="message.reasoning" class="tool-card">
                  <summary style="cursor: pointer; padding: 7px 10px; color: var(--text-3); font-size: 11.5px">
                    {{ t('思考过程', 'Reasoning') }}
                  </summary>
                  <div class="tool-body">{{ message.reasoning }}</div>
                </details>

                <div v-if="message.content" class="md" v-html="renderMarkdown(message.content)" />
                <div v-if="message.streaming && !message.content" class="row" style="gap: 6px; color: var(--text-3); font-size: 12px">
                  <Loader2 :size="13" class="tool-spin" /> {{ t('思考中…', 'Thinking…') }}
                </div>

                <div v-for="call in message.toolCalls" :key="call.id" class="tool-card">
                  <div class="tool-head" @click="toggleTool(call.id)">
                    <Wrench :size="13" :style="{ color: call.running ? 'var(--accent)' : call.ok ? 'var(--green)' : 'var(--red)' }" />
                    <span class="tname">{{ call.name }}</span>
                    <span class="tiny">{{ Object.keys(call.arguments || {}).join(', ') }}</span>
                    <span class="grow" style="flex: 1" />
                    <Loader2 v-if="call.running" :size="12" class="tool-spin" />
                    <Check v-else-if="call.ok" :size="12" style="color: var(--green)" />
                    <X v-else-if="call.ok === false" :size="12" style="color: var(--red)" />
                    <ChevronDown v-if="expandedTools[call.id]" :size="12" />
                    <ChevronRight v-else :size="12" />
                  </div>
                  <div v-if="expandedTools[call.id]" class="tool-body">
                    <div style="color: var(--text-3)">{{ t('参数', 'Arguments') }}:</div>
                    <div>{{ prettyArgs(call.arguments) }}</div>
                    <div v-if="call.output" style="color: var(--text-3); margin-top: 8px">{{ t('结果', 'Result') }}:</div>
                    <div v-if="call.output">{{ call.output.slice(0, 8000) }}</div>
                    <div v-if="call.backupId" style="margin-top: 8px">
                      <button v-if="!call.undone" class="btn sm" @click="undoWrite(call)"><Undo2 :size="12" /> {{ t('撤销写入', 'Undo write') }}</button>
                      <span v-else class="badge green">{{ t('已撤销', 'Undone') }}</span>
                    </div>
                  </div>
                </div>

                <div v-for="approval in message.approvals" :key="approval.id" class="approval-card">
                  <div class="approval-title">
                    <AlertTriangle :size="15" />
                    {{ t('需要你的批准', 'Approval required') }} · {{ approval.title }}
                  </div>
                  <pre v-if="approval.detail?.diff" class="diff-block">{{ approval.detail.diff }}</pre>
                  <div class="approval-detail">{{ prettyArgs({ ...approval.detail, diff: undefined }) }}</div>
                  <div v-if="approval.status === 'pending'" class="approval-actions">
                    <button class="btn sm primary" @click="decide(approval, true)"><Check :size="12" /> {{ t('允许', 'Allow') }}</button>
                    <button class="btn sm" @click="decide(approval, false)"><X :size="12" /> {{ t('拒绝', 'Deny') }}</button>
                    <label class="row" style="gap: 6px; cursor: pointer; font-size: 11.5px; color: var(--text-2)">
                      <span class="checkbox" :class="{ on: approvalsRemember[approval.id] }" @click="approvalsRemember[approval.id] = !approvalsRemember[approval.id]">
                        <Check v-if="approvalsRemember[approval.id]" :size="11" />
                      </span>
                      {{ t('本会话记住', 'Remember for this chat') }}
                    </label>
                  </div>
                  <div v-else class="tiny">
                    {{ approval.status === 'approved' ? t('已允许', 'Approved') : t('已拒绝', 'Denied') }}
                  </div>
                </div>

                <div v-if="message.error" class="alert error" style="margin-top: 8px">{{ message.error }}</div>
                <div v-if="message.usage" class="tiny" style="margin-top: 6px">
                  tokens: {{ message.usage.total_tokens || message.usage.prompt_tokens + message.usage.completion_tokens || 0 }}
                </div>
                <div v-if="message.content" class="msg-actions">
                  <button class="icon-btn sm" :title="t('复制', 'Copy')" @click="copyMessage(message)"><Copy :size="12" /></button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="ai-composer">
          <div class="ai-composer-inner">
            <div v-if="referenced.length" class="row" style="gap: 6px; flex-wrap: wrap; margin-bottom: 6px">
              <span v-for="asset in referenced" :key="asset.id" class="badge accent" style="gap: 5px">
                {{ asset.name }}
                <X :size="10" style="cursor: pointer" @click="removeRef(asset.id)" />
              </span>
            </div>
            <div class="composer-box">
              <textarea
                ref="composerEl"
                v-model="input"
                :placeholder="t('向 H1KHub 任意提问，@ 引用资产，Ctrl+Enter 发送', 'Ask anything, @ to reference assets, Ctrl+Enter to send')"
                @keydown="onKeydown"
                @input="autoGrow"
              />
              <div class="composer-bar">
                <button class="btn ghost sm" :title="t('引用资产', 'Reference asset')" @click="mentionMenu">
                  <AtSign :size="13" /> {{ t('引用资产', 'Assets') }}
                </button>
                <span class="tiny">{{ currentProviderLabel }}</span>
                <span class="grow" />
                <button v-if="sending" class="btn sm" @click="stop"><Square :size="12" /> {{ t('停止', 'Stop') }}</button>
                <button class="send-btn" :disabled="!input.trim() || sending" :title="t('发送', 'Send')" @click="send">
                  <Loader2 v-if="sending" :size="15" class="tool-spin" />
                  <ArrowUp v-else :size="16" />
                </button>
              </div>
            </div>
            <div class="tiny" style="margin-top: 6px; display: flex; gap: 10px; align-items: center">
              <span>{{ t('Enter 发送 · Shift+Enter 换行', 'Enter to send · Shift+Enter for newline') }}</span>
              <span class="grow" />
              <span v-if="provider">{{ provider.protocol === 'anthropic' ? 'Anthropic' : 'OpenAI-compatible' }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
