<script setup>
import { computed } from 'vue'
import { X } from 'lucide-vue-next'
import AssetIcon from './AssetIcon.vue'
import { state, t } from '../lib/store'

const groups = [
  {
    title: t('终端与远程', 'Terminal & remote'),
    items: [
      { type: 'Shell', label: t('本地终端', 'Local terminal'), icon: 'square-terminal', color: '#37c2b1', desc: t('使用 PTY 打开本机 Shell', 'Local PTY shell') },
      { type: 'SSH', label: t('SSH', 'SSH'), icon: 'server', color: '#7f9cf5', desc: t('密码 / 私钥 / Agent / 跳板 / 代理', 'Password, key, agent, jump, proxy') },
      { type: 'FTP', label: t('SFTP', 'SFTP'), icon: 'folder-tree', color: '#5aa9e6', desc: t('文件浏览、上传下载、在线编辑', 'Browse, transfer and edit files') },
      { type: 'Telnet', label: t('Telnet', 'Telnet'), icon: 'globe', color: '#b07cff', desc: t('原始 TCP 终端', 'Raw TCP terminal') },
      { type: 'SSHTunnel', label: t('SSH 隧道', 'SSH tunnel'), icon: 'git-branch', color: '#7f9cf5', desc: t('规划中', 'Planned'), disabled: true },
      { type: 'Serial', label: t('串口', 'Serial'), icon: 'usb', color: '#9aa0a6', desc: t('规划中', 'Planned'), disabled: true },
    ],
  },
  {
    title: t('数据库与缓存', 'Databases & cache'),
    items: [
      { type: 'DB', label: 'MySQL', icon: 'database', color: '#5aa9e6', dbType: 'mysql', desc: 'MySQL / MariaDB' },
      { type: 'DB', label: 'PostgreSQL', icon: 'database', color: '#5aa9e6', dbType: 'postgresql', desc: 'PostgreSQL / Kingbase' },
      { type: 'DB', label: 'SQLite', icon: 'database', color: '#5aa9e6', dbType: 'sqlite', desc: t('本地数据库文件', 'Local database file') },
      { type: 'DB', label: 'DuckDB', icon: 'database', color: '#5aa9e6', dbType: 'duckdb', desc: t('嵌入式分析数据库文件', 'Embedded analytics database file') },
      { type: 'DB', label: 'ClickHouse', icon: 'database', color: '#5aa9e6', dbType: 'clickhouse', desc: t('列式分析数据库', 'Column-oriented analytics database') },
      { type: 'DB', label: 'MongoDB', icon: 'database', color: '#5aa9e6', dbType: 'mongodb', desc: t('文档数据库 / 集合浏览', 'Document store / collection browser') },
      { type: 'DB', label: 'SQL Server', icon: 'database', color: '#5aa9e6', dbType: 'sqlserver', desc: t('SQL Server / Azure SQL', 'SQL Server / Azure SQL') },
      { type: 'DB', label: 'Oracle', icon: 'database', color: '#5aa9e6', dbType: 'oracle', desc: t('Oracle 12c+（thin 模式）', 'Oracle 12c+ (thin mode)') },
      { type: 'DB', label: '达梦', icon: 'database', color: '#5aa9e6', dbType: 'dameng', desc: t('达梦 DM8', 'Dameng DM8') },
      { type: 'Redis', label: 'Redis', icon: 'database-zap', color: '#e05d5d', desc: t('键空间浏览与控制台', 'Key browser and console') },
    ],
  },
  {
    title: t('容器与工具', 'Containers & tools'),
    items: [
      { type: 'Docker', label: 'Docker', icon: 'container', color: '#4c9aff', desc: t('容器、镜像、卷、网络', 'Containers, images, volumes, networks') },
      { type: 'SSHPrivateKey', label: t('SSH 私钥', 'SSH key'), icon: 'key', color: '#c9a227', desc: t('供 SSH / SFTP 引用', 'Referenced by SSH / SFTP') },
      { type: 'QuickCmd', label: t('快捷命令', 'Quick command'), icon: 'terminal-square', color: '#f0a35e', desc: t('向终端发送常用命令', 'Send a command to a terminal') },
      { type: 'Dir', label: t('目录', 'Folder'), icon: 'folder', color: '#d9b45a', desc: t('资产分组', 'Group assets') },
    ],
  },
]

const parentId = computed(() => state.pickerParentId || null)

function choose(item) {
  if (item.disabled) return
  state.pickerOpen = false
  state.assetDialog = { open: true, typeName: item.type, parentId: parentId.value, asset: null, preset: item.dbType ? { dbType: item.dbType } : null }
}
</script>

<template>
  <div v-if="state.pickerOpen" class="modal-mask" @mousedown.self="state.pickerOpen = false">
    <div class="modal" style="width: 720px">
      <div class="modal-head">
        <span class="modal-title">{{ t('新建连接', 'New connection') }}</span>
        <button class="icon-btn sm" @click="state.pickerOpen = false"><X :size="14" /></button>
      </div>
      <div class="modal-body" style="max-height: 70vh">
        <div v-for="group in groups" :key="group.title" style="margin-bottom: 18px">
          <div class="tiny" style="margin-bottom: 8px; text-transform: uppercase; letter-spacing: .6px">{{ group.title }}</div>
          <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px">
            <div
              v-for="item in group.items"
              :key="item.label"
              class="home-card"
              :style="{ padding: '11px', opacity: item.disabled ? 0.45 : 1, cursor: item.disabled ? 'not-allowed' : 'pointer' }"
              @click="choose(item)"
            >
              <div class="row" style="gap: 8px">
                <AssetIcon :icon="item.icon" :size="18" :color="item.color" />
                <div style="font-size: 12.5px; color: var(--text)">{{ item.label }}</div>
              </div>
              <div class="hc-sub">{{ item.desc }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
