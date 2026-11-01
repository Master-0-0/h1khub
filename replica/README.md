# H1KHub — 一体化本机工作台

H1KHub 是一个本地优先的一体化工作台：**数据库 + SSH/SFTP + Redis + Docker + AI Agent**，真实可用，不是界面原型。数据库支持 MySQL/MariaDB、PostgreSQL、SQLite、DuckDB、ClickHouse、MongoDB、Oracle 19c、达梦 DM8，并支持 trzsz 文件传输与本地 OCI registry 拉取。

- 形态：本机 HTTP 服务 + 内嵌 Vue 3 单页应用（浏览器打开）
- 后端：Python 3.11+ / aiohttp / paramiko / PyMySQL / psycopg2 / redis-py
- 前端：Vue 3 / Vite / xterm.js / CodeMirror 6
- 版本标识：`5.1.9`（沿用拆解对象的功能基线，便于逐条对照）
- 数据目录：`~/.h1khub`（`data.db` + `secret.key` + `known_hosts` + `runtime.json`）；
  升级自旧版时，若 `~/.h1khub` 不存在而 `~/.hexhub-replica` 存在，首次启动会自动迁移

---

## 一、界面

| 首页 | SSH 终端 + SFTP |
|---|---|
| ![首页](docs/screenshots/01-home.png) | ![SSH](docs/screenshots/02-ssh-sftp.png) |

| MySQL 表数据 | SQL 编辑器 |
|---|---|
| ![表数据](docs/screenshots/03-mysql-table.png) | ![SQL](docs/screenshots/04-sql-editor.png) |

| Redis | Docker |
|---|---|
| ![Redis](docs/screenshots/05-redis.png) | ![Docker](docs/screenshots/06-docker.png) |

| 批量执行 | AI Agent（工具调用 + 审批） |
|---|---|
| ![批量](docs/screenshots/07-batch-exec.png) | ![AI](docs/screenshots/13-ai-agent.png) |

| 设置 | 亮色主题 |
|---|---|
| ![设置](docs/screenshots/08-settings.png) | ![亮色](docs/screenshots/10-light-theme.png) |

本轮补齐的能力：

| 表结构设计 | SQL 导入 |
|---|---|
| ![结构](docs/screenshots/14-db-schema.png) | ![导入](docs/screenshots/15-db-import.png) |

| 数据库实时信息 | 明文 FTP |
|---|---|
| ![实时](docs/screenshots/16-db-realtime.png) | ![FTP](docs/screenshots/17-ftp.png) |

| 独立 SSH 隧道 | 容器文件 |
|---|---|
| ![隧道](docs/screenshots/18-ssh-tunnel.png) | ![容器文件](docs/screenshots/19-docker-files.png) |

| Redis 批量操作 | 13 家 AI 供应商 |
|---|---|
| ![Redis 批量](docs/screenshots/20-redis-batch.png) | ![供应商](docs/screenshots/21-ai-providers.png) |

| 表结构/数据同步 | 命令广播 |
|---|---|
| ![同步](docs/screenshots/23-table-sync.png) | ![广播](docs/screenshots/24-broadcast.png) |

| AI 终端/SQL 助手 | 后台任务 |
|---|---|
| ![助手](docs/screenshots/25-ai-assistants.png) | ![任务](docs/screenshots/26-tasks.png) |

| MongoDB 集合 | ClickHouse 表 |
|---|---|
| ![MongoDB](docs/screenshots/28-mongodb.png) | ![ClickHouse](docs/screenshots/29-clickhouse.png) |

| 本地 OCI 仓库（发布/拉取） | trzsz 下载入口 |
|---|---|
| ![本地仓库](docs/screenshots/30-local-registry.png) | ![trzsz](docs/screenshots/31-trzsz.png) |

| 分屏视图（4 面板） | 多窗口（独立浏览器窗口） |
|---|---|
| ![分屏](docs/screenshots/32-split-view.png) | ![多窗口](docs/screenshots/33-multi-window.png) |

| 锁屏（本地口令门） | 设置·安全（锁屏可配置） |
|---|---|
| ![锁屏](docs/screenshots/34-lock-screen.png) | ![安全](docs/screenshots/35-settings-security.png) |

| 更新设置（版本检查 / 通道） | 关于页检查更新 |
|---|---|
| ![更新](docs/screenshots/36-settings-update.png) | ![关于](docs/screenshots/37-about-update-check.png) |

| 资产地图（拓扑总览） | 资产地图（2 跳链详情） |
|---|---|
| ![地图](docs/screenshots/asset-map.png) | ![链](docs/screenshots/asset-map-chain.png) |

| Chrome 服务器代理（管理对话框） | SSH 资产右键入口（运行中可直接停止） |
|---|---|
| ![Chrome 代理](docs/screenshots/40-chrome-proxy.png) | ![菜单](docs/screenshots/42-chrome-proxy-stop.png) |

| 真实 Chrome 经 3 跳 SSH 代理加载页面 | |
|---|---|
| ![多跳](docs/screenshots/43-chrome-proxy-multihop.png) | |


---

## 二、快速开始

```bash
cd replica

# 1) Python 依赖（系统已有可跳过）
python3 -m pip install -r requirements.txt

# 2) 构建前端（已提交 dist 时可跳过；start.sh 会自动构建）
./scripts/build_frontend.sh

# 3) 启动（默认 127.0.0.1:38110，自动打开浏览器）
./scripts/start.sh

# 可选：拉起演示依赖（RedisJSON 6385 / MongoDB 27017 / 检查 ClickHouse 9000）
./scripts/start_lab_services.sh

# 可选：SQL Server 演示实例（容器 mssql2019，127.0.0.1:14330，hexhub_demo）
# 需要先有 hexhub-mssql:2019 镜像，见脚本头部说明；2022 镜像在本机起不来
./scripts/start_mssql.sh

# 可选：写入演示资产（使用下方实验环境的地址与口令）
python3 scripts/seed_demo.py --reset-settings

# 可选：安装应用菜单快捷方式（~/.local/share/applications）
./scripts/install_desktop.sh
```

常用参数：

```bash
./scripts/start.sh --port 38110 --home ~/.h1khub --no-open
./scripts/start.sh --host 127.0.0.1 --token <fixed-token>
```

启动后：

- 页面：`http://127.0.0.1:38110/`
- 公开端点：`GET /api/public/info`
- 运行信息：`~/.h1khub/runtime.json`（含端口、PID、会话令牌，权限 0600）

演示资产默认指向开发用的本机实验环境（可在界面中修改或删除）：

| 服务 | 地址 | 账号 |
|---|---|---|
| SSH | `127.0.0.1:22` | `hexlab / Hexlab@123` |
| MariaDB | `127.0.0.1:3307` | `hexhub / hexhub123`，库 `demo` |
| PostgreSQL | `127.0.0.1:5433` | `hexhub / hexhub123`，库 `demo` |
| ClickHouse | `127.0.0.1:9000` | `default / 空口令`，库 `demo` |
| MongoDB | `127.0.0.1:27017` | 无认证，库 `demo` |
| Redis | `127.0.0.1:6380` | 无口令 |
| RedisJSON | `127.0.0.1:6385` | 无口令（`h1khub_json` 模块） |
| SQLite | `/home/kali/hexhub-lab/demo.db` | — |
| Docker | `/var/run/docker.sock` | 当前用户需有 socket 权限 |
| 本地 OCI registry | `127.0.0.1:5050` | 在 Docker 视图「镜像 → 本地仓库」启动 |
| 达梦 DM8 | `127.0.0.1:52360` | `HEXHUB / Hexhub@12345`（容器 `hexhub-dm8`） |
| Oracle | `ecourse.org:1521/ol8.ecourse.org` | `scott / tiger`（公共共享实例，只读为主） |
| SQL Server | `127.0.0.1:14330` | `sa / Hexhub@12345`，库 `hexhub_demo`（容器 `mssql2019`） |

---

## 三、架构

```
┌───────────────────────────── 浏览器（同一台机器） ─────────────────────────────┐
│  http://127.0.0.1:38110/  →  Vue 3 SPA（Vite 构建，内嵌于后端静态目录）        │
│    ├─ 资产树 / 标签页 / 终端(xterm) / 数据网格 / SQL(CodeMirror) / AI 对话     │
│    ├─ HTTP：/api/*（X-H1KHub-Token 会话令牌）                                  │
│    └─ WebSocket：/rpc?token=…（通道式双向 RPC，JSON 帧）                       │
└───────────────────────────────────────────────────────────────────────────────┘
                                      │ 127.0.0.1 only
┌─────────────────────────────── h1khub-backend (Python) ───────────────────────┐
│ aiohttp HTTP + WebSocket                                                      │
│  ├─ store.py     SQLite 资产库 + AES-256-GCM 配置加密 + 设置/历史/会话         │
│  ├─ hub.py       资产解析（私钥/跳板链/隧道）、连接工厂、SSH 远端执行           │
│  ├─ sessions.py  本地 PTY / SSH PTY / Telnet / Docker exec 会话               │
│  ├─ sshlib.py    paramiko 认证、known_hosts TOFU、代理、跳板链、端口转发        │
│  ├─ fsops.py     本地文件 + SFTP 浏览/上传/下载/编辑/权限                      │
│  ├─ archive_svc.py 本地/远端压缩与解压（tar/zip/7z、越界校验）                 │
│  ├─ ftp_svc.py   明文 FTP / FTPS 浏览与传输                                    │
│  ├─ scp_svc.py   SCP 上传/下载（远端 scp -t/-f 协议）                          │
│  ├─ tunnel_svc.py 独立 SSH 隧道：local / remote / SOCKS5                       │
│  ├─ chrome_proxy_svc.py SSH 资产 → 本地 Chrome 的 SOCKS5 代理启动/回收         │
│  ├─ db.py        MySQL/MariaDB、PostgreSQL、SQLite 适配器（含事务原语）        │
│  ├─ dbsessions.py 会话级事务、表结构编辑、.sql 导入、批量行、Blob、实时信息     │
│  ├─ dbsync.py    表结构差异/同步、数据 append/replace                          │
│  ├─ transfer_svc.py 跨服务器 SFTP 传输（文件/目录）                            │
│  ├─ key_svc.py   密钥生成、ssh-copy-id（公钥追加）                              │
│  ├─ redis_svc.py Redis 键空间、类型化读写、批量操作、控制台、实时信息          │
│  ├─ docker_svc.py Docker CLI（本地 socket / SSH / TCP）、Compose、容器文件     │
│  ├─ ai.py        OpenAI 兼容 / Anthropic 流式对话 + 33 个工具 + 审批            │
│  └─ app.py       路由、鉴权、RPC、静态资源                                     │
└───────────────────────────────────────────────────────────────────────────────┘
```

设计取舍：原产品是 **CEF 外壳 + Go 后端 + protobuf 通道 RPC + IndexedDB**；复刻版保持"本地服务 + 嵌入式 SPA + 通道 RPC + 同一份资产树"的形态，但用浏览器承载窗口、Python 实现后端、二进制 Packet/protobuf 承载 RPC 帧（见第八节差异表）。这样无需 400 MB 的 Chromium 运行时即可运行，同时保留原有的交互模型与数据模型。

---

## 四、功能矩阵

图例：✅ 真实实现并验证；◐ 部分实现；✗ 未实现。

### 4.1 资产与工作区

| 能力 | 状态 | 说明 |
|---|:--:|---|
| 21 种资产类型枚举 | ✅ | 数值与拆解报告一致（0=Dir … 21=LLMProviderConfig），前端按类型渲染图标/编辑器 |
| 资产树 | ✅ | 目录、嵌套、展开、搜索（含别名）、右键菜单、颜色、收藏、复制、删除、显示别名、拖拽排序 |
| 资产 CRUD / 移动 | ✅ | 编辑器中可改父目录；删除递归 |
| 资产导入/导出 | ✅ | JSON 资产包，含版本号，支持合并导入 |
| 全局搜索 | ✅ | `Ctrl+K`，资产 / 命令 / 标签页，键盘导航 |
| 最近历史 | ✅ | 记录打开的资产；`Ctrl+E` 打开历史面板 |
| 多标签工作区 | ✅ | 终端 / 数据库 / Redis / Docker / AI，标签可关闭、切换、全部关闭 |
| 资产地图 | ✅ | 连接类资产拓扑：跳板链与 DB/Redis/Docker/隧道 SSH 依赖、分层布局、缩放平移、搜索/筛选、选中详情、循环提示；`test_assetmap.py` PASS=35 |
| 批量执行（Pro 功能） | ✅ | 多台 SSH 主机并行执行命令，逐台展示 stdout/stderr/耗时 |
| 后台任务面板 | ✅ | 统一任务队列；SQL 导入/跨服传输/Docker 拉取可后台执行，可轮询/取消 |
| 命令广播 | ✅ | 勾选多个终端会话同时注入命令，逐会话返回结果 |
| 多窗口 / 分屏 | ✅ | 垂直/水平分屏（最多 4 面板）、拖动分隔条调比例、`Ctrl+←/→/↑/↓` 切焦点、标签右键「在新窗口打开 / 同屏打开」；`test_split.py` PASS=45（第二十轮） |
| 云账号 / 会员 / 支付 / 云同步 | ✗ | 刻意不实现（本地模式，无云端依赖） |

### 4.2 终端与远程

| 能力 | 状态 | 说明 |
|---|:--:|---|
| 本地 PTY 终端 | ✅ | Shell 路径、工作目录、环境变量；窗口尺寸同步 |
| SSH 密码 / 私钥 / 口令 | ✅ | 私钥可直接粘贴或引用"私钥资产" |
| SSH Agent | ✅ | 支持每个资产指定 `agentSocket`；已用临时 ssh-agent 做端到端连接与命令执行验证 |
| 跳板机 | ✅ | 递归 `jumpAssetId`，最多 8 个跳板（目标不计入）；`client -> Jn -> … -> J1 -> target` 由外到内逐跳认证与 known_hosts TOFU；单层/多层、循环、超深、失败清理均有端到端测试（`scripts/test_jumps.py`） |
| 代理 | ✅ | SOCKS5 / HTTP CONNECT，支持用户名口令；已用本机代理 fixture 端到端验证 |
| 通过服务器代理 Chrome（原 Pro） | ✅ | 资产列表右键 SSH 资产，启动本机 Chrome/Chromium，流量经该服务器的临时 SOCKS5 隧道；独立临时配置，浏览器退出或手动停止后自动回收；**3 跳 SSH 链 + 真实 Chromium 实测**；`test_chromeproxy.py` PASS=75、`test_chromeproxy_jumps.py` PASS=23 |
| 主机密钥确认 | ✅ | known_hosts TOFU；指纹变化直接拒绝连接 |
| 终端增强 | ✅ | xterm.js：搜索、链接、选中复制、字号/光标设置、5000 行回滚 |
| 命令广播 / 分屏 | ✅ | 命令广播：多会话同时注入并逐台返回；分屏：垂直/水平 ≤4 面板、拖动比例、`Ctrl+方向键` 切焦点（见 4.1） |
| 生成私钥 / 复制公钥 | ✅ | ed25519/RSA 生成、显示公钥、ssh-copy-id（幂等），已端到端验证 |
| 共享密码对 | ✅ | SSHPasswordPair 资产，SSH 连接可引用，避免重复填写 |
| Telnet | ✅ | IAC 协商、NAWS 尺寸、终端输入输出；已用本机 telnet fixture 端到端验证 |
| 串口 | ✅ | pyserial 会话（波特率/数据位/校验/停止位）；socat PTY 对双向验证，真实硬件未插 |
| RDP | — | Linux 版原产品即不支持，属平台限定（形态替代） |
| VNC | ✅ | 资产字段、专用视图、端口探测；与原版一致不内置 RFB 画面（`scripts/test_vnc.py`） |
| X11 转发 | ✅ | paramiko `request_x11` + Xauthority cookie + 本地 X 代理；`xdpyinfo` 经转发验证（`scripts/test_x11.py`） |

### 4.3 文件

| 能力 | 状态 | 说明 |
|---|:--:|---|
| SFTP 浏览 | ✅ | 远端目录、隐藏文件、排序、路径跳转、主目录 |
| 上传 / 下载 | ✅ | 多文件、拖拽上传、浏览器下载，流式传输 |
| 在线编辑 | ✅ | 文本检测、UTF-8/Latin-1 回退、保存 |
| 新建目录/重命名/删除/权限 | ✅ | 递归删除、chmod |
| 压缩 / 解压 | ✅ | 文件面板多选（Ctrl/Shift）或右键：tar.gz / tar.bz2 / tar.xz / tar / zip / 7z；远端直接在 SSH 主机调用 tar/zip/7z，本地用 Python 标准库；压缩前校验同目录选择，解压前扫描成员拒绝绝对路径与 `..` 越界；支持覆盖/跳过、新建目标目录（`scripts/test_archive.py` PASS=135、`scripts/test_archive_ui.py` PASS=27） |
| 本地文件浏览 | ✅ | 本地/远程双栏、目录收藏、路径同步；跨栏直接传输仍走上传/下载 |
| SCP 上传 / 下载 | ✅ | 远端 `scp -t/-f` 协议，支持错误提示；已端到端验证 |
| 明文 FTP | ✅ | 连接、列目录、上传/下载、重命名、删除、在线编辑；已端到端验证 |
| ZModem（rz/sz） | ✅ | 终端内 `zmodem.js`：`sz` 下载自动保存、`rz` 选文件回传（`scripts/test_zmodem.py`） |
| trzsz（trz/tsz） | ✅ | 后端隧道协议对接远端 trz/tsz，终端工具栏上传/下载（`scripts/test_trzsz.py`） |
| 断点续传 | ✗ | 未单独实现；超过 2 GB 上传会明确提示 |
| 跨服务器传输 | ✅ | SFTP 服务器之间直接中转，支持目录递归、覆盖/跳过 |

### 4.4 数据库

| 能力 | 状态 | 说明 |
|---|:--:|---|
| MySQL / MariaDB | ✅ | 连接、库/表/视图/例程、分页、排序、筛选、增删改、DDL、导出、Explain |
| PostgreSQL | ✅ | 同上，支持 schema、物化视图列表、基于 information_schema 生成 DDL |
| SQLite | ✅ | 文件库、表/视图、数据读写、PRAGMA、DDL |
| SQL 编辑器 | ✅ | CodeMirror 6、方言高亮、Ctrl+Enter、选中执行、多结果集、Explain、保存为查询资产 |
| 行内编辑 | ✅ | 需要主键；支持 NULL、插入行、删除行 |
| 导出 | ✅ | CSV / JSON / SQL INSERT / XLSX / TXT（10k 行上限） |
| 表结构编辑 | ✅ | 字段增改删、类型/默认值/注释、索引、外键、建表/删表；MySQL/PG 实测，SQLite 限制项报错 |
| 视图/例程编辑 | ✅ | 视图与例程定义查看/保存（重建）/删除；MySQL/PG 视图、MySQL 过程实测 |
| 结构同步 / 数据同步 | ✅ | 按表比较并应用 ALTER/CREATE；数据按同名列 append/replace（MySQL/PG 实测） |
| 事务按钮 | ✅ | 会话级连接 + BEGIN/COMMIT/ROLLBACK + autocommit 开关（MySQL/PG/SQLite），已实测 |
| 导入 | ✅ | `.sql` 脚本：事务、遇错停止、逐语句结果；XLSX 导入后置 |
| 实时信息 | ✅ | MySQL 状态/连接/命中率、PG 事务/缓存、SQLite 页统计 + 进程列表 |
| DuckDB | ✅ | 嵌入式文件库：对象树/表数据/查询/Explain/DDL/事务/导出/ATTACH（含 sqlite 跨库），`scripts/test_duckdb.py` |
| ClickHouse | ✅ | 原生 TCP（clickhouse-driver）：库/表树、查询、Explain、DDL、INSERT、ALTER UPDATE/DELETE、实时指标、导出，`scripts/test_clickhouse.py` |
| MongoDB | ✅ | 文档集合：库/集合树、文档表、shell/JSON 查询、aggregate、增删改/drop、Explain、实时指标、导出，`scripts/test_mongodb.py` |
| Oracle 19c | ✅ | thin 模式（python-oracledb）：服务→当前 schema 对象树、表数据、DBMS_XPLAN、DDL、元数据、增删改、CSV 导出；**Oracle 19c 真实实例已验证**（`ecourse.org:1521/ol8.ecourse.org`，SCOTT，EMP 14 行 / DEPT 4 行，`scripts/test_oracle.py` PASS=50） |
| 达梦 DM8 | ✅ | dmPython：用户架构对象树、`EXPLAIN FOR`、DDL、元数据、增删改、导出；真实实例 `127.0.0.1:52360`（`scripts/test_dameng.py` PASS=29） |
| SQL Server | ✅ | pymssql/TDS：库→dbo 架构对象树、表数据、查询、`SHOWPLAN_XML` 执行计划、DDL、表元数据、增删改、实时指标、进程列表、CSV 导出；**SQL Server 2019 真实实例已验证**（`127.0.0.1:14330`，`Microsoft SQL Server 2019 RTM-CU32-GDR 15.0.4500.2` Developer Edition，`hexhub_demo` users/orders，`scripts/test_sqlserver.py` PASS=32） |
| 其他数据库 | ◐ | Kingbase / OpenGauss 走 PostgreSQL 方言，未单独实测 |
| SSH 隧道 / 跳板 | ✅ | 数据库连接可引用 SSH 资产，自动建立本地端口转发 |

### 4.5 Redis

| 能力 | 状态 | 说明 |
|---|:--:|---|
| 连接 / DB 列表 | ✅ | 口令、TLS、SSH 隧道 |
| SCAN / 键列表 | ✅ | 游标分页、pattern、类型与 TTL |
| 类型读写 | ✅ | String / Hash / List / Set / ZSet / Stream |
| 键管理 | ✅ | 新建、重命名、删除、TTL、批量扫描 + 批量删除/重命名/设置值/设置TTL |
| 控制台 | ✅ | 任意命令（shlex 解析） |
| 实时信息 | ✅ | 内存、客户端、命中率、键空间 |
| 批量操作 | ✅ | 按 glob 预览后批量删除/重命名/设置值/设置 TTL，已实测 |
| 集群 | ✅ | 3 节点集群连接、跨节点 SCAN、跨槽批量重命名（克隆+删除）、批量操作、INFO/实时 ✅ |
| RedisJSON | ✅ | `h1khub_json` 模块（ReJSON-RL）+ JSON 编辑器（根路径）；`scripts/test_redisjson.py` |

### 4.6 Docker

| 能力 | 状态 | 说明 |
|---|:--:|---|
| 连接 | ✅ | 本地 Unix socket、SSH 远端、TCP 远端（三种均已实测） |
| 容器 | ✅ | 列表、启停、重启、删除、stats + 创建/重命名/pause/unpause/top |
| 日志 / 详情 | ✅ | 最近 800 行、inspect JSON |
| 容器终端 | ✅ | 本地 PTY、SSH 远端、TCP 远端三种 `docker exec -it` 均已实测 |
| 镜像 | ✅ | 列表、删除、拉取、推送、构建、导出/导入 tar、历史、清理 |
| 本地 OCI registry | ✅ | `tools/oci_registry.py` 发布本地镜像并由 Docker 拉取；DockerView「镜像 → 本地仓库」，`scripts/test_local_registry.py` |
| 卷 / 网络 | ✅ | 列表 |
| Compose | ✅ | 读取/写入 compose 文件 + up/down/stop/restart/ps，临时项目端到端实测 |
| 容器文件 | ✅ | 列目录/读取/写入/上传/下载/重命名/删除（exec + tar），已实测 |
| 私有仓库 | ◐ | `docker login --password-stdin` 已实现；无真实仓库，成功路径未验证 |

### 4.7 AI Agent

| 能力 | 状态 | 说明 |
|---|:--:|---|
| 供应商 | ✅ | OpenAI 兼容协议 + Anthropic Messages；内置 13 家预设 + 自定义 |
| 模型目录 / 连通性测试 | ✅ | `/v1/models` 拉取、最小对话测试 |
| 流式对话 | ✅ | SSE 解析、增量渲染、停止、token 统计 |
| 工具 | ✅ | 33 个：资产（列/读/新建/修改/移动/删除 + 密钥读写）、文件、目录、shell、SQL、Redis、Docker 容器/日志/启停、终端、SCP 上下行、隧道启停/列表、FTP 列目录/上传/下载/删除/重命名、网页抓取、联网搜索（websearch，需配置端点+审批） |
| 审批策略 | ✅ | 默认静默读/只读 SQL；写文件、命令、写 SQL 需审批；危险命令始终审批；可"本会话记住" |
| 会话持久化 | ✅ | SQLite 中的 chat/messages，界面可新建、切换、删除 |
| 专用助手 | ✅ | 通用 / Terminal 2.0（终端上下文+终端工具）/ SQL（数据库与 SQL 上下文+SQL 工具） |
| 写入 diff / 回滚 | ✅ | 写文件前展示 unified diff；一键恢复到写入前版本（本地/SSH） |
| 图片输入 / MCP | ✗ | 统一为一个通用 Agent；工具已覆盖终端与 SQL 场景 |

### 4.8 设置与体验

| 能力 | 状态 | 说明 |
|---|:--:|---|
| 主题 | ✅ | 暗色 / 亮色 / Nord / Dracula / Solarized / GitHub / 跟随系统；4 套强调色 |
| 语言 | ✅ | 简体中文 / English（界面内即时切换） |
| 终端 / 编辑器设置 | ✅ | 字号/字体/光标/回滚/右键/自动日志；编辑器字号/Tab/换行/自动保存 |
| 快捷键 | ✅ | 可配置：搜索/设置/本地终端/关标签/历史/面板/分屏（垂直·水平）/分屏焦点（←→↑↓）；`Alt+1…9` 保留 |
| 锁屏 / 托盘 | ◐ | 锁屏已实现：本地口令门（PBKDF2 哈希 + 随机 salt、失败退避、立即/空闲锁定、刷新/新标签仍锁）；系统托盘不可用（浏览器形态无 `enableTray`/`setTrayMenu`） |
| 热更新 / 插件市场 | ✗ | 原产品为编译期内置插件；复刻版无插件机制 |
| 遥测 | ✗ | 无任何上报 |

---

## 五、核心设计

### 5.1 本地存储与加密

- `data.db`（0600）：`assets`、`settings`、`history`、`command_history`、`chat_sessions`、`chat_messages`。
- 每条资产的**整个 config** 使用 AES-256-GCM 加密后以 base64 存储；密钥在 `secret.key`（0600），数据目录 0700。
- 读取资产时只返回非敏感字段，并附带 `secrets: ["password", ...]`；敏感值永不回传前端。
- 保存时若敏感字段留空则保留旧值（前端占位提示"已加密保存，留空不修改"），`"__CLEAR__"` 可显式清除。
- 这修复了原产品"仅 SSH 字段走固定密钥 AES、DB/Redis/Docker 凭据可能明文"的问题。

### 5.2 本地 API 鉴权

- 启动时生成 32 字节随机令牌，写入 `runtime.json`（0600）。
- 除 `/api/public/info` 与 `/api/auth/token`（仅回环地址可访问）外，所有 `/api/*` 需要 `X-H1KHub-Token`。
- WebSocket `/rpc?token=…` 同样校验；未授权返回 `401 <h1>session invalid</h1>`，与拆解报告中的行为一致。
- 服务仅监听 `127.0.0.1`；不要通过反向代理暴露到公网。

### 5.3 通道式 RPC

WebSocket 帧为 JSON：

```json
{"method": "open",  "channel": "term:s_xxx"}
{"method": "event", "channel": "term:s_xxx", "data": {"type": "data", "data": "<base64>"}}
{"method": "send",  "channel": "term:s_xxx", "data": {"type": "input", "data": "<base64>"}}
{"method": "close", "channel": "term:s_xxx"}
```

- 终端通道：`history`（连接时回放最近 512 KB）、`data`、`exit`；输入/尺寸调整走 `send`。
- AI 通道：`start`、`delta`、`reasoning`、`tool_start`、`tool_result`、`approval`、`usage`、`done`、`error`。
- 与拆解报告的 `Packet{id, method, data}` / `ChannelOpen/ChannelSend/ChannelClose` 对应：二进制帧 `u16 methodLen | u32 dataLen | u32 id | method | data`，逐字节 XOR；`/proto/*` 提供拆解所得 schema，SQL 查询回复为 protobuf `TableDataResponse`。

### 5.4 SSH 安全

- 首次连接记录主机指纹到 `~/.h1khub/known_hosts`（非 22 端口写作 `[host]:port`）；同一算法的指纹变化直接拒绝，并指出是链路的第几跳。
- 支持密码、私钥、agent、keyboard-interactive 回退；私钥可引用独立资产，密码可引用“密码对”资产。
- **递归多层跳板**：每个 SSH 资产仍只有一个 `jumpAssetId`，跳板资产自身可继续引用跳板。连接顺序固定为由外到内：`client -> Jn -> … -> J1 -> target`（`target.jumpAssetId=J1`、`J1.jumpAssetId=J2`……）。默认上限 `MAX_JUMP_DEPTH = 8`（`backend/hub.py`，最多 8 个跳板，目标不计入该上限）。
- 每一跳都是一次完整 SSH 认证，并独立做 known_hosts TOFU。任一跳连接/认证/密钥校验失败时，已经打开的外层与内层 transport 会全部关闭，不会泄漏连接或会话。
- 循环引用（A→B→A）与超深链在**链路解析阶段**快速失败，返回 400 并包含完整链路/上限；不会建立任何 socket，也不会挂起。
- 代理边界：只有**最外层**跳板能使用自己的 `proxy`。`connect_transport()` 在 `via` 非空时通过父 transport 的 `direct-tcpip` 建链，不再走 proxy，因此中间跳的 `proxy` 字段会被忽略（有意保留的边界，见“已知限制”第 8 条）。
- 数据库/Redis 资产通过 `sshAssetId` 建立的隧道、Docker SSH 模式、SFTP、SCP、终端会话都复用同一套链路连接；`hub.tunnel()` 也已修正为支持多层跳板。
- 多层链的每一层都是一次完整 SSH 认证，延迟与文件描述符随层数线性增长；8 层是防御性上限，不是性能建议值。
- 数据库/Redis 的 SSH 隧道使用 `direct-tcpip` 打开本地监听端口，仅接受本机连接。

### 5.5 AI 工具与审批

默认策略（可在设置中修改）：

```json
{
  "allowSilentFileRead": true,
  "allowSilentFileWrite": false,
  "allowSilentCommand": false,
  "allowSilentReadonlySql": true,
  "blockLocal": false,
  "maxToolRounds": 12
}
```

- 只读操作直接执行；写文件、shell、写 SQL、写 Redis 命令、Docker 容器启停/重启、FTP 上传/下载/删除/重命名、联网搜索默认会阻塞并弹出审批卡片（Docker、FTP 写操作与联网搜索使用独立审批 kind，不受静默策略影响；联网搜索可在设置中关闭审批）。
- 危险命令（`rm`、`dd`、`mkfs`、`shutdown`、`sudo` 包装、管道执行等）即使开启"静默命令"也必须审批。
- 审批结果可按"本会话记住"缓存；拒绝则工具返回"用户拒绝"。
- 工具输出截断到 100 KB，避免把超大结果塞回模型。

资产 CRUD（7 个工具）使用 6 个独立审批 kind，**始终审批**、不继承静默策略，也不纳入
Terminal / SQL 助手子集：

| 工具 | 审批 kind | 说明 |
|---|---|---|
| `asset_get` | 无（只读） | 返回非密钥配置与密钥字段名；不含密钥值与秘密 |
| `asset_create` | `asset_create` | `config` 中出现密钥字段直接拒绝（`secret_in_config`），密钥须走 `asset_secret_write` |
| `asset_update` | `asset_update` | 只改名称/别名/颜色/非密钥配置；改父目录用 `asset_move`，改类型直接拒绝 |
| `asset_move` | `asset_move` | 目标必须是 `Dir`，禁止自身/成环（`asset_cycle`） |
| `asset_delete` | `asset_delete` | 必须 `confirm=true`；非空目录还须 `recursive=true`，否则在审批前返回 `asset_not_empty` / `confirmation_required` 与受影响资产数量、名称 |
| `asset_secret_read` | `asset_secret_read` | 字段必须在 `SECRET_KEYS` 白名单内；**按本轮策略明文返回**（字段名/是否存在/长度/值），审批卡片同样展示明文预览 |
| `asset_secret_write` | `asset_secret_write` | 只接受白名单字段；空值用 `clear` 清除；明文值随工具事件/审批卡片展示与传输 |

删除是单条 `DELETE ... IN (...)` 事务，天然全有或全无；但**不可撤销**：`export_assets` /
`import_assets` 会重新分配 id 并断开会话/历史引用，且导出会落盘明文秘密，因此不做自动快照。
每次变更都会写 `history`（`asset` / `asset-delete` / `asset-secret-read`），detail 记录操作者 `ai`、
工具名、资产 id、变更字段与数量（密钥只记字段名与字节数，不写值）。

联网搜索（`websearch`）默认关闭，**没有默认端点**；`settings.websearch.enabled=false` 或
`endpoint` 为空时，工具只返回结构化错误（`websearch_disabled` / `websearch_not_configured`，
语义 501），不会发起任何 DNS/HTTP 请求。可在「设置 → Agent → 联网搜索」启用、选择是否每次审批并填写端点，
或调用本机 API：

```http
PUT /api/settings
{"websearch": {"enabled": true, "endpoint": "https://searx.example/search?q={query}&format=json", "timeoutSeconds": 15, "maxResults": 8, "requireApproval": true}}
```

端点约定：GET，返回 `{"results":[{"title","url","snippet"}]}`（也接受裸数组 / `items` / `data`）；
`{query}` 做 URL 编码替换，未包含占位符时自动追加 `q=`。单次响应最多读取 1 MB，默认 15 s 超时
（1–30 s），标题/摘要分别截断到 300/1000 字符；`allowed_domains` / `blocked_domains` 可按域名过滤。
联网搜索默认每次调用都走独立审批 kind `websearch`，不继承静默策略；`requireApproval=false`
可在设置中关闭审批，关闭后不再弹出审批卡片（仍需端点已配置，并继续受超时/大小/域名过滤约束）。
**不内置 DuckDuckGo 或 `api.hexhub.cn` 回退**
（原版 `NewWebSearchTool` 抓 DuckDuckGo HTML，默认云地址仅作为逆向资料记录）。该工具不纳入
Terminal / SQL 助手子集。

### 5.6 资产地图

入口：左侧导航的 Network 图标，或资产面板「更多 → 资产地图」。地图是只读视图，不建立连接。

- **范围**：连接类资产（SSH / DB / Redis / Docker / SSHTunnel / FTP / Telnet / VNC）。目录、快捷命令、凭据资产仍留在资产树，避免图被非连接节点淹没。
- **接口**：`GET /api/asset-map` 返回节点、边、循环与统计。节点含类型、主机、目录、入/出度、跳板链、深度、循环/缺失引用标记；边分 `jump`（目标 SSH → 跳板 SSH）与 `ssh`（DB/Redis/Docker/隧道 → SSH 主机）。
- **方向与分层**：边从“依赖方”指向“被依赖方”，因此层号向外递增：服务 → 目标 SSH → J1 → J2。前端按层从左到右排列，并对每层做父节点重心排序；未连线资产单独放在下方网格，循环节点放在尾部列。
- **交互**：滚轮缩放、拖拽平移、适应窗口、名称/主机搜索、按类型筛选、「仅拓扑 / 含孤立」切换；点击节点高亮整个连通分量并在右侧显示详情，详情中的跳板链按由外到内排列且可点击跳转；双击打开资产，右键可打开/编辑/居中/复制。
- **循环与缺失引用**：循环边红色虚线、节点红色标记并在地图左上角列出完整循环；指向不存在资产的 `jumpAssetId/sshAssetId` 会记录在节点 `missing` 中而不生成假节点。
- **数据更新**：资产列表刷新后约 300ms 自动重载地图；也可点击工具栏刷新。
- 实现为自绘 SVG（无第三方图库），布局/缩放/详情都在前端完成，后端只提供拓扑数据。

### 5.7 通过服务器代理 Chrome（第二十七轮）

入口：资产列表右键 **SSH** 资产 →「通过服务器代理 Chrome」；资产面板「更多 → Chrome 代理（查看/停止）…」打开管理对话框。

这是原产品 Pro 能力 `startTunnelForWebBrowser` / `executil.OpenChrome` 的复刻：后端在
`127.0.0.1` 上为该 SSH 资产（含跳板链）建立临时 SOCKS5 监听，再启动本机 Chrome/Chromium 并把
浏览器代理指向它；浏览器退出或点击停止时，隧道和临时配置一起回收。

- **Chrome 发现**：沿用原版的 Linux 候选路径（`/usr/bin/google-chrome`、`/usr/bin/google-chrome-stable`、
  `/usr/bin/chromium`、`/usr/bin/chromium-browser`、`/snap/bin/chromium`），再回退到 `PATH`；
  macOS/Windows 有对应默认路径。`H1KHUB_CHROME_PATH` 可强制指定；找不到时返回尝试过的完整路径列表。
- **启动参数**（与原版一致）：`--proxy-server=socks5://127.0.0.1:<port>`、`--user-data-dir=<临时目录>`、
  `--no-first-run`、`--no-default-browser-check`、`--proxy-bypass-list=<-loopback>`；可选 URL 作为最后一个参数
  （仅接受 `http/https`，长度 ≤2048）。
- **配置隔离**：每次启动用 `tempfile.mkdtemp()` 创建 0700 目录，退出后删除；不碰用户日常 Chrome 配置，
  也不需要用户先退出正在运行的 Chrome。
- **生命周期**：后端等待 Chrome 退出（0.5s 收尾）后关闭 SSH transport、SOCKS 监听并删除配置目录；
  对话框可随时「停止」；后端退出时 `stop_all()` 兜底。最多同时 8 个实例。
- **关闭方式**：再次右键该 SSH 资产 →「停止通过服务器代理的 Chrome」；或「更多 → Chrome 代理（查看/停止）…」→ 停止；或直接关闭 Chrome 窗口（进程退出后自动回收隧道与临时配置）。
- **多跳**：代理链路复用资产自身的 `jumpAssetId` 递归链（`hub.open_ssh_transport`），与普通 SSH 完全一致。已用 **3 跳（4 台 sshd）** 实测：Chrome 页面请求经 SOCKS5 到达目标侧 fixture，每一跳 sshd 日志都新增一次认证连接，浏览器窗口见截图 `43-chrome-proxy-multihop.png`（`test_chromeproxy_jumps.py` PASS=23）。
- **限制**：Chrome 窗口在**后端所在机器**上打开；复刻版是浏览器形态，无法把窗口推到另一台机器。
  后端主机需要图形会话（`DISPLAY`/`WAYLAND_DISPLAY`），无图形环境会启动后立即退出并返回明确 400。
  `--proxy-bypass-list=<-loopback>` 与原版一致，因此该 Chrome 访问 `127.0.0.1` 也会走代理
  （H1KHub 本身请用日常浏览器访问）。
- **接口**：`GET /api/chrome-proxy`、`POST /api/chrome-proxy/start` `{assetId,url?}`、
  `POST /api/chrome-proxy/{sessionId}/stop`。
- **测试**：`scripts/test_chromeproxy.py` PASS=75 FAIL=0（假 Chrome 记录参数 + 真实 sshd +
  SOCKS5 实际 HTTP 转发 + CDP 右键菜单/管理对话框；另起实例验证未安装 Chrome 的 400 与启动即退出的错误路径）；
  `scripts/test_chromeproxy_jumps.py` PASS=23 FAIL=0（真实 Chromium + 3 跳 SSH 链 + 页面加载 + 每跳认证日志 + 窗口截图）。

---

## 六、目录结构

```
replica/
├── backend/
│   ├── app.py            # aiohttp 路由 / 鉴权 / RPC / 静态资源
│   ├── store.py          # SQLite + AES-256-GCM 资产库
│   ├── hub.py            # 资产解析、SSH 隧道、连接工厂
│   ├── assetmap_svc.py   # 资产地图只读拓扑（节点/边/循环/分层）
│   ├── sshlib.py         # paramiko 认证、known_hosts、代理、跳板、转发
│   ├── sessions.py       # PTY / SSH / Telnet / Docker exec 会话
│   ├── fsops.py          # 本地与 SFTP 文件操作
│   ├── tunnel_svc.py     # 独立 SSH 隧道：local / remote / SOCKS5
│   ├── chrome_proxy_svc.py # SSH 资产 → 本机 Chrome 的 SOCKS5 代理
│   ├── archive_svc.py    # 本地/远端压缩与解压（tar/zip/7z、越界校验）
│   ├── db.py             # MySQL / PostgreSQL / SQLite 适配器
│   ├── redis_svc.py      # Redis 服务
│   ├── docker_svc.py     # Docker 服务
│   ├── ai.py             # 模型流式调用 + 工具 + 审批
│   └── util.py
├── frontend/
│   ├── src/App.vue       # 壳层
│   ├── src/components/   # 标题栏、资产树、对话框、编辑器、文件面板…
│   ├── src/views/        # Terminal / Db / Redis / Docker / Ai / AssetMap
│   ├── src/lib/          # API、RPC、状态、格式化、UI 工具
│   ├── src/styles/       # 设计令牌与样式
│   └── dist/             # 构建产物（由后端直接提供）
├── scripts/
│   ├── start.sh          # 依赖检查 + 构建 + 启动
│   ├── build_frontend.sh
│   ├── seed_demo.py      # 演示资产
│   ├── cdp_check.py      # 无头浏览器检查工具
│   ├── ui_flow.py        # UI 冒烟脚本
│   ├── test_assetmap.py  # 资产地图 API + UI 套件
│   ├── test_archive.py   # 压缩/解压 API 套件（SFTP + 本地）
│   ├── test_chromeproxy.py # Chrome 服务器代理 API + UI 套件（真实 sshd/SOCKS5）
│   ├── test_chromeproxy_jumps.py # 真实 Chrome + 3 跳 SSH 代理端到端
│   ├── test_archive_ui.py # 压缩/解压 UI 套件（真实 SSH + CDP）
│   ├── capture_docs.py   # 文档截图
│   └── mock_llm.py       # 测试用 OpenAI 兼容 mock
├── docs/
│   ├── screenshots/
│   ├── verification.md
│   └── feature-matrix.csv
├── requirements.txt
└── README.md
```

---

## 七、API 速查

所有 `/api/*` 需要 `X-H1KHub-Token`（除 public/token）。

| 分组 | 端点 |
|---|---|
| 公共 | `GET /api/public/info`、`POST /api/auth/token` |
| 资产 | `GET/POST /api/assets`、`PUT/DELETE /api/assets/{id}`、`POST /api/assets/{id}/duplicate\|move`、`GET /api/assets-export`、`POST /api/assets-import` |
| 资产地图 | `GET /api/asset-map`（只读拓扑：节点/边/循环/统计/分层） |
| 连接测试 | `POST /api/test-connection`（支持已保存资产或未保存的 inline 配置） |
| Chrome 代理 | `GET /api/chrome-proxy`、`POST /api/chrome-proxy/start`、`POST /api/chrome-proxy/{id}/stop` |
| 会话 | `POST /api/sessions/start`、`GET /api/sessions`、`POST /api/sessions/{id}/close\|input` |
| 文件 | `/api/sftp/{list,mkdir,rename,delete,chmod,read,write,download,upload}`、`/api/fs/*`、`/api/{sftp,fs}/archive/{formats,inspect,compress,extract}` |
| 数据库 | `/api/db/{overview,objects,table,ddl,query,explain,row,export}` |
| Redis | `/api/redis/{test,databases,scan,key,set-string,hash-set,hash-del,list-push,list-set,list-remove,set-add,set-remove,zset-add,zset-remove,create,delete,rename,expire,console,realtime}` |
| Docker | `/api/docker/{test,containers,images,volumes,networks,logs,inspect,stats,action,image-remove,pull}` |
| 批量 | `POST /api/batch/exec` |
| AI | `POST /api/ai/models`、`POST /api/ai/test`、WS 通道 `ai` |
| 设置/历史 | `GET/PUT /api/settings`、`GET/DELETE /api/history`、`GET/POST /api/commands` |
| 锁屏 | `GET /api/lock/status`、`PUT /api/lock/config`、`POST /api/lock/{password,lock,verify,activity}`、`DELETE /api/lock/password` |
| 更新 | `GET /api/update/check`、`POST /api/update/channel`（热更新 `start/status` 仍 501） |
| 能力注册表 | `GET /api/capabilities` |
| 会话记录 | `GET/POST /api/chats`、`DELETE /api/chats/{id}`、`GET /api/chats/{id}/messages` |
| RPC | `GET /rpc?token=…`（WebSocket） |

---

## 八、与 HexHub 5.1.9 的差异

| 维度 | 原产品 | 复刻版 | 原因 |
|---|---|---|---|
| 桌面壳 | CEF + `window.hexhub` 原生桥 | 浏览器访问本地服务 | 去掉 237 MB Chromium 依赖；无系统托盘；多窗口 = 独立浏览器窗口（`?asset=` 只开目标资产），分屏 = pane 布局 |
| 浏览器代理 | Pro：`startTunnelForWebBrowser` 起隧道后由 CEF 进程启动 Chrome | 后端启动本机 Chrome/Chromium，同一套 SOCKS5 隧道语义 + 临时配置 + 管理对话框 | 浏览器形态下窗口只能开在后端主机 |
| 后端语言 | Go 1.25（142 MB） | Python 3 / aiohttp | 迭代速度与运行环境可用性 |
| RPC 编码 | protobuf `Packet` | 二进制 Packet（兼容原版 XOR 帧）+ JSON 载荷；查询回复 protobuf | 复用拆解所得协议与 schema |
| 资产存储 | IndexedDB (Dexie) | SQLite（加密配置） | 后端可复用资产；加密更彻底 |
| 凭据加密 | 硬编码 AES 密钥，仅 SSH 字段 | AES-256-GCM，全部配置，独立密钥 | 修复拆解报告中的高风险项 |
| 账号/会员/支付/推广/云同步 | 有（云端） | 无 | 本地优先，无云端依赖 |
| 热更新 | 7z + MD5 + 重启 | 无 | 本地构建/部署 |
| 插件体系 | 6 个编译期内置插件 | 单体模块化服务 | 无动态插件需求 |
| 数据库范围 | 9 种 + DuckDB | MySQL/MariaDB、PostgreSQL、SQLite、DuckDB、ClickHouse、MongoDB、SQL Server、Oracle、达梦 DM8 | 聚焦可验证的核心 |
| Pro 限制 | 多窗口/同步/广播/跨服传输等 | 全部功能免费，无限制码 | 复刻版无商业化 |
| AI 助手 | 3 种会话 + 13 家供应商 | 1 个通用 Agent + 2 种协议 | 工具已覆盖终端/SQL 场景 |

### 未实现 / 未验证清单（明确边界）

- 传输协议：断点续传（trzsz 已实现）。
- 浏览器代理：Chrome 窗口只能开在后端所在机器，且需要图形会话；无图形环境返回明确错误（`H1KHUB_CHROME_PATH` 可指定浏览器）。
- 终端/桌面：系统托盘（浏览器形态无托盘）；锁屏、分屏/多窗口、版本检查与更新通道已实现并实测（锁屏见第八节与 `scripts/test_lock.py`，更新见 `scripts/test_update.py`）。
- 数据库：达梦 DM8、Oracle 19c SE2 与 SQL Server 2019 均已真实实例验证（Oracle 走公共实例 `ecourse.org:1521/ol8.ecourse.org`，SCOTT 无 `v$` 权限，连接数/会话列表如实为空；SQL Server 用本机 `hexhub-mssql:2019` 容器，`127.0.0.1:14330`，`15.0.4500.2` Developer Edition）；Kingbase/OpenGauss 未单独实测。**SQL Server 2022 镜像本机不可用**：容器在 VMware + 6.19 内核下于 PAL 初始化处空转，chroot 直跑同一 rootfs 亦复现（已排除 ulimit / seccomp / cgroup / overlayfs / `--privileged` / ASLR），故实验环境固定 2019 镜像。
- Redis：RedisJSON 子路径（仅根路径）。
- Docker：远程仓库推送（Docker Hub 不可达）；本地 OCI registry 发布/拉取已实现。
- AI：图片输入、MCP。
- 云端/产品形态：云同步、账号/会员/支付/推广、热更新（92）、插件市场；版本检查与通道已用本机 manifest 实现。
- 本轮新增能力均有自动化脚本：`scripts/test_*.py`，汇总见 `docs/test-results.log`。

### 锁屏（第二十二轮）、更新检查/通道（第二十三轮）与剩余预留项

第二十二轮实现了 90 的锁屏一半；系统托盘仍不可用。锁屏契约（详见
[`docs/reserved-slots.md#90-锁屏与托盘`](docs/reserved-slots.md#90-锁屏与托盘)）：

- 口令只存 `pbkdf2_sha256$210000$<salt>$<hash>`（16B 随机 salt / 32B digest），恒定时间比较，
  `GET /api/settings` 与所有锁屏响应都不回传哈希；通用 `PUT /api/settings` 不能改锁屏配置。
- 前 2 次错误口令直接 401，第 3 次起指数退避（2/4/8/…/60 秒），退避期 429 + `retryAfter`。
- 立即锁定、可配置空闲自动锁定（服务端判定，0=关闭）；锁定状态在服务端，刷新页面、重开标签、
  另开浏览器都保持锁定；解锁走 `POST /api/lock/verify`。
- 设置 → 安全可开关锁屏、设/改/清口令、调空闲时间、立即锁屏；系统托盘行保持「不可用」。
- `scripts/test_lock.py` PASS=50 FAIL=0（API + 真实 CDP；含 401/限速/空闲/刷新/新标签/无明文口令）。
- 限制：防误触不防对抗；后端重启清空进程内锁定标志（口令保留）；忘记口令需停服清
  `data.db` 的 `lock.passwordHash`；托盘不做。

第二十三轮实现了 91 版本检查与 93 更新通道（契约见
[`docs/reserved-slots.md#91-版本检查`](docs/reserved-slots.md#91-版本检查)）：

- **版本检查**：`GET /api/update/check` 读取本机 manifest（`file://` 绝对路径或
  `127.0.0.1`/`localhost` 的 http(s)；禁止 `api.hexhub.cn` 等外部主机），按数值比较
  `version`/`build`，返回 `current/latest/hasUpdate/notes`；只读，不下载、不执行、不写资产库。
  manifest 缺失/非法/超大一律 400（`UPDATE_*`），不会 500。
- **更新通道**：`POST /api/update/channel` 只接受 `stable|experimental` × `production|test`，
  持久化后通过 `{channel}`/`{env}` 模板或目录 URL 选择 manifest；不触发下载或重启。
- **autoCheck**：打开后前端在 bootstrap 完成后延迟检查一次，不做后台轮询。
- 设置→更新节可配置版本检查/通道/env/autoCheck/manifest；热更新行仍禁用。关于页与关于窗口的
  「检查更新」可用并显示 current/latest/hasUpdate/notes。
- `scripts/test_update.py` PASS=63 FAIL=0（API + 真实 CDP）。

其余 4 项仍为预留（详见 [`docs/reserved-slots.md`](docs/reserved-slots.md)，状态以
`GET /api/capabilities` 为准）：

- **92 热更新**：`update.hot` 预留（`POST /api/update/start`、`GET /api/update/status` → 501）。
- **95 云加密**：`cloud.encryption` 为 `unavailable`（依赖云端会员 RSA 体系，本地无法验证；不注册路由、无 UI 入口）。
- **98 MCP**：`ext.mcp` 预留（`GET /api/mcp/info` → 501；原版产物未发现 MCP，属新增能力）。
- **99 插件市场**：`ext.plugins` 预留（`GET /api/plugins` → 501；原版为编译期内置 6 插件，无动态市场）。

计数：`docs/completion-audit.csv` 为 **已复刻 80 / 未实现 13 / 形态替代 4 / 改进 2（99 行）**；
本地口径 **83/84（98.8%）**，剩余唯一本地缺口是 95 云加密。托盘 `unavailable` 不计入实现。

---

## 九、验证

详见 [`docs/verification.md`](docs/verification.md)。摘要：

- 数据库：事务回滚/提交、表结构编辑、`.sql` 导入、批量行、Blob、CSV/JSON/SQL/XLSX/TXT 导出、实时信息、元数据缓存 ✅
- PostgreSQL 回归：`select count(*) as n from users` → `2` ✅
- 文件：FTP 全操作、SCP 上下行、SFTP 浏览/上传/下载/编辑、超大文件提示 ✅
- 终端：Telnet、SSH Agent、SOCKS5/HTTP 代理、跳板机、批量执行 ✅
- 多层跳板：0/1/2/3/4 层逐跳 banner、每跳首次 known_hosts `new` 与二次 `known`、主机密钥变更定位到具体跳、循环与 10 层链快速 400、死跳失败后无残留连接/会话/隧道、最外层 SOCKS5 代理、DB/Redis `sshAssetId` 3 层链、Docker SSH、SFTP（含 `hub.sftp` 跨服务器传输）、SCP、SSHTunnel dynamic、终端会话与资产对话框链预览 ✅（`scripts/test_jumps.py` PASS=70 FAIL=0）
- 资产地图：连接类资产拓扑、跳板/SSH 依赖边、分层与孤立资产分区、循环/缺失引用、缩放平移、搜索筛选、选中连通分量与由外到内链详情、导航/资产面板入口 ✅（`scripts/test_assetmap.py` PASS=35 FAIL=0，截图 `docs/screenshots/asset-map*.png`）
- 隧道：local / remote / SOCKS5 三种独立隧道 ✅
- Chrome 服务器代理：SSH 资产右键启动本机 Chrome，SOCKS5 实际转发、临时配置、退出/停止自动回收、未安装 Chrome 的 400、右键菜单与管理对话框 ✅（`scripts/test_chromeproxy.py` PASS=75 FAIL=0）；**3 跳 SSH 链 + 真实 Chromium** 页面加载、每跳新增认证、窗口截图 ✅（`scripts/test_chromeproxy_jumps.py` PASS=23 FAIL=0）
- Docker：Compose、容器文件、创建/重命名/pause/top、本地/SSH/TCP 容器终端 ✅
- Redis：SCAN、类型读写、批量删除/重命名/设置值/TTL、控制台、实时信息 ✅
- AI：本地 mock 的流式工具调用 + 审批允许/拒绝 + SCP/隧道/FTP 全操作/Docker 容器启停/联网搜索工具 ✅
- 安全：无令牌 401、敏感字段不回传、DB 文件中无明文口令、权限 0600/0700 ✅
- 锁屏：PBKDF2 哈希 + 随机 salt、失败退避、立即/空闲锁定、刷新/新标签仍锁定、`data.db` 无明文口令（`test_lock.py` PASS=50）✅
- UI：无头 Chromium 全流程无 console error ✅
- 同步/传输/广播/密钥/密码对：结构差异与应用、数据 append/replace、SFTP 跨服务器、两会话广播、密钥登录 ✅
- 后台任务：传输/导入后台执行、轮询、取消 ✅
- 串口（PTY 模拟）与 Docker 镜像构建/导入导出/历史/清理 ✅
- 主题（6 套 + 跟随系统）与视图/例程编辑 ✅
- 终端/编辑器设置、会话日志与可配置快捷键 ✅
- Redis 3 节点集群（SCAN/类型/跨槽批量/INFO）✅
- 资产别名与拖拽排序 ✅
- AI 写文件 diff 预览与快照回滚 ✅
- VNC 条目与端口探测 ✅
- 本地/远程双栏文件、收藏与路径同步 ✅
- SQL 编辑器格式化与停止执行 ✅
- 私有仓库登录成功/失败路径 ✅
- trzsz / ClickHouse / MongoDB / 本地 OCI registry：真实 SSH 往返、真实数据库实例、真实 docker pull ✅
- 达梦 DM8：真实实例（`dm8_20250506_rev270902`，127.0.0.1:52360）连接/版本/对象树/表数据/查询/`EXPLAIN FOR`/DDL/元数据/增删改读回/CSV 导出/UI 树表格 全部实测（`test_dameng.py` PASS=29 FAIL=0）✅
- Oracle 19c SE2：真实公共实例（`ecourse.org:1521/ol8.ecourse.org`，服务名 `ol8.ecourse.org`，SCOTT/tiger）连接/版本/对象树（82 表 + 2 视图）/`EMP` 14 行 8 列主键 `EMPNO`/查询与 join/`DBMS_XPLAN`/DDL/元数据/索引/`ZZ_ORA_PROBE` 增改删读回并删除/CSV 导出/UI 树与 14 行表格 全部实测（`test_oracle.py` PASS=50 FAIL=0）✅
- SQL Server 2019：真实本机容器实例（`hexhub-mssql:2019`，`127.0.0.1:14330`，`Microsoft SQL Server 2019 RTM-CU32-GDR 15.0.4500.2` Developer Edition）连接/版本/`hexhub_demo` 库树与 dbo 架构/`users` 2 行 5 列主键 `id`/聚合与行读回/`SHOWPLAN_XML` 执行计划/DDL/表元数据/`zz_ss_probe` 增改删读回并删除（残留 0）/实时指标/进程列表/CSV 导出/UI 树与 2 行表格 全部实测（`test_sqlserver.py` PASS=32 FAIL=0）✅
- 多窗口/分屏（第二十轮）：垂直/水平分屏（≤4 面板）、拖动分隔条调比例、`Ctrl+←/→/↑/↓` 切面板焦点、资产「同屏打开」、标签「在新窗口打开」（`?asset=` 独立窗口）、标签栏自动滚到活动标签；`scripts/test_split.py` PASS=45 FAIL=0（`ELAPSED≈27 s`）✅
- 锁屏（第二十二轮）：PBKDF2-HMAC-SHA256 + 随机 salt（`data.db` 无明文）、错误口令计数与指数退避、立即/空闲锁定、服务端锁定状态（刷新/新标签仍锁）、设置→安全可配置、系统托盘不可用；`scripts/test_lock.py` PASS=50 FAIL=0（`ELAPSED≈31 s`，API + 真实 CDP）✅
- 版本检查/更新通道（第二十三轮）：本机 manifest 数值比较（5.1.10 > 5.1.9）、400 错误族、通道白名单与模板/目录选择、autoCheck 启动一次、热更新仍 501；`scripts/test_update.py` PASS=63 FAIL=0（API + 真实 CDP）✅
- 文件压缩/解压（第二十六轮）：SSH 登录后的 SFTP 文件面板支持 Ctrl/Shift 多选与右键；tar.gz / tar.bz2 / tar.xz / tar / zip / 7z 往返（含空格与中文名）、覆盖/跳过、目标目录新建、assetId 无终端直连、归档越界路径（`../`、绝对路径）拒绝、本地与远端对称；`scripts/test_archive.py` PASS=135 FAIL=0，`scripts/test_archive_ui.py` PASS=27 FAIL=0（真实 SSH + 无头 Chromium，0 console error）✅
- 汇总（第 25 轮）：`scripts/run_all_tests.sh docs/test-results-assetmap-final.log` → **37 个套件 + ui_flow、855 项 API 断言**全部 PASS、FAIL=0、`ui_flow ERRORS: []`、退出码 0（**最终全绿日志 `docs/test-results-assetmap-final.log`**；新增 `test_assetmap` PASS=35；`test_jumps` PASS=70；`test_zmodem` 在全量负载下首跑遇到 `rz` 提示窗口超时、`test_update` 首跑遇到 autoCheck 观测窗竞态，均为测试时序问题，补等待/观测基线后独立复跑与最终全量全绿，未改产品逻辑；`test_oracle` PASS=50）。第 26 轮新增的 `test_archive`（PASS=135）与 `test_archive_ui`（PASS=27）已注册进 `run_all_tests.sh` 并独立复跑全绿；第 27 轮新增的 `test_chromeproxy`（PASS=75）与 `test_chromeproxy_jumps`（PASS=23，真实 Chrome + 3 跳）已注册进 `run_all_tests.sh` 并独立复跑全绿；本轮未重跑其余 37 个套件。

复现 UI 检查：

```bash
python3 scripts/ui_flow.py          # 需要 38110 端口已启动
python3 scripts/capture_docs.py     # 重新生成文档截图
```

---

## 十、已知限制

1. 单用户、单进程；不适合直接暴露到局域网/公网。
2. `/api/auth/token` 仅校验回环地址，本机其他进程理论上可获取令牌（桌面单用户模型的固有限制）。
3. 数据库默认仍使用短连接；需要事务时通过“会话”模式（SQL 编辑器事务条）保持长连接。大表 COUNT 可能较慢。
4. 终端回放缓冲 512 KB；切换标签再回来会重建 xterm 并回放缓冲（会话本身不断）。
5. 串口/VNC、断点续传、数据库方言扩展等未覆盖场景见上表。
6. AI 需要用户自备模型 API Key；仓库不包含任何密钥。演示截图中的 `Mock LLM (本地演示)` 指向 `scripts/mock_llm.py`。
7. 前端 bundle 约 1.38 MB（gzip 410 KB），首次加载稍慢；未做路由级拆包。
8. 多层跳板中只有最外层跳板可使用 `proxy`；中间跳的 proxy 字段被忽略。每层一次完整 SSH 认证，延迟/fd 线性增长，8 层为防御性上限而非性能建议值。
9. 压缩/解压只覆盖 SFTP 与本地文件面板，明文 FTP 面板不支持（FTP 没有可用的远端 shell）；远端可用格式取决于主机是否安装 `tar`/`zip`/`7z`，格式列表由探测结果动态给出。超大会话的成员扫描按 20 万条/16 MiB 截断，此时结果标记 `partial`，其余部分依赖 tar/unzip/7z 自身的路径保护。远端 tar 的“跳过已存在文件”使用 GNU tar 的 `--skip-old-files`，在 BSD/macOS 目标上该选项不可用（覆盖模式不受影响）。

---

## 十一、声明

本项目仅用于学习、研究与自有环境下的效率工具替代。请勿用于未授权的系统访问、凭据窃取或任何违法用途。使用 SSH/数据库/Docker 功能时，请确保你对目标系统拥有明确授权。
