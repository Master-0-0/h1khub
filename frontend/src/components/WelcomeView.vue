<script setup>
import { computed } from 'vue'
import {
  ArrowRight, Container, Database, History, Plus, Server, Sparkles, SquareTerminal, Zap,
} from 'lucide-vue-next'
import {
  newLocalTerminal, openAi, openDockerDefault, openHistory, openTab, state, t, openAsset,
} from '../lib/store'
import { timeAgo } from '../lib/format'

const recent = computed(() => state.history.slice(0, 6))

function newSsh() {
  state.assetDialog = { open: true, typeName: 'SSH', parentId: null, asset: null }
}

function newDb() {
  state.assetDialog = { open: true, typeName: 'DB', parentId: null, asset: null }
}

function openRecent(item) {
  const asset = state.assets.find((a) => a.id === item.asset_id)
  if (asset) openAsset(asset)
}

const shortcuts = [
  { label: '全局搜索', keys: ['Ctrl', 'K'] },
  { label: '最近历史标签', keys: ['Ctrl', 'E'] },
  { label: '设置', keys: ['Ctrl', ','] },
  { label: '新终端', keys: ['Ctrl', '`'] },
  { label: '关闭当前标签', keys: ['Ctrl', 'W'] },
  { label: '切换标签', keys: ['Alt', '1…9'] },
]
</script>

<template>
  <div class="home">
    <div class="home-logo">
      <span class="mark-glyph" style="font-size: 27px; letter-spacing: -1.4px">H1K</span>
    </div>
    <div style="text-align: center">
      <h1>{{ t('欢迎使用 H1KHub', 'Welcome to H1KHub') }}</h1>
      <div class="sub">{{ t('数据库 · SSH · SFTP · Docker · Redis · AI，一站式本机工作台', 'Databases, SSH, SFTP, Docker, Redis and AI in one local workspace') }}</div>
    </div>

    <div class="home-cards">
      <div class="home-card" @click="newSsh">
        <Server class="hc-icon" :size="20" />
        <div class="hc-title">{{ t('新建 SSH 连接', 'New SSH connection') }}</div>
        <div class="hc-sub">{{ t('密码 / 私钥 / Agent，支持跳板与代理', 'Password, key or agent; jump host and proxy') }}</div>
      </div>
      <div class="home-card" @click="newDb">
        <Database class="hc-icon" :size="20" />
        <div class="hc-title">{{ t('连接数据库', 'Connect database') }}</div>
        <div class="hc-sub">{{ t('MySQL / PostgreSQL / SQLite 数据浏览与 SQL', 'MySQL, PostgreSQL and SQLite browsing + SQL') }}</div>
      </div>
      <div class="home-card" @click="openDockerDefault()">
        <Container class="hc-icon" :size="20" />
        <div class="hc-title">{{ t('管理 Docker', 'Manage Docker') }}</div>
        <div class="hc-sub">{{ t('容器、镜像、卷、网络与实时日志', 'Containers, images, volumes, networks and logs') }}</div>
      </div>
      <div class="home-card" @click="openAi()">
        <Sparkles class="hc-icon" :size="20" />
        <div class="hc-title">{{ t('AI 助手', 'AI assistant') }}</div>
        <div class="hc-sub">{{ t('调用真实工具操作资产，写操作需审批', 'Operate assets with tools; approvals for writes') }}</div>
      </div>
    </div>

    <div class="home-shortcuts">
      <div v-for="sc in shortcuts" :key="sc.label" class="home-sc">
        <span>{{ t(sc.label, sc.label) }}</span>
        <span class="keys"><span v-for="k in sc.keys" :key="k" class="kbd">{{ k }}</span></span>
      </div>
    </div>

    <div v-if="recent.length" style="width: 100%; max-width: 700px">
      <div class="row" style="margin-bottom: 8px; color: var(--text-3); font-size: 11.5px">
        <History :size="13" />
        <span>{{ t('最近打开', 'Recently opened') }}</span>
        <span class="grow" />
        <button class="btn ghost sm" @click="openHistory">{{ t('查看全部', 'View all') }} <ArrowRight :size="13" /></button>
      </div>
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px">
        <div
          v-for="item in recent"
          :key="item.id"
          class="tree-row"
          style="background: var(--bg-panel); border: 1px solid var(--border); height: 34px"
          @click="openRecent(item)"
        >
          <span class="dot" :class="{ off: item.kind !== 'open' }" />
          <span class="tree-name">{{ item.title }}</span>
          <span class="tiny">{{ timeAgo(item.created_at) }}</span>
        </div>
      </div>
    </div>

    <div class="row" style="gap: 10px; color: var(--text-3); font-size: 11.5px">
      <Zap :size="13" />
      <span>{{ t('本地优先 · 凭据 AES-256-GCM 加密存储 · 所有操作在你自己的机器上完成', 'Local-first · credentials encrypted with AES-256-GCM · everything runs on your machine') }}</span>
    </div>
  </div>
</template>
