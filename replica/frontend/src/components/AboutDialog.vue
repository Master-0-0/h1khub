<script setup>
import { computed } from 'vue'
import { BookOpen, Github, ShieldCheck, X } from 'lucide-vue-next'
import { state, t } from '../lib/store'
import UpdateCheckPanel from './UpdateCheckPanel.vue'

const info = computed(() => state.info || {})
const version = computed(() => info.value.version || '5.1.9')
</script>

<template>
  <div v-if="state.aboutOpen" class="modal-mask" @mousedown.self="state.aboutOpen = false">
    <div class="modal" style="width: 560px">
      <div class="modal-head">
        <span class="modal-title">{{ t('关于 H1KHub', 'About H1KHub') }}</span>
        <button class="icon-btn sm" @click="state.aboutOpen = false"><X :size="14" /></button>
      </div>
      <div class="modal-body">
        <div class="row" style="gap: 14px; align-items: flex-start">
          <div class="home-logo" style="width: 56px; height: 56px; border-radius: 15px">
            <span class="mark-glyph" style="font-size: 24px; letter-spacing: -1.2px">H1K</span>
          </div>
          <div style="flex: 1">
            <div style="font-size: 17px; font-weight: 650">H1KHub</div>
            <div class="muted" style="margin-top: 3px">
              {{ t('版本', 'Version') }} {{ version }} · {{ t('本地版', 'Local build') }} · build {{ info.build || 519 }}
            </div>
            <div class="muted text-xs" style="margin-top: 6px">
              {{ info.os }} {{ info.arch }} · {{ info.hostname }} · {{ t('端口', 'port') }} {{ info.port }}
            </div>
          </div>
        </div>

        <div class="divider" />

        <div style="margin-bottom: 12px">
          <div class="row" style="justify-content: space-between; margin-bottom: 6px">
            <b style="font-size: 12.5px">{{ t('版本检查', 'Version check') }}</b>
            <span class="muted text-xs">{{ version }} · build {{ info.build || 519 }}</span>
          </div>
          <UpdateCheckPanel />
        </div>

        <div class="divider" />

        <p style="line-height: 1.7; font-size: 12.5px; margin: 0 0 10px">
          {{ t('H1KHub 是基于《HexHub 5.1.9 逆向拆解报告》独立实现的一体化本机工作台：本地 HTTP + WebSocket RPC 服务、内嵌 Vue 3 单页应用、真实 SSH/SFTP/数据库/Redis/Docker/AI 能力。',
               'H1KHub is an independent local-first workspace built from the HexHub 5.1.9 reverse-engineering report: a local HTTP + WebSocket RPC server with an embedded Vue 3 SPA and real SSH, SFTP, database, Redis, Docker and AI capabilities.') }}
        </p>

        <div class="alert info" style="margin-bottom: 12px">
          <div class="row" style="margin-bottom: 5px"><ShieldCheck :size="14" /><b>{{ t('安全说明', 'Security notes') }}</b></div>
          <ul style="margin: 0; padding-left: 18px; line-height: 1.7">
            <li>{{ t('所有凭据使用 AES-256-GCM 加密存储，密钥仅保存在本机。', 'Credentials are encrypted at rest with AES-256-GCM; the key never leaves this machine.') }}</li>
            <li>{{ t('本地 API 需要会话令牌，仅监听 127.0.0.1。', 'The local API requires a session token and listens on 127.0.0.1 only.') }}</li>
            <li>{{ t('SSH 首次连接采用 TOFU 记录主机指纹，变更时会拒绝连接。', 'SSH uses TOFU host-key pinning and refuses changed fingerprints.') }}</li>
            <li>{{ t('AI 写文件、执行命令、写 SQL 默认需要人工审批。', 'AI file writes, commands and SQL writes require approval by default.') }}</li>
          </ul>
        </div>

        <div class="row" style="gap: 8px; color: var(--text-3); font-size: 11.5px">
          <BookOpen :size="14" />
          <span>{{ t('架构', 'Architecture') }}: Python · aiohttp · paramiko · Vue 3 · Vite · xterm.js · CodeMirror 6</span>
        </div>
        <div class="row" style="gap: 8px; color: var(--text-3); font-size: 11.5px; margin-top: 6px">
          <Github :size="14" />
          <span>{{ t('数据目录', 'Data directory') }}: {{ info.dataHome }}</span>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn" @click="state.aboutOpen = false">{{ t('关闭', 'Close') }}</button>
        <button class="btn primary" @click="state.aboutOpen = false; state.settingsOpen = true">{{ t('打开设置', 'Open settings') }}</button>
      </div>
    </div>
  </div>
</template>
