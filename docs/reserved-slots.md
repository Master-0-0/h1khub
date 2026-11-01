# 预留扩展点地图（第二十三轮，2026-10-31）

> **第二十二轮实现了 90 的「锁屏」一半；第二十三轮实现了 91 版本检查与 93 更新通道。**
> 90 系统托盘、92 热更新、95 云加密、98 MCP、99 插件市场仍未实现或被判定为不可用。
>
> - 计数：`docs/completion-audit.csv` = **已复刻 80 / 未实现 13 / 形态替代 4 / 改进 2（99 行）**。
> - 本地口径：**83/84（98.8%）**（排除 15 条：1,2,5,6,12,79-85,92,98,99；
>   95 云加密是唯一剩余本地缺口；90 已移出排除集，但「系统托盘」仍明确为 `unavailable`，
>   不计入实现。口径推导见 `docs/verification.md` 第 28 节）。
> - 唯一事实源：`backend/capabilities.py::CAPABILITIES`，HTTP 暴露为 `GET /api/capabilities`。
> - 3 个 `reserved` 能力（4 条路由：92 的 start/status、MCP、插件市场）仍返回 **HTTP 501**
>   + `error.code = "FEATURE_RESERVED"`，不返回 `ok:true`。
> - 已实现 3 项：`lock.screen`、`update.check`、`update.channel`；锁屏与更新端点返回真实的
>   `{"ok":false,"error":{"code":"LOCK_..."/"UPDATE_..."}}`，不再是 501。
> - 已生效的配置键：`lock.*`、`update.channel/env/manifestUrl/autoCheck`；
>   `plugins/mcp/cloud` 仍默认关闭，且**没有任何代码读取它们**（没有 gate）。

## 0. 约定

### 0.1 状态三值

| 状态 | 含义 | 本轮数量 |
|---|---|---|
| `reserved` | 未来实现；已有 501 占位路由 | 3 |
| `unavailable` | 产品形态或外部依赖上不可行；**不注册路由**、**不提供 UI 入口** | 2 |
| `done` | 真正实现（`lock.screen` round22；`update.check`/`update.channel` round23） | 3 |

### 0.2 501 统一错误体

```json
{
  "ok": false,
  "error": {
    "code": "FEATURE_RESERVED",
    "feature": "update.hot",
    "reservedIn": "round21",
    "docs": "docs/reserved-slots.md#92-热更新",
    "message": "该能力已预留扩展点，尚未实现"
  }
}
```

构造器：`backend/capabilities.py::reserved_response()`（L140）。传入非 `reserved` 的 id 会直接
`ValueError`，防止误开一个“看起来能用”的端点。

### 0.3 配置键

**已生效（更新，`update`）**：`backend/store.py::DEFAULT_SETTINGS` 与
`frontend/src/lib/store.js::DEFAULT_SETTINGS` 双侧登记：

```js
update: { channel: 'stable', env: 'production', manifestUrl: '', autoCheck: false }
```

- `channel` ∈ `stable|experimental`、`env` ∈ `production|test`，由 `POST /api/update/channel`
  或通用 `PUT /api/settings` 写入；两条路径都做白名单校验，非法值 400，永远不会落库。
- `manifestUrl` / `autoCheck` 由通用 `PUT /api/settings` 写入；`manifestUrl` 在保存时校验
  scheme 与主机（file 绝对路径或 loopback http(s)），`autoCheck` 必须是布尔值。
- `autoCheck` 只影响前端 bootstrap 后的一次延迟检查，不是后台轮询，也不写任何服务端状态。

**已生效（锁屏，`lock`）**：`backend/store.py::DEFAULT_SETTINGS` 与
`frontend/src/lib/store.js::DEFAULT_SETTINGS` 双侧登记：

```js
lock: { enabled: false, idleMinutes: 0, passwordHash: '' }
```

- `passwordHash` 只由 `POST /api/lock/password` / `DELETE /api/lock/password` 写入，值为
  `pbkdf2_sha256$210000$<salt>$<hash>`；`GET /api/settings` 永远剔除该键，通用
  `PUT /api/settings` 也会丢弃整个 `lock` 对象，不能绕过旧口令校验。
- `enabled` / `idleMinutes` 由 `PUT /api/lock/config` 修改；启用前必须已有口令。

**仍为预留（默认关闭，不生效）**：

```js
plugins: { enabled: false, dir: '' },
mcp:     { enabled: false, transport: 'stdio' },
cloud:   { encryption: false },
```

`backend/store.py` 与 `frontend/src/lib/store.js` 必须同步登记这些键，否则
`update_settings` 会丢弃未登记键、前端 `deepMerge` 也会丢键。

### 0.4 UI 入口

`frontend/src/components/SettingsDialog.vue`：

- **安全节（已实现）**：自定义表单，包含锁屏开关、空闲自动锁屏下拉、口令设置/修改/清除、
  「立即锁屏」按钮，以及「系统托盘不可用」说明。所有动作走 `/api/lock/*`。
- **更新节（91/93 已实现）**：版本检查按钮与结果、更新通道（stable/experimental）、发布环境
  （production/test）、自动检查开关、manifest 地址；版本检查/通道/autoCheck/manifest 均可配置。
  「热更新」行仍 `disabled`，`title="预留（未实现）"`，状态来自 `/api/capabilities`。
- **扩展节（仍为预留）**：由 `GET /api/capabilities` 数据驱动，每个控件 `disabled`，
  reserved 行 `title="预留（未实现）"`。
- 「关于」设置节与关于窗口共用 `frontend/src/components/UpdateCheckPanel.vue`，
  「检查更新」按钮可用，显示 current/latest/hasUpdate/notes。
- 云加密（`group = "cloud"`）不属于任何设置节，界面无入口。

锁屏遮罩是独立组件 `frontend/src/components/LockScreen.vue`，固定全屏（`z-index: 2000`），
背景工作区 `inert`，Tab 焦点被限制在遮罩内。

## 预留位总表

| oid | id | 名称 | 状态 | 入口 |
|---|---|---|---|---|
| 90 | `lock.screen` | 锁屏 | **done** | GET /api/lock/status、POST /api/lock/verify 等 |
| 90 | `lock.tray` | 系统托盘 | unavailable | —（浏览器形态无托盘） |
| 91 | `update.check` | 版本检查 | **done** | GET /api/update/check |
| 92 | `update.hot` | 热更新 | reserved | POST /api/update/start、GET /api/update/status |
| 93 | `update.channel` | 更新通道 | **done** | POST /api/update/channel |
| 95 | `cloud.encryption` | 云加密 | unavailable | —（依赖云端会员 RSA，无入口） |
| 98 | `ext.mcp` | MCP | reserved | GET /api/mcp/info |
| 99 | `ext.plugins` | 插件市场 | reserved | GET /api/plugins |

---

## 90 锁屏与托盘

### 现状（第二十二轮：锁屏已实现，托盘仍不可用）

`completion-audit.csv` 第 90 行改为「已复刻」，核查说明明确写「系统托盘仍 unavailable，不计入实现」；
`feature-matrix.csv` 的「设置·锁屏/托盘/多窗口」仍为「部分实现」（多窗口、锁屏已做；托盘不可做）。
第 90 行已移出本地排除集，本地分母 81→82。

原版证据（《拆解报告》）：

- **L419**：「窗口：多窗口、水平/垂直分屏、标签拖拽、标签新窗口、会话恢复、鼠标中键关闭、
  托盘按钮位置、**启动锁屏/自动锁屏/锁屏密码**。」
- **L420**：「托盘：`enableTray`、`setTrayMenu`，菜单项由前端下发。」
- 原版前端（`index-DqhrxGqR.js`）：`lockScreenPassword` 与 `lockScreenTime` 存在设置中，
  `localStorage.lastOpTime` + 10 秒轮询触发遮罩；启动锁屏仅在登录账号后启用；口令比较是明文直接比较。
- 功能矩阵 **L91**（oid 90）：`设置 / 锁屏/托盘 / 启动锁屏/自动锁屏/托盘菜单`。

复刻版刻意不复用原版的明文比较与客户端计时：**哈希只落服务端、锁定状态在服务端判定**，
因此刷新页面或新开标签不能靠清 localStorage 绕过。

### 实现契约

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/lock/status` | GET | `enabled` / `hasPassword` / `locked` / `idleMinutes` / `lockedAt` / `failedAttempts` / `retryAfter` / 口令长度与 scheme；不含哈希 |
| `/api/lock/config` | PUT | `{enabled?, idleMinutes?}`；启用前必须有口令，否则 409 `LOCK_PASSWORD_REQUIRED` |
| `/api/lock/password` | POST | `{oldPassword?, newPassword}`；已有口令时必须验旧口令，成功后自动启用 |
| `/api/lock/password` | DELETE | `{oldPassword}`；校验旧口令后清除哈希并关闭锁屏 |
| `/api/lock/lock` | POST | 立即锁定；未配置时 409 `LOCK_NOT_CONFIGURED` |
| `/api/lock/verify` | POST | `{password}`；未锁定时 409 `LOCK_NOT_LOCKED`；错误 401；退避期 429 |
| `/api/lock/activity` | POST | 前端节流心跳，刷新空闲窗口；不改变锁定状态 |

哈希与退避（`backend/lock_svc.py`）：

- `pbkdf2_sha256$210000$<16B salt>$<32B digest>`，每个口令独立随机 salt；
  比较用 `hmac.compare_digest`，不存明文、不回传哈希。
- 前 2 次错误直接 401；第 3 次起指数退避 2/4/8/…/60 秒，退避窗口内返回 429 + `retryAfter`。
- 成功解锁或成功修改口令会清零失败计数；失败计数是进程级（本地单用户）。
- `lock.enabled=false` 或清除口令会同时解除锁定；锁屏不使会话令牌失效，终端/数据库会话继续运行。

空闲与服务端状态：

- `idleMinutes` 由服务端 `last_activity` 判定；`GET /api/lock/status` **不**算活动，
  所以后台轮询不会让空闲会话保持解锁。
- 前端在 keydown/mousedown/scroll 上节流 15 秒发一次 `/api/lock/activity`；页面隐藏/关闭后心跳停止，
  下次打开时状态接口按已流逝时间直接锁定。
- 锁定状态保存在后端进程内：刷新页面、重开标签、另开浏览器都会读到 `locked=true`。
  后端重启会清空该进程内状态（不持久化锁定标志），见“限制”。

UI：

- `frontend/src/components/LockScreen.vue`：全屏遮罩、口令输入、显示/隐藏、错误提示、
  退避倒计时、失败次数、忘记口令说明；`z-index: 2000`，背景 `.app-body` 为 `inert`，
  Tab 焦点被限制在遮罩内，Esc 不能关闭。
- 设置 → 安全：锁屏开关、空闲时间、口令设置/修改/清除、立即锁屏；系统托盘行保持
  「不可用」并解释浏览器无 `enableTray`/`setTrayMenu`。

### 验收与证据

`scripts/test_lock.py`（PASS=50 FAIL=0，含真实 CDP）：

- 无 token 访问锁屏端点为 401；`POST /api/lock/verify` 不再是 501；
- 首次设置口令、修改需旧口令、错误旧口令 401；`GET /api/settings` 无 `passwordHash`；
  `data.db` 原始字节无明文口令，设置行只有 PBKDF2 记录（16B salt / 32B digest / 210000 轮）；
- 修改口令后 salt 与哈希都变化；旧口令不能再解锁，新口令可以；
- 连续错误计数 1/2/3，第 3 次给 `retryAfter`，退避期 429，退避后正确口令解锁并清零；
- `idleMinutes=0.02` 时活动心跳后不立即锁，2 秒后状态接口返回 `locked=true`；
- 通用 `PUT /api/settings` 不能改 `lock` 或注入 `passwordHash`；
- CDP：全屏/固定/`z-index`/`inert`/输入框存在；错误口令显示错误且不解除；正确口令解除；
  设置页安全节可配置、无哈希泄露、托盘不可用；UI 改空闲时间持久化；设置页「立即锁屏」生效；
  **刷新仍锁定**；**另开浏览器（新标签）仍锁定**；无 JS 异常。

### 限制与风险

1. **防误触，不防对抗**：能读取 `~/.h1khub`（data.db / secret.key / runtime.json）或
   复制本地会话令牌的人可以绕过；这是本地单用户工具的定位，不是多用户安全边界。
2. **锁定标志不持久化**：后端进程重启后 `locked` 回到 false（口令哈希保留）。刷新/新标签/新浏览器
   不受影响；若要跨重启保持锁定，需要把状态写入磁盘并处理恢复，本轮明确不做。
3. **忘记口令恢复**：停止服务后清除 `data.db` 中 `settings.key='lock'` 的 `passwordHash`
   （或删除该行）；没有云端账号可走原版“重置密码并退出登录”。
4. **系统托盘仍不可用**：浏览器没有 `enableTray`/`setTrayMenu`；`lock.tray.status` 永远
   `unavailable`，无路由、无 UI 入口。不得把托盘算作已实现。
5. 失败计数与退避是进程级全局的，不区分来源；本地单用户场景足够，多用户部署需重新设计。

---

## 91 版本检查

### 现状（第二十三轮：已实现并实测）

`completion-audit.csv` 第 91 行改为「已复刻」；`update.check` 状态 `done`
（`implementedIn: round23`）。`GET /api/update/check` 从 501 占位变为真实的只读检查，
读取用户配置的本机 manifest，不访问云端。

原版证据（《拆解报告》）：

- **L393**：「版本 | `client/version/check`、`detail`、`history`」。
- **L425**：「版本检查：云端 `client/version/check|detail|history`；界面 `WebUpdateDialog`、
  `ClientDownloadDialog`。」
- 功能矩阵 **L92**（oid 91）：`更新 / 版本检查 / check/detail/history`。
- 原版没有本地 manifest：检查依赖账号与 `api.hexhub.cn`。复刻版没有账号体系，
  因此改为「用户显式配置的本机 manifest」，只做数值比较，不伪装云端 `detail/history`。

### 实现契约

| 端点 | 方法 | 成功响应 |
|---|---|---|
| `/api/update/check` | GET | `{current:{version,build}, latest:{version,build}, hasUpdate, notes, url, md5, channel, env, manifestUrl, source, checkedAt}` |

manifest 契约（UTF-8 JSON 对象，≤ **256 KiB**；示例见
[`docs/update-manifest.example.json`](update-manifest.example.json)）：

```json
{
  "version": "5.1.10",
  "build": 520,
  "notes": "本版本更新说明",
  "url": "https://example.invalid/h1khub-5.1.10.deb",
  "md5": "0123456789abcdef0123456789abcdef"
}
```

- `version` 必填，点分数字（1–4 段，每段 ≤6 位，如 `5.1.9`）；
- `build` 必填，非负整数（≤10^9），**不接受字符串**；
- `notes` 可选，字符串，响应中截断到 4000 字符；
- `url` 可选，≤2048 字符，仅 `http/https/file`（只展示，不下载）；
- `md5` 可选，32 位十六进制（只展示，不校验包）；
- 其他字段忽略；根不是对象、类型不符或数值非法一律 400。

数值比较（`update_svc.is_newer()`）：先按点分段转整数元组比较 `version`，`version` 相等时再比较
`build`。因此 `5.1.10 > 5.1.9`（不是字符串比较），`5.1.9 build 600 > 5.1.9 build 519`。

错误均为 HTTP **400**（不是 500），统一为
`{"ok":false,"error":{"code":"UPDATE_...","message":"...","feature":"update.check","docs":"docs/reserved-slots.md#91-版本检查"}}`：

| code | 触发 |
|---|---|
| `UPDATE_MANIFEST_NOT_CONFIGURED` | `manifestUrl` 为空 |
| `UPDATE_MANIFEST_URL_INVALID` | scheme 不在 file/http/https、相对路径、file 带远程主机、带凭据 |
| `UPDATE_MANIFEST_FORBIDDEN` | http(s) 主机不是 loopback（含 `api.hexhub.cn`） |
| `UPDATE_MANIFEST_UNAVAILABLE` | 文件不存在/不可读、HTTP 非 200、重定向、连接失败/超时 |
| `UPDATE_MANIFEST_TOO_LARGE` | 文件或 HTTP 响应 > 256 KiB（含 Content-Length 预检） |
| `UPDATE_MANIFEST_INVALID` | 非 UTF-8、非 JSON、schema 不符 |

安全边界（`backend/update_svc.py`）：

- 只读 `update.manifestUrl`。允许 `file://` 绝对路径（authority 为空或 localhost）和
  loopback `http(s)`（`127.0.0.1` / `localhost` / `::1`）；保存配置时即拒绝其他主机，
  因此 `api.hexhub.cn` 永远不会被请求；
- HTTP 客户端 `allow_redirects=False`、`trust_env=False`、5 秒总超时、流式读取并 256 KiB 截断，
  防止 SSRF（含重定向逃逸）与超大输入；
- 不下载 manifest 里的 `url`、不执行任何内容、不写资产库、不持久化检查结果；
- 每次检查都重新读取 manifest 与设置，不缓存“已忽略版本”。

前端：

- `frontend/src/lib/store.js::checkUpdate()` 调 `/api/update/check` 并保存
  `state.updateCheck = {checking, result, error}`；
- `scheduleAutoUpdateCheck()` 在 `App.vue` 的 bootstrap 成功返回后调度 **一次** 1.5 秒延迟检查；
  模块级 flag 保证每页只调度一次，**没有后台轮询**；
- 设置→更新节与关于页/关于窗口共用 `UpdateCheckPanel.vue`，按钮可用，显示
  current/latest/hasUpdate/notes（以及 channel/env/manifestUrl）。

### 验收与证据

`scripts/test_update.py`（新增，**PASS=63 FAIL=0**，API + 真实 CDP）覆盖：

- 未配置、文件不存在、非法 JSON、超大文件、非法 version/build/md5、缺 build → 全部 400 且非 500；
- 更高/相同/更低版本、同版本更高/更低 build、`5.1.10 > 5.1.9` 数值比较；
- 拒绝 `api.hexhub.cn`、ftp、`file://otherhost`、相对路径，且被拒绝的地址不落库；
- 本机 HTTP manifest 成功读取；HTTP 非 JSON、Content-Length 超大、302 重定向全部 400，
  重定向只请求原始路径（未跟随）；
- 检查不修改设置、不新增资产、`/api/update/start|status` 仍 501；
- autoCheck 启动后只调用一次、4 秒后仍为一次；
- 设置→更新节可配置、热更新仍禁用；关于页与关于窗口的检查按钮可用并渲染结果；无 JS 异常。

### 限制与风险

- manifest 没有签名；`url/md5` 只是未来热更新（92）的输入字段，本轮不下载、不校验包；
- 白名单是 SSRF 防护，不是对抗本机用户的边界：能直接改 `data.db` 的人可以写入外部 URL，
  但 `GET /api/update/check` 在读取时仍会再做一次 scheme/host 校验；
- 版本号只支持 1–4 段纯数字；预发布号、日期版本不在契约内；
- 不实现原版 `detail/history`，因为那依赖云端账号与云端数据。

---

## 92 热更新

### 现状（第二十三轮：仍是 reserved）

`completion-audit.csv` 第 92 行仍为「未实现」。`POST /api/update/start`、`GET /api/update/status`
均为 501；没有下载、校验、解压、重启任何逻辑。第二十三轮实现 91/93 时**刻意没有顺手做 92**：
它涉及下载→MD5→7z→runtime→重启与供应链防护，需要新增依赖与单独的威胁模型。

### 预留落点

| 类型 | 位置 |
|---|---|
| 注册表 | `backend/capabilities.py` `update.hot`（L64） |
| 路由 | `POST /api/update/start`、`GET /api/update/status` → 501（`backend/app.py` L3207-3208） |
| 配置键 | `update.channel` / `update.manifestUrl`（`backend/store.py` L95） |
| UI | 设置 → 更新节「热更新」行（仍禁用，`title=预留（未实现）`） |

### 下一轮实现要点

原版证据：

- **L427-432**：`hotUpdateStartHandle` 接收 `versionId` → `fetchHotUpdateDetail` →
  下载到临时目录 → `computeFileMD5` 与详情 MD5 `strings.EqualFold` 比较 →
  `extractSevenZipArchive`（`mholt/archives`/`sevenzip`），每个条目过
  `sanitizeArchiveEntryPath`（防 Zip-Slip）→ 安装到 `runtime/<version>` →
  `ensureHotUpdateExecutableExists`、`writeRuntimeCurrentEntry`（原子写 `current.json`）→
  `hotUpdatePrepareRestartHandle` → 清理旧 runtime。
- **L465-467**：`POST /hot-update/start`、`GET /hot-update/status`、
  `/hot-update/prepare-restart`（handler 名推断）。
- 功能矩阵 **L93**（oid 92）：`更新 / 热更新 / 下载 7z -> MD5 比对 -> 解压 -> 重启`。

实现建议：先做“可中断的任务 + 状态机”（`idle/downloading/verifying/installing/ready/failed`），
MD5 比对失败必须保留现场并回报 `Failed to hash package` 等价错误；解压前逐条目规范化路径；
安装目录与 `current.json` 原子写；重启采用“写入待重启标记 + 退出进程”，由外部启动器拉起，
不在请求线程里 `exec`。Python 侧可用 `py7zr`，但**在实现 92 前仍不加依赖**，所以只留接口与文档。

### 验收标准

- 未配置 manifest / 包不存在 / MD5 不符 / 包含 `../` 条目时，状态为 failed 且不落盘。
- 成功后 `runtime/<version>` 与 `current.json` 一致，旧版本目录可被清理。
- 任何失败都不破坏当前运行版本（回滚到 `current.json` 指向的旧版本）。

### 风险

- 这是剩余预留中风险最高的一项：涉及下载、解压、替换可执行文件，必须做签名/哈希与路径穿越防护。
- 浏览器形态没有“重启后端”的权限，最终形态可能需要外部守护进程；先做状态机再谈自动重启。

---

## 93 更新通道

### 现状（第二十三轮：已实现并实测）

`completion-audit.csv` 第 93 行改为「已复刻」；`update.channel` 状态 `done`
（`implementedIn: round23`）。`POST /api/update/channel` 不再 501，而是校验白名单、
持久化 `update.channel` / `update.env`，并由二者共同选择版本检查读取的 manifest。

原版证据：

- **L426**：「通道：`稳定通道` / `实验性通道`；发布环境 `production/test`（`set-env`、
  `isVisibleReleaseEnv`）。」
- 功能矩阵 **L94**（oid 93）：`更新 / 通道 / 稳定/实验性；production/test`。

### 实现契约

| 端点 | 方法 | 请求 | 成功响应 |
|---|---|---|---|
| `/api/update/channel` | POST | `{channel?, env?}`，至少一个 | `{channel, env, manifestUrl, autoCheck, manifestResolved}` |

- 白名单：`channel ∈ {stable, experimental}`、`env ∈ {production, test}`，大小写敏感；
  其他值 400 `UPDATE_INVALID_CHANNEL` / `UPDATE_INVALID_ENV`；空请求体 400；
- 持久化到 settings 的 `update.channel` / `update.env`（不新增数据库表）；
- 通用 `PUT /api/settings` 也可写入这两个键（设置对话框保存整份草稿），但走同一套白名单校验，
  非法值 400 且不落库；
- 不触发下载、不重启、不改变运行版本；测试断言切换前后 `/api/public/info.startedAt` 不变；
- `manifestResolved` 只是 URL 解析结果（模板替换/目录补全），**不读取文件/网络**。

manifest 选择规则（`update_svc.resolve_manifest_url()`）：

| `update.manifestUrl` 形态 | 选择结果 |
|---|---|
| 含 `{channel}` / `{env}` 占位符 | 替换后使用，例如 `file:///srv/updates/{channel}-{env}.json` |
| 以 `/` 结尾的目录 | 追加 `update-<channel>-<env>.json` |
| 其他（普通文件 URL） | 所有通道共用该文件 |

通道与 env 是两个正交维度，分别存储、分别校验，不会互相覆盖。

### 验收与证据

`scripts/test_update.py` 覆盖：

- `channel=beta`、`env=dev`、空请求体、通用 PUT 写非法通道 → 全部 400；
- `stable/production` 与 `experimental/test` POST → 200、GET `/api/settings` 读回一致；
- 切换通道不重启服务（`startedAt` 不变）；
- `{channel}-{env}` 模板：切换后 manifest 从 stable/production 变为 experimental/test，
  `latest` 与 `notes` 同步变化；
- 目录 URL：自动选择 `update-stable-production.json` / `update-experimental-test.json`；
- UI 下拉切换立即持久化，可切回稳定通道。

### 限制与风险

- 通道只影响 manifest 选择；热更新（92）仍 501，不会因为切换通道而下载或安装任何东西；
- manifest 仍限制为本地文件或 loopback http(s)，实验通道也不能指向外网；
- 没有实现原版 `isVisibleReleaseEnv` 的“test 环境默认隐藏”逻辑：本复刻版把 env 作为显式可配置项，
  默认 `production`，UI 同时提供 production/test 两个选项。

---

## 95 云加密

### 现状

`completion-audit.csv` 第 95 行「未实现」。本轮标记为 `unavailable`：依赖云端会员 RSA 公钥
体系，没有云端账号/公钥/私钥，本地无法验证，因此**不注册路由、不提供 UI 入口**；
`cloud.encryption` 仅作配置占位，默认 `false` 且无代码读取。

### 预留落点

| 类型 | 位置 |
|---|---|
| 注册表 | `backend/capabilities.py` `cloud.encryption`（L88） |
| 路由 | 无（探测 `/api/cloud/encryption` 应 404） |
| 配置键 | `cloud.encryption`（`backend/store.py` L99；`frontend/src/lib/store.js` L88） |
| UI | 无入口（设置里只显示 90/91/92/93/98/99，云加密不渲染） |

### 下一轮实现要点

原版证据：

- **L29**：「云同步用会员 RSA 公钥分块加密资产（117 字节/块）」。
- **L217**：「`getEncryptedList()` 用 `member.publicKey` 对每条资产 JSON 做 RSA PKCS#1 v1.5
  分块加密（每块 117 字节，符合 RSA-1024 明文上限），超过 100 KB 的资产与超过 200 KB 的结果
  会被跳过；上传到 `client/member-asset/sync`，并通过 `client/member-asset/diff` 做差异。」
- 功能矩阵 **L96**（oid 95）：`安全 / 云加密 / 会员 RSA 公钥分块加密`。

若未来要做，前置条件是**先有云同步**（当前刻意不做云端）；否则只应保持 unavailable。
纯本地可做的替代是“资产导出加密包”（对称加密 + 口令），但那不是原版云加密，不应冒充。

### 验收标准

- `/api/capabilities` 中 `status == "unavailable"` 且 `entry` 为空。
- 不注册任何 `/api/cloud/*` 路由；设置界面不出现“云加密”入口。
- 不引入云端依赖、不上传任何资产。

### 风险

- 把“本地导出加密”当成“云加密”属于虚报，必须避免。
- 一旦引入 RSA 分块加密，兼容性（块大小、填充、Base64）容易与原版不一致，需先用产物验证。

---

## 98 MCP

### 现状

`completion-audit.csv` 第 98 行「未实现」，备注「原产品也未发现」。本轮为 `reserved`：
`GET /api/mcp/info` 返回 501，只声明位置，不启动 MCP 客户端/服务端。

### 预留落点

| 类型 | 位置 |
|---|---|
| 注册表 | `backend/capabilities.py` `ext.mcp`（L100） |
| 路由 | `GET /api/mcp/info` → 501（`backend/app.py` L3211） |
| 配置键 | `mcp.enabled` / `mcp.transport`（`backend/store.py` L98；`frontend/src/lib/store.js` L87） |
| UI | 设置 → 扩展节「MCP」行（禁用） |

### 下一轮实现要点

原版证据：

- 《拆解报告》**L544**：「MCP | ❌ | 未发现 MCP 客户端/服务端实现」。
- 功能矩阵 **L99**（oid 98）：`未知 / MCP / 未发现 MCP 客户端/服务端`。

也就是说，这不是“复刻原版功能”，而是“新增能力”。实现建议：先确定角色（作为 MCP server
暴露工具，还是作为 client 连接外部 server），优先做只读工具（列资产、读文件、查询）；
`mcp.transport` 仅支持 stdio（本地），要 `enabled=true` 才启动；所有工具走现有 AI 审批策略。
**本轮不加 `mcp` 依赖**，requirements.txt 零变化。

### 验收标准

- `mcp.enabled=false` 时进程不监听任何额外端口、不拉起子进程。
- `GET /api/mcp/info` 在实现前保持 501；实现后返回协议版本与工具清单。
- 工具调用受既有审批/白名单约束，不能绕过。

### 风险

- MCP 工具面会扩大攻击面（文件/命令/数据库），必须默认关闭并逐工具授权。
- 原版没有该能力，文档必须写“新增”，不能计入复刻计数。

---

## 99 插件市场

### 现状

`completion-audit.csv` 第 99 行「未实现」，备注「原产品为编译期内置」。本轮为 `reserved`：
`GET /api/plugins` 返回 501；没有下载、安装、加载任何插件。

### 预留落点

| 类型 | 位置 |
|---|---|
| 注册表 | `backend/capabilities.py` `ext.plugins`（L113） |
| 路由 | `GET /api/plugins` → 501（`backend/app.py` L3212） |
| 配置键 | `plugins.enabled` / `plugins.dir`（`backend/store.py` L97；`frontend/src/lib/store.js` L86） |
| UI | 设置 → 扩展节「插件市场」行（禁用） |

### 下一轮实现要点

原版证据：

- 《拆解报告》**L36**：「插件是编译期内置的 6 个 Go 插件，不是动态 `.so` 插件」。
- **L439-450**（4.10 插件框架）：6 个内置插件 `master / remote-control / db / redis /
  docker / repository`；`registerInProcessPlugins` → `RegisterPluginRoutes` →
  `master.RegisterPluginHandler` / `HasPluginRoute` / `servePluginRequest`；
  **不是** `plugin.Open` 动态加载，也没有外部插件包/市场机制。
- 功能矩阵 **L100**（oid 99）：`未知 / 插件市场 / 编译期内置，无动态插件`。

实现建议：若要做“市场”，先定义插件清单 schema（id/version/entry/hash/permissions）与
沙箱边界；加载方式优先“独立进程 + RPC”，而不是同进程 import；安装目录来自 `plugins.dir`，
校验哈希与签名；`plugins.enabled=false` 时完全不扫描目录。**本轮不加插件 SDK 依赖。**

### 验收标准

- `plugins.enabled=false` 时进程不扫描 `plugins.dir`、不加载任何代码。
- 清单非法/哈希不符时拒绝安装并保留原状态。
- 插件不能越权访问未授权资产；卸载后无残留进程。

### 风险

- 动态插件是最高供应链风险项：必须签名、沙箱、权限声明三件套齐全才可上线。
- 原版本就没有市场，做出来属于新增能力，不得计入复刻计数。

---

## 与计数的关系

| 口径 | 第二十一轮 | 第二十二轮 | 第二十三轮 |
|---|---|---|---|
| 已复刻 | 77 | 78 | **80** |
| 未实现 | 16 | 15 | **13** |
| 形态替代 | 4 | 4 | 4 |
| 改进 | 2 | 2 | 2 |
| 合计行数 | 99 | 99 | 99 |
| 本地口径 | 80/81 | 81/82 | **83/84** |

第二十三轮把 91（版本检查）与 93（更新通道）实现并移出本地排除集：排除集 17→15
（1,2,5,6,12,79-85,92,98,99），本地分母 82→84、分子 81→83；剩余唯一本地缺口仍是 95 云加密。
92 热更新仍是 `reserved` 并继续返回 501；95/98/99 与 90 系统托盘没有变化，
**托盘仍是 `unavailable`，不计入分子**。
