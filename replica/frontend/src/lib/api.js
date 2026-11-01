// HTTP + WebSocket RPC client for the H1KHub replica backend.
import { decodeJson, decodePacket, encodePacket } from './packet'

const API_BASE = ''

let token = sessionStorage.getItem('h1khub.token') || ''
let tokenPromise = null

export function getToken() {
  return token
}

export async function ensureToken(force = false) {
  if (token && !force) return token
  if (tokenPromise) return tokenPromise
  tokenPromise = (async () => {
    const res = await fetch(`${API_BASE}/api/auth/token`, { method: 'POST' })
    if (!res.ok) throw new Error(`无法获取本地会话令牌（HTTP ${res.status}）`)
    const payload = await res.json()
    token = payload?.data?.token || ''
    if (!token) throw new Error('本地会话令牌为空')
    sessionStorage.setItem('h1khub.token', token)
    return token
  })()
  try {
    return await tokenPromise
  } finally {
    tokenPromise = null
  }
}

async function request(method, path, body, options = {}) {
  const t = await ensureToken()
  const headers = { 'X-H1KHub-Token': t }
  let payload
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const res = await fetch(`${API_BASE}${path}`, { method, headers, body: payload, ...options })
  const ctype = res.headers.get('Content-Type') || ''
  if (res.status === 401 && !ctype.includes('application/json')) {
    // The auth middleware answers a bare text/html 401 when the local session
    // token expired; application-level JSON 401s (a wrong screen-lock password)
    // must not be retried or every attempt would count twice.
    await ensureToken(true)
    return request(method, path, body, options)
  }
  if (!ctype.includes('application/json')) {
    const text = await res.text()
    if (!res.ok) throw new Error(text || `HTTP ${res.status}`)
    return text
  }
  const data = await res.json()
  if (!res.ok) {
    const message = typeof data.error === 'string' ? data.error : data.error?.message || `HTTP ${res.status}`
    const err = new Error(message)
    err.status = res.status
    err.payload = data
    throw err
  }
  return data.data ?? data
}

export const api = {
  get: (path, params) => {
    const qs = params
      ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString()
      : ''
    return request('GET', path + qs)
  },
  post: (path, body = {}, options) => request('POST', path, body, options),
  put: (path, body = {}) => request('PUT', path, body),
  del: (path, body) => request('DELETE', path, body),
  raw: request,
}

export async function postDownload(path, body, fallbackName) {
  const t = await ensureToken()
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'X-H1KHub-Token': t, 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  })
  if (!res.ok) {
    let message = `下载失败（HTTP ${res.status}）`
    try {
      const data = await res.json()
      message = data.error || message
    } catch {}
    throw new Error(message)
  }
  const blob = await res.blob()
  const cd = res.headers.get('Content-Disposition') || ''
  let name = fallbackName || 'download'
  const m = /filename="?([^";]+)"?/.exec(cd)
  if (m) name = decodeURIComponent(m[1])
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 4000)
  return name
}

export async function uploadTo(path, query, file) {
  const t = await ensureToken()
  const qs = new URLSearchParams(query || {}).toString()
  const res = await fetch(`${API_BASE}${path}${qs ? '?' + qs : ''}`, {
    method: 'POST',
    headers: { 'X-H1KHub-Token': t, 'Content-Type': 'application/octet-stream' },
    body: file,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `上传失败（HTTP ${res.status}）`)
  return data.data
}

export async function fetchTextFile(path, query) {
  const t = await ensureToken()
  const qs = new URLSearchParams(query || {}).toString()
  const res = await fetch(`${API_BASE}${path}${qs ? '?' + qs : ''}`, {
    headers: { 'X-H1KHub-Token': t },
  })
  if (!res.ok) throw new Error(`读取失败（HTTP ${res.status}）`)
  return res.text()
}

/* ------------------------------------------------------------------ RPC */
class RpcClient {
  constructor() {
    this.ws = null
    this.connecting = null
    this.handlers = new Map()
    this.openWaiters = new Map()
    this.ready = false
    this.queue = []
    this.lastError = ''
    this.channelIds = new Map()
    this.channelNames = new Map()
    this.nextId = 1
    this.pending = new Map()
    this.mode = 'binary'
    this.protoReplies = 0
  }

  url() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${proto}//${location.host}/rpc?token=${encodeURIComponent(token)}`
  }

  async ensure() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return this.ws
    if (this.connecting) return this.connecting
    await ensureToken()
    this.connecting = new Promise((resolve, reject) => {
      let settled = false
      let ws
      try {
        ws = new WebSocket(this.url())
      } catch (err) {
        reject(err)
        return
      }
      ws.binaryType = 'arraybuffer'
      ws.onopen = () => {
        this.ws = ws
        this.ready = true
        this.connecting = null
        settled = true
        const pending = this.queue.splice(0)
        for (const item of pending) ws.send(item)
        resolve(ws)
      }
      ws.onmessage = (evt) => this.dispatch(evt)
      ws.onerror = () => {
        if (!settled) {
          this.connecting = null
          reject(new Error('无法连接本地 RPC 服务'))
        }
      }
      ws.onclose = () => {
        this.ready = false
        this.ws = null
        for (const [channel, handler] of this.handlers) {
          try {
            handler({ type: 'close' })
          } catch {}
        }
        for (const waiter of this.openWaiters.values()) {
          try { waiter.reject(new Error('本地 RPC 连接已关闭')) } catch {}
        }
        this.openWaiters.clear()
        if (!settled) {
          this.connecting = null
          reject(new Error('本地 RPC 连接已关闭'))
        }
      }
    })
    return this.connecting
  }

  frame(method, id, payload) {
    try {
      return encodePacket(method, id, new TextEncoder().encode(JSON.stringify(payload ?? {})))
    } catch {
      return null
    }
  }

  dispatch(evt) {
    if (typeof evt.data === 'string') {
      this.dispatchJson(evt.data)
      return
    }
    let pkt
    try {
      pkt = decodePacket(evt.data)
    } catch {
      return
    }
    if (!pkt) return
    if (pkt.method === 'welcome') {
      this.mode = 'binary'
      if (typeof window !== 'undefined') window.__h1khubRpc = { mode: 'binary', protoReplies: this.protoReplies }
      return
    }
    if (pkt.method === 'ChannelOpen') {
      const body = decodeJson(pkt.data)
      const channel = body.method || this.channelNames.get(pkt.id) || ''
      const cid = body.channelId ?? pkt.id
      if (channel) {
        this.channelIds.set(channel, cid)
        this.channelNames.set(cid, channel)
      }
      const waiter = this.openWaiters.get(channel)
      if (waiter) {
        this.openWaiters.delete(channel)
        waiter.resolve(body.data || {})
      }
      return
    }
    if (pkt.method === 'ChannelSend') {
      const channel = this.channelNames.get(pkt.id) || ''
      const data = decodeJson(pkt.data)
      if (!channel) return
      if (data && data.type === 'error') {
        const waiter = this.openWaiters.get(channel)
        if (waiter) {
          this.openWaiters.delete(channel)
          waiter.reject(new Error(data.message || 'RPC 错误'))
        }
      }
      const handler = this.handlers.get(channel)
      if (handler) handler(data || {})
      return
    }
    if (pkt.method === 'error') {
      const msg = new TextDecoder().decode(pkt.data)
      const waiter = this.pending.get(pkt.id)
      if (waiter) {
        this.pending.delete(pkt.id)
        waiter.reject(new Error(msg || 'RPC 错误'))
      }
      return
    }
    const waiter = this.pending.get(pkt.id)
    if (waiter) {
      this.pending.delete(pkt.id)
      if (pkt.method === 'db.query' || pkt.method === 'db.tableData') {
        this.protoReplies += 1
        if (typeof window !== 'undefined') window.__h1khubRpc = { mode: 'binary', protoReplies: this.protoReplies }
      }
      waiter.resolve(pkt.data)
    }
  }

  dispatchJson(raw) {
    let packet
    try {
      packet = JSON.parse(raw)
    } catch {
      return
    }
    this.mode = 'json'
    const channel = packet.channel
    if (packet.method === 'welcome') return
    if (packet.method === 'open') {
      const waiter = this.openWaiters.get(channel)
      if (waiter) {
        this.openWaiters.delete(channel)
        waiter.resolve(packet.data)
      }
      return
    }
    if (packet.method === 'error') {
      const waiter = this.openWaiters.get(channel)
      if (waiter) {
        this.openWaiters.delete(channel)
        waiter.reject(new Error(packet.data?.message || 'RPC 错误'))
      }
      const handler = this.handlers.get(channel)
      if (handler) handler({ type: 'error', message: packet.data?.message })
      return
    }
    if (packet.method === 'event') {
      const handler = this.handlers.get(channel)
      if (handler) handler(packet.data || {})
    }
  }

  on(channel, handler) {
    this.handlers.set(channel, handler)
    return () => {
      if (this.handlers.get(channel) === handler) this.handlers.delete(channel)
    }
  }

  async open(channel) {
    const ws = await this.ensure()
    const promise = new Promise((resolve, reject) => {
      this.openWaiters.set(channel, { resolve, reject })
      setTimeout(() => {
        if (this.openWaiters.has(channel)) {
          this.openWaiters.delete(channel)
          reject(new Error('打开通道超时'))
        }
      }, 12000)
    })
    const frame = this.frame('ChannelOpen', 0, { method: channel, args: {} })
    if (ws.readyState === WebSocket.OPEN) ws.send(frame)
    else this.queue.push(frame)
    return promise
  }

  async send(channel, data) {
    const ws = await this.ensure()
    const cid = this.channelIds.get(channel)
    if (cid === undefined) return
    const frame = this.frame('ChannelSend', cid, data)
    if (ws.readyState === WebSocket.OPEN) ws.send(frame)
    else this.queue.push(frame)
  }

  close(channel) {
    const cid = this.channelIds.get(channel)
    if (this.ws && this.ws.readyState === WebSocket.OPEN && cid !== undefined) {
      this.ws.send(this.frame('ChannelClose', cid, {}))
    }
    this.channelIds.delete(channel)
    if (cid !== undefined) this.channelNames.delete(cid)
    this.handlers.delete(channel)
  }

  /** Request/reply call; resolves with the raw reply bytes (protobuf or JSON). */
  async call(method, args = {}, timeout = 15000) {
    const ws = await this.ensure()
    const id = this.nextId++
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id)
          reject(new Error(`RPC ${method} 超时`))
        }
      }, timeout)
    })
    const frame = this.frame(method, id, args)
    if (ws.readyState === WebSocket.OPEN) ws.send(frame)
    else this.queue.push(frame)
    return promise
  }
}

export const rpc = new RpcClient()

export function bytesToBase64(bytes) {
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk))
  }
  return btoa(binary)
}

export function base64ToBytes(b64) {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes
}
