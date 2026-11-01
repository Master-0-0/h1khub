<script setup>
import { computed } from 'vue'
import {
  Cloud, Command, Container, Database, DatabaseZap, FileCode, Folder, FolderTree, GitBranch,
  Globe, KeyRound, Layers, Monitor, Server, Settings, Shield, Sparkles, SquareTerminal, Usb,
} from 'lucide-vue-next'

const props = defineProps({
  typeName: { type: String, default: '' },
  icon: { type: String, default: '' },
  size: { type: [Number, String], default: 16 },
  color: { type: String, default: 'currentColor' },
  strokeWidth: { type: [Number, String], default: 1.7 },
})

const ICONS = {
  folder: Folder,
  'folder-tree': FolderTree,
  key: KeyRound,
  server: Server,
  database: Database,
  'database-zap': DatabaseZap,
  'terminal-square': SquareTerminal,
  'square-terminal': SquareTerminal,
  'file-code': FileCode,
  container: Container,
  layers: Layers,
  settings: Settings,
  command: Command,
  monitor: Monitor,
  cloud: Cloud,
  usb: Usb,
  globe: Globe,
  shield: Shield,
  'git-branch': GitBranch,
  sparkles: Sparkles,
}

const TYPE_ICONS = {
  Dir: 'folder',
  SSHPrivateKey: 'key',
  SSH: 'server',
  FTP: 'folder-tree',
  DB: 'database',
  QuickCmd: 'terminal-square',
  DBQuery: 'file-code',
  Docker: 'container',
  DockerRegistry: 'layers',
  Setting: 'settings',
  Hotkey: 'command',
  QuickCmdGroup: 'folder',
  RDP: 'monitor',
  VNC: 'monitor',
  RepositorySource: 'cloud',
  Redis: 'database-zap',
  Shell: 'square-terminal',
  Serial: 'usb',
  Telnet: 'globe',
  SSHPasswordPair: 'shield',
  SSHTunnel: 'git-branch',
  LLMProviderConfig: 'sparkles',
}

const key = computed(() => props.icon || TYPE_ICONS[props.typeName] || 'folder')
const component = computed(() => ICONS[key.value] || Folder)
</script>

<template>
  <component :is="component" :size="size" :color="color" :stroke-width="strokeWidth" />
</template>
