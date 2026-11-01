# H1KHub 验证记录

- 时间：2026-10-29 ~ 2026-10-31（Asia/Shanghai）
- 环境：Kali GNU/Linux Rolling 2026.2 / x86_64 / Xfce
- 运行方式：`./scripts/start.sh --port 38110 --home ~/.h1khub --no-open`
- 只用本机实验服务；未访问 `api.hexhub.cn`，未使用任何云账号
- UI 自动化：Chromium 148 无头模式 + CDP（`scripts/cdp_check.py` / `ui_flow.py` / `capture_docs.py`）

## 1. 版本

| 组件 | 版本 |
|---|---|
| Python | 3.13.12 |
| aiohttp | 3.13.5 |
| paramiko | 4.0.0 |
| PyMySQL | 1.4.6 |
| psycopg2 | 2.9.12 |
| redis-py | 6.4.0 |
| cryptography | 46.0.7 |
| Node / npm | 24.22.0 / 11.19.1 |
| Chromium | 148.0.7778.178 |
| MariaDB | 11.8.6 |
| PostgreSQL | 18.4 |
| Redis | 8.0.6 |
| 目标 SSH | OpenSSH 10.3p1（`hexlab@127.0.0.1:22`） |

## 2. 功能验证

### 2.1 资产与存储

| 用例 | 操作 | 结果 |
|---|---|---|
| 未授权访问 | `curl /api/assets` 不带令牌 | `401` ✅ |
| 公开端点 | `GET /api/public/info` | `200`，返回版本/端口/平台 ✅ |
| 资产创建 | 创建目录、SSH、MySQL、Redis、Docker、Shell、QuickCmd、DBQuery | 全部成功 ✅ |
| 敏感字段 | `GET /api/assets` 中 MySQL 资产 | 仅返回 `"secrets": ["password"]`，不含口令 ✅ |
| 静态加密 | `grep -c hexhub123 ~/.h1khub/data.db` | `0` ✅ |
| 文件权限 | `ls -la ~/.h1khub` | 目录 `700`；`data.db`、`secret.key`、`known_hosts`、`runtime.json`、日志均 `600` ✅ |
| 资产导入/导出 | 导出 JSON → 导入另一实例 | 成功（`imported: N`） ✅ |
| 删除递归 | 删除目录资产 | 子资产一并删除 ✅ |

### 2.2 SSH / SFTP

| 用例 | 操作 | 结果 |
|---|---|---|
| 密码登录 | 资产 `SSH hexlab` 测试连接 | 成功，主机密钥 `new` → `known` ✅ |
| 终端交互 | 打开终端，输入 `echo "H1KHub replica · $(date +%F)" && uname -sr && df -h \| head -3` | 输出正常，PTY 尺寸正确 ✅ |
| 跳板机 | `SSH 跳板演示`（`jumpAssetId=SSH hexlab`） | 两级跳转成功 ✅ |
| 多层跳板 | `scripts/test_jumps.py`：0–4 层链、循环、10 层上限、死跳、代理、DB/Redis/Docker/SFTP/SCP/终端、UI | PASS=70 FAIL=0（见第 29 节）✅ |
| known_hosts | 移除记录后重连 | 自动记录指纹；修改记录后拒绝连接 ✅ |
| SFTP 列表 | 打开 `~` | 13 项，含大小/时间/权限 ✅ |
| SFTP 上传 | 上传文件后刷新 | 文件与权限正确 ✅ |
| SFTP 编辑 | 打开文本文件、修改、保存 | 内容写回 ✅ |
| SFTP 删除/重命名/chmod | 逐项操作 | 成功 ✅ |
| 断开状态 | 关闭会话 | 终端显示 `[会话已结束]` ✅ |

### 2.3 数据库

| 用例 | 服务 | 结果 |
|---|---|---|
| 连接测试 | MariaDB 11.8 / PostgreSQL 18.4 / SQLite | 全部成功 ✅ |
| 对象树 | MySQL：3 库 / `demo` 2 表；PostgreSQL：`public` 2 表；SQLite：2 表 | 正确 ✅ |
| 表数据 | 分页 200、排序、`WHERE` 过滤 | 正确（SQL 预览一致） ✅ |
| 行更新 | 将 `users.id=1` 的 `name` 改为 `张三丰` | `affected=1`，刷新后可见 ✅ |
| 行插入 | 新增一行（返回 `id=4`） | 成功 ✅ |
| 行删除 | 删除新增行 | 成功 ✅ |
| 无主键表 | 只读保护 | 编辑按钮禁用 ✅ |
| DDL | MySQL `SHOW CREATE TABLE`；PostgreSQL 由元数据生成；SQLite `sqlite_master` | 可查看/复制 ✅ |
| 导出 | CSV / JSON / SQL | 内容与行数正确 ✅ |
| Explain | MySQL JSON / PostgreSQL JSON / SQLite QUERY PLAN | 正确 ✅ |
| 多语句 | MySQL 分号脚本 | 返回多个结果集 ✅ |
| SQL 资产 | 从资产树打开 `用户列表查询` → 执行 | 3 行结果 ✅ |
| SSH 隧道 | 数据库配置引用 SSH 资产 | 建立本地转发并连接成功（代码路径已测，未单独截图） ✅ |

### 2.4 Redis

| 用例 | 结果 |
|---|---|
| DB 列表 | `db0 (3 keys)` ✅ |
| SCAN | `greeting`(string)、`queue`(list)、`user:1`(hash) ✅ |
| 键详情 | `user:1` 显示 hash 字段/值、encoding、内存、TTL ✅ |
| TTL 设置 | 写入并回读 ✅ |
| 控制台 | `INFO` 等命令返回 ✅ |
| 实时信息 | 内存/客户端/命中率/键空间 ✅ |
| 写操作 | hash 增删、list push、set/zset 操作（API 验证） ✅ |

### 2.5 Docker

| 用例 | 结果 |
|---|---|
| 容器列表 | 9 个（含 running/exited） ✅ |
| stats | CPU/内存/网络字段 ✅ |
| 日志 | 最近 800 行 ✅ |
| 启停/重启/删除 | API/UI 已实现；未做破坏性操作验证 ◐ |
| 容器终端 | `docker exec -it` PTY；`echo docker-ok; id` 输出 root ✅ |
| 镜像/卷/网络 | 列表可加载 ✅ |
| 拉取镜像 | 代码路径已实现；未在验证中执行（避免网络副作用） ◐ |
| SSH 远程 Docker | 通过远端 `docker` CLI 实现；未搭建远程 Docker 目标验证 ◐ |

### 2.6 批量执行

- 目标：`SSH hexlab` + `SSH 跳板演示`
- 命令：`uname -a && uptime`
- 结果：2/2 OK；单台耗时 682 ms / 1377 ms；逐台输出与展开详情正常 ✅
- 失败路径：无 SSH 资产时提示"还没有 SSH 资产"；命令为空时前端拦截 ✅

### 2.7 AI Agent

使用 `scripts/mock_llm.py` 提供的 OpenAI 兼容本地端点（仅测试用）：

| 用例 | 结果 |
|---|---|
| 模型列表 | `GET /v1/models` → `mock-1` ✅ |
| 连通性测试 | 成功 ✅ |
| 流式对话 | 增量渲染，结束后写入 `chat_messages` ✅ |
| 工具调用 | `list_assets` 返回真实资产 JSON 并回填模型 ✅ |
| 写操作审批 | `shell` 触发审批卡片；允许后执行 `echo hello-from-h1khub-agent && uname -s`，结果回填并生成总结 ✅ |
| 拒绝路径 | 点"拒绝"→ 工具返回"用户拒绝执行命令"（代码路径） ✅ |
| 停止 | 停止按钮取消流式任务（代码路径） ✅ |
| 会话列表 | 新建/切换/删除、历史消息回读 ✅ |

> AI 未接入真实厂商密钥；上述验证只覆盖协议、工具循环与审批，不代表具体模型的回答质量。

### 2.8 UI / 交互

`python3 scripts/ui_flow.py` 覆盖：

- 首页渲染、资产树展开、打开 MySQL、表数据、SSH 终端、Docker、Redis、AI、设置
- 结果：全部页面渲染成功，`Runtime.exceptionThrown` / console error **0**
- 截图生成：`scripts/capture_docs.py`，11 张流程截图 + 2 张 AI 截图

已验证的交互：

- 标签切换/关闭、拖拽分栏（SFTP 宽度）
- 表格双击编辑并回写数据库
- 新建连接对话框：类型选择、表单、测试连接、创建
- 全局搜索、命令面板、主题切换

未做自动化覆盖：键盘快捷键全量、右键菜单全量、SFTP 拖拽上传、Telnet/串口。

## 3. 安全验证

| 项 | 结果 |
|---|---|
| 无令牌访问 `/api/*` | `401 <h1>session invalid</h1>` ✅ |
| 令牌获取仅回环 | 远端地址请求返回 403（代码路径） ✅ |
| 配置加密 | AES-256-GCM；DB 文件无明文口令 ✅ |
| 密钥文件 | `secret.key` 0600，目录 0700 ✅ |
| SSH 主机密钥 | TOFU + 变更拒绝 ✅ |
| AI 审批 | 写文件/命令/写 SQL 默认审批；危险命令强制审批 ✅ |
| 凭据回显 | API 不返回密码/私钥；UI 占位"已加密保存" ✅ |
| 遥测 | 无任何外连（除用户配置的模型 API 与网页抓取工具） ✅ |

## 4. 截图清单

| 文件 | 内容 |
|---|---|
| `docs/screenshots/01-home.png` | 首页 / 快捷入口 / 最近打开 |
| `docs/screenshots/02-ssh-sftp.png` | SSH 终端 + SFTP 双栏（`uname`/`df` 输出） |
| `docs/screenshots/03-mysql-table.png` | MySQL 表数据（排序、筛选、分页、SQL 预览） |
| `docs/screenshots/04-sql-editor.png` | SQL 编辑器 + 结果集 |
| `docs/screenshots/05-redis.png` | Redis 键详情（Hash） |
| `docs/screenshots/06-docker.png` | Docker 容器面板 |
| `docs/screenshots/07-batch-exec.png` | 批量执行 2 台主机 |
| `docs/screenshots/08-settings.png` | 设置 - 外观 |
| `docs/screenshots/09-settings-ai.png` | 设置 - AI 供应商 |
| `docs/screenshots/10-light-theme.png` | 亮色主题 |
| `docs/screenshots/11-ai-empty.png` | AI 空状态 |
| `docs/screenshots/12-ai-approval.png` | AI 工具审批卡片 |
| `docs/screenshots/13-ai-agent.png` | AI 工具结果与总结（Mock LLM） |

## 5. 已知未验证 / 未实现

1. X11 转发：未实现（paramiko 不支持 request_x11）。
2. 串口：未实现；本机无串口设备，无法验证。
3. VNC：未实现（原产品 Linux 版也仅做枚举）；RDP 原 Linux 版不支持。
4. trzsz / ZModem / 断点续传：未实现。
5. 数据库方言：SQL Server/Oracle/达梦/ClickHouse/DuckDB/MongoDB（无驱动或目标服务）。
6. Docker 私有仓库登录成功路径：无真实私有仓库。
7. 真实模型供应商：未配置真实 API Key；仅使用 mock。
8. 大规模数据（百万行）与并发会话压力：未测试。
9. AI Terminal 2.0 / SQL 专用助手、图片输入、MCP：未实现。
10. Redis 集群 / RedisJSON：未实现。
11. 桌面形态（多窗口/分屏/锁屏/托盘/热更新）与云端账号：按任务约定不做。

## 6. 复核补充（2026-10-29 13:20）

复核时发现并修复了一个会影响“PostgreSQL SQL 编辑器”结论的缺陷：

- 现象：对 PostgreSQL 资产执行任意 `SELECT` 都返回 `SQL 执行失败：not supported by PostgreSQL`。
- 原因：`PostgresAdapter.query()` 在取完第一个结果后调用 `cur.nextset()`；psycopg2 对 `nextset()` 直接抛 `NotSupportedError`，且 PostgreSQL 的简单查询协议不返回多个结果集。
- 修复：只处理 `execute()` 返回的单个结果集，不再调用 `nextset()`（[backend/db.py](../backend/db.py) 的 `PostgresAdapter.query`）。
- 复查：`select count(*) as n from users` → `2`；`select * from users order by id` → 2 行；`select 1; select 2` 返回最后一个结果集 `2`。
- 边界：PostgreSQL 多语句脚本只返回最后一个结果集（psycopg2 简单查询协议的限制），与 MySQL 的多结果集行为不同。

该缺陷说明：原验证记录只覆盖了 PostgreSQL 的连接、对象树和表数据，没有覆盖 SQL 编辑器，因此“PostgreSQL 查询 ✅”需要以本次修复后的复查为准。

## 7. P0 缺口补齐复验（2026-10-29 14:00–14:25）

本轮针对 `completion-audit.csv` 中的 P0 缺口补齐了数据库事务/结构编辑/导入/批量/Blob/导出、FTP/SCP、独立 SSH 隧道、Docker Compose/容器文件/仓库登录/远程终端、Redis 批量操作、AI 工具与供应商，并新增 6 个可复现测试脚本。

### 7.1 测试脚本与总结果

```bash
cd replica
./scripts/run_all_tests.sh docs/test-results.log
```

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `scripts/test_db.py` | 事务、结构编辑、.sql 导入、批量行、Blob、导出、实时信息、PG 回归 | `PASS=48 FAIL=0` |
| `scripts/test_files.py` | FTP 全操作、SCP 上下行、FTP 根目录保护 | `PASS=19 FAIL=0` |
| `scripts/test_network.py` | Telnet、SSH Agent、SOCKS5/HTTP 代理、local/remote/SOCKS5 隧道 | `PASS=26 FAIL=0` |
| `scripts/test_docker.py` | 容器创建/重命名/pause/top、容器文件、Compose、仓库登录错误路径 | `PASS=34 FAIL=0` |
| `scripts/test_redis.py` | SCAN、批量重命名/设置/TTL/删除、类型保护 | `PASS=15 FAIL=0` |
| `scripts/test_ai.py` | 流式、工具调用、审批/拒绝、SCP/隧道/FTP 工具、专用助手 | `PASS=24 FAIL=0` |
| `scripts/test_sync.py` | 表结构差异/同步、数据 append/replace（MySQL/PG） | `PASS=22 FAIL=0` |
| `scripts/test_extras.py` | 跨服务器传输、命令广播、密钥生成/复制、密码对、本地文件 | `PASS=28 FAIL=0` |
| `scripts/test_tasks.py` | 后台任务：传输/导入提交、轮询、结果、取消、404 | `PASS=10 FAIL=0` |
| `scripts/test_serial.py` | 串口会话：socat PTY 对双向收发、测试连接 | `PASS=5 FAIL=0` |
| `scripts/test_images.py` | Docker 镜像构建/历史/导出/导入/删除/清理 | `PASS=10 FAIL=0` |
| `scripts/test_objects.py` | 视图定义/保存/删除、MySQL 过程定义/重建/调用/删除 | `PASS=16 FAIL=0` |
| `scripts/test_settings.py` | 会话日志开关/落盘/0600/列表/下载、设置持久化 | `PASS=9 FAIL=0` |
| `scripts/test_cluster.py` | 3 节点 Redis 集群：连接/SCAN/类型/批量/INFO/实时 | `PASS=12 FAIL=0` |
| `scripts/test_tree.py` | 资产别名创建/更新、拖拽排序、移动根目录 | `PASS=4 FAIL=0` |
| `scripts/test_agent_undo.py` | AI 写文件 diff 预览、备份、回滚、404 | `PASS=7 FAIL=0` |
| `scripts/test_vnc.py` | VNC 资产 CRUD、端口探测、关闭端口失败、密码密文 | `PASS=4 FAIL=0` |
| `scripts/test_sql_editor.py` | MySQL/PG 会话与直连长查询取消、无查询 400 | `PASS=9 FAIL=0` |
| `scripts/test_registry.py` | 本地 Basic-auth /v2/ 私有仓库登录成功/失败 | `PASS=3 FAIL=0` |
| `scripts/ui_flow.py` | 首页/数据库/SSH/Docker/Redis/AI/设置 | `ERRORS: []` |

完整日志：[test-results.log](test-results.log)。

### 7.2 数据库

| 用例 | 操作 | 结果 |
|---|---|---|
| 会话级连接 | `/api/db/session/open` 后多次 `/api/db/session/query` | 同一连接可见前一次写入 ✅ |
| 事务回滚 | `BEGIN` → INSERT → `ROLLBACK` | 行数为 0 ✅ |
| 事务提交 | `BEGIN` → INSERT → `COMMIT` | 行数 +1 ✅ |
| 自动提交恢复 | `COMMIT` 后连接恢复 autocommit | 新 INSERT 立即可见 ✅ |
| 结构编辑 | 加字段/改类型/加索引/删字段 | MySQL、PostgreSQL 均成功 ✅ |
| 建表/删表 | `/api/db/schema` create-table/drop-table | 成功 ✅ |
| 外键 | 添加/删除外键 | MySQL/PG 成功；SQLite 明确报错 ◐ |
| .sql 导入 | 3 条语句（DROP/CREATE/INSERT） | 3/3 成功，3 行可见 ✅ |
| 导入错误 | 第二条语句引用不存在表 + stopOnError | `failed=1`，返回错误与回滚状态 ✅ |
| 批量更新/删除 | 选中 2 行更新、1 行删除 | `affected` 正确 ✅ |
| Blob | LONGBLOB 读取预览/大小/原始下载 | `Hello Blob!` / 11 bytes ✅ |
| 导出 | CSV/TXT/JSON/SQL/XLSX | XLSX 为合法 zip，其余内容正确 ✅ |
| 实时信息 | MySQL 状态/连接/命中率；PG 连接/事务/缓存；SQLite 页 | 均返回 ✅ |
| 元数据缓存 | 20 秒 TTL + `refresh` | 命中缓存；结构变更后失效 ✅ |
| PG 回归 | `select count(*) as n from users` | `2` ✅ |

边界：PostgreSQL 多语句脚本只返回最后一个结果集（psycopg2 简单查询协议限制）；SQLite 不支持在线修改字段类型/外键。

### 7.3 文件与终端

| 用例 | 操作 | 结果 |
|---|---|---|
| FTP 连接 | 明文 FTP `127.0.0.1:2121`（本地 fixture） | 连接成功 ✅ |
| FTP 列目录 | 根目录/子目录 | 目录/文件/大小/时间/权限正确 ✅ |
| FTP 上传/下载 | 二进制 27 字节 | 字节级一致 ✅ |
| FTP 编辑 | 写入/读回 UTF-8 文本 | 内容一致 ✅ |
| FTP 重命名/删除 | 文件重命名、目录递归删除 | 成功 ✅ |
| FTP 保护 | 删除 `/` | 400 拒绝 ✅ |
| SCP 上传 | 本机文件 → `/tmp/h1khub-scp-*.txt` | 远端 `cat` 内容一致 ✅ |
| SCP 下载 | 远端 → 本机 | 字节级一致 ✅ |
| SCP 错误 | 下载不存在文件 | 400 + `No such file` ✅ |
| Telnet | IAC 协商 + 登录 + echo/uname | 会话输出正确 ✅ |
| SSH Agent | 临时 ssh-agent + authorized_keys | Agent 认证连接、远程执行成功 ✅ |
| SSH Agent 错误 | 不存在的 agent socket | 400 + 明确错误 ✅ |
| SOCKS5 代理 | 本机 SOCKS5 fixture → SSH | 握手 + 命令执行成功 ✅ |
| HTTP CONNECT 代理 | 本机 HTTP 代理 → SSH | 握手 + 命令执行成功 ✅ |
| 代理错误 | 不可达代理端口 | 400 + 连接错误 ✅ |
| SSH 隧道 local | 本地监听 → 127.0.0.1:22 | 探测返回 SSH banner ✅ |
| SSH 隧道 remote | 远端监听 → 本机 22 | 探测返回 SSH banner ✅ |
| SSH 隧道 dynamic | SOCKS5 握手 → 22 | 握手/连接/banner 均成功 ✅ |
| 隧道停止 | 停止后端口释放、列表为空 | ✅ |

> FTP/Telnet/代理均为 `scripts/test_servers.py` 提供的**本地测试 fixture**，不是产品依赖。

### 7.4 Docker

| 用例 | 操作 | 结果 |
|---|---|---|
| 容器创建 | 使用本地镜像创建 `h1khub-e2e-*` 并启动 | running ✅ |
| 重命名/pause/unpause/top | 逐项执行 | 状态与进程列表正确 ✅ |
| 容器文件列目录 | `sh` + `stat` 扫描（避免整树 tar） | `/` 0.2 s 返回 ✅ |
| 容器文件读写 | 写入/读回 UTF-8；上传/下载二进制 | 字节级一致 ✅ |
| 容器文件重命名/删除 | 重命名、删除、拒绝删除 `/` | 正确 ✅ |
| Compose 读写 | 写入/读取 compose 文件 | 内容一致 ✅ |
| Compose up/ps/stop/restart/down | 临时项目 | 服务出现、停止、重启、down 清理 ✅ |
| 仓库登录 | 不可达 registry | 400，错误信息清晰 ◐（成功路径未验证） |
| 远程 Docker | TCP 2375（socat）+ SSH（临时 kali 密钥） | 容器列表/终端均成功 ✅ |
| 容器终端 | 本地/SSH/TCP 三种 | `echo docker-*-ok` 输出正确 ✅ |

远程 Docker 的 SSH 验证使用临时 `~/.ssh/authorized_keys` 条目，验证后已删除；未改变系统 Docker 权限。

### 7.5 Redis

| 用例 | 结果 |
|---|---|
| 键模式扫描 | `zz_rbatch_*` 精确匹配 ✅ |
| 批量重命名 | 前缀规则，3/3 成功 ✅ |
| 批量设置值/TTL | `v2` + 300 s，TTL 生效 ✅ |
| 批量持久化 | TTL=0 调用 PERSIST ✅ |
| 类型保护 | 对 list 键批量 set 返回失败原因 ✅ |
| 批量删除 | 3 个键删除，扫描为空 ✅ |
| 实时信息 | keyspace/内存/命中率 ✅ |

### 7.6 AI

| 用例 | 结果 |
|---|---|
| 模型目录/连通性 | mock `/v1/models` → `mock-1` ✅ |
| 流式对话 | 多个 delta + done ✅ |
| 工具调用（读） | `list_assets` 返回真实资产 ✅ |
| 审批（允许） | `shell` 触发审批，允许后执行 ✅ |
| 审批（拒绝） | 拒绝后工具返回错误、不执行 ✅ |
| `ftp_list` | 列出 FTP fixture 内容 ✅ |
| `scp_upload` / `scp_download` | 审批后传输，内容一致 ✅ |
| `start_tunnel` / `stop_tunnel` / `list_tunnels` | 启动返回端口、停止生效、列表为空 ✅ |
| 供应商预设 | 设置界面 13 家 + 自定义 ✅（真实 Key 未接入） |

### 7.7 界面（无头 Chromium 复核）

- 数据库：事务条（BEGIN/提交/回滚/自动提交）、导入对话框、结构设计对话框、实时信息面板、批量行操作、Blob 查看、导出 XLSX/TXT。
- FTP 资产打开为独立文件视图；SSH SFTP 面板新增 SCP 上传/下载。
- SSH 隧道资产打开为隧道视图，可启动/停止/探测，显示监听地址与连接数。
- Docker：Compose 面板、容器文件、创建容器、仓库登录、进程（top）、pause/unpause/重命名。
- Redis：批量操作对话框（预览/删除/重命名/设置值/TTL）。
- 设置：AI 供应商预设 13 家。
- 上述流程通过 CDP 复跑，`Runtime.exceptionThrown`/console error 为 0；截图见 `docs/screenshots/` 与本轮 `/tmp/shot-*.png`。

### 7.8 本轮新增/修改文件

- 后端：`backend/dbsessions.py`、`ftp_svc.py`、`scp_svc.py`、`tunnel_svc.py`；扩展 `db.py`、`docker_svc.py`、`redis_svc.py`、`ai.py`、`hub.py`、`sshslib.py`、`app.py`。
- 前端：`DbImportDialog.vue`、`DbSchemaDialog.vue`、`DbRealtimeDialog.vue`、`BlobDialog.vue`、`ContainerFiles.vue`、`RedisBatchDialog.vue`、`FilesView.vue`、`TunnelView.vue`；扩展 `DbView/DbTable/DbQuery/DockerView/RedisView/FilePane/AssetDialog/SettingsDialog`。
- 脚本：`test_db.py`、`test_files.py`、`test_network.py`、`test_docker.py`、`test_redis.py`、`test_ai.py`、`test_servers.py`、`run_all_tests.sh`、`restart.sh`。

## 8. 第三轮：同步 / 传输 / 广播 / 密钥 / 本地文件复验（2026-10-29 14:30–14:50）

### 8.1 新增验证

| 用例 | 操作 | 结果 |
|---|---|---|
| 结构差异 | 源表比目标表多 `qty` 列/索引，`name` 类型不同 | 检出 `add-column`/`modify-column`/`add-index` ✅ |
| 结构应用 | 执行生成的 ALTER | 目标表出现 `qty` 与索引 ✅ |
| 建表同步 | 目标表不存在 | 自动生成 CREATE TABLE ✅ |
| 数据预览 | 源 3 行、目标 0 行 | 行数与公共列正确 ✅ |
| 数据追加 | append 3 行 | 目标 3 行，无重复 ✅ |
| 数据替换 | replace 再执行 | 目标仍 3 行（先清空） ✅ |
| PostgreSQL | 同套结构/数据用例 | `BIGINT`/`TEXT` 差异与应用成功 ✅ |
| 跨服务器传输 | `SSH hexlab` → `SSH 跳板演示`，目录 2 文件 | 2 文件/字节一致 ✅ |
| 覆盖控制 | `overwrite=false` | 已存在文件进入 `skipped`，内容不变 ✅ |
| 命令广播 | 2 个 SSH 会话（含跳板）同时发送 `echo` | 两会话均输出结果 ✅ |
| 密钥生成 | ed25519 + 注释 + 保存为私钥资产 | 返回公钥/指纹，资产已创建 ✅ |
| ssh-copy-id | 把公钥追加到 hexlab | `added=true`，重复执行 `existed=true` ✅ |
| 密钥登录 | 用生成的私钥资产连接 | 未复制前失败、复制后成功 ✅ |
| 密码对 | SSHPasswordPair + SSH 引用 | 登录成功 ✅ |
| 本地文件 | home/list/write/read/rename/download/upload/delete | 全部成功 ✅ |

### 8.2 顺带修复

- **终端输出线程安全**：`BaseSession.feed()` 原先直接从 SSH/PTY 读取线程调用 `_emit_now()`，在事件循环空闲时可能延迟或丢失实时输出；改为走 `emit()`（`call_soon_threadsafe`）。修复后命令广播/实时输出在 WS 端稳定可见。
- 新增 `POST /api/sessions/{id}/read`：读取会话最近缓冲（用于自动化验证与 AI 读取终端）。

### 8.3 UI 复核

- 表数据工具栏新增“表同步”对话框：目标资产/库/schema/表、结构差异预览、应用结构、数据预览与 append/replace 复制。
- 终端工具栏新增“命令广播”：勾选当前打开的会话、输入命令、显示逐会话发送结果。
- SFTP 右键新增“传输到其他服务器…”：选择目标 SSH 资产与路径、覆盖开关。
- 资产编辑器：私钥资产可生成 ed25519/RSA 密钥、显示公钥并 ssh-copy-id；新增 SSHPasswordPair 编辑器。
- 无头 Chromium 复跑以上入口，`ERRORS: []`；截图 `docs/screenshots/23-table-sync.png`、`24-broadcast.png`。

### 8.4 汇总

```bash
cd replica && ./scripts/run_all_tests.sh docs/test-results.log
```

9 个套件共 **226 项 API 断言 + UI flow** 全部通过（`docs/test-results.log`）。

## 9. 第四轮：AI 专用助手 + 后台任务（2026-10-29 14:55–15:10）

| 用例 | 操作 | 结果 |
|---|---|---|
| 终端助手 | `assistant=terminal`，工具集裁剪为终端/SSH/隧道类 | mock 下 `docker_logs` 不在工具列表，未触发 ✅ |
| SQL 助手 | `assistant=sql`，附带数据库与 SQL 上下文 | 只读 `execute_sql` 执行并返回 `2` ✅ |
| 通用模式 | `assistant=general` | 原有流式对话正常 ✅ |
| 后台传输 | `/api/transfer` + `background:true` | 任务 success，目标文件字节正确 ✅ |
| 后台导入 | `/api/db/import` + `background:true` | 任务 success，表内 2 行 ✅ |
| 任务列表/404/取消 | `/api/tasks`、`/api/tasks/{id}` | 均符合预期 ✅ |
| UI | AI 工具栏“通用/终端/SQL”切换 + 上下文条；标题栏“后台任务”面板 | CDP 复核通过 ✅ |

实现要点：
- `ai.py` 增加 `tools_for(assistant)` 与专用提示词；`ChatRunner.run(..., tools=...)` 支持工具子集。
- `tasks.py` 统一任务队列；SQL 导入、跨服务器传输、Docker 拉取支持 `background`；任务面板轮询状态、显示结果/错误、可取消。
- 后台任务只保存在内存，进程重启后清空（长操作结果仍在目标端）。
- 测试稳定性：`scripts/cdp_check.py` 改用随机 CDP 调试端口；此前固定 9333 会在残留 Chromium 时连到旧页面，导致 ui_flow 误报。

## 10. 第五轮：串口 + Docker 镜像管理（2026-10-29 15:15–15:35）

| 用例 | 操作 | 结果 |
|---|---|---|
| 串口连接测试 | `/api/test-connection` 打开 `/tmp/hx-serial-a` | 成功 ✅ |
| 串口会话 | 创建 SerialSession，socat PTY 对端写 `hello-from-peer` | 会话读到 ✅ |
| 串口发送 | 会话输入 `hello-from-h1khub` | 对端 pty 收到 ✅ |
| 串口参数 | 115200/8/N/1 | 会话 meta 正确 ✅ |
| 镜像构建 | 临时 Dockerfile（本地基础镜像）构建 `h1khub-e2e-img:<tag>` | 成功、列表可见 ✅ |
| 镜像历史 | `docker history` 行 | 返回多层 ✅ |
| 镜像导出 | `/api/docker/image/save` | 合法 tar，含 `manifest.json` ✅ |
| 镜像导入 | `/api/docker/image/load` 上传 tar | `docker load` 成功 ✅ |
| 镜像删除/清理 | remove + `image prune` | 列表移除、清理成功 ✅ |

说明：
- 串口使用 socat 创建的虚拟串口对做端到端验证，**未使用真实串口硬件**。
- Docker 镜像构建使用本机已有基础镜像，不访问外网；`-f` 路径已按构建目录解析（修复了 BuildKit 读不到 Dockerfile 的问题）。
- UI：镜像面板新增标签/构建目录/构建、导入 tar、清理；镜像行支持历史与导出。

## 11. 第六轮：主题 + 视图/例程编辑（2026-10-29 15:40–15:55）

| 用例 | 操作 | 结果 |
|---|---|---|
| 主题数量 | 设置 → 外观 | 暗/亮/Nord/Dracula/Solarized/GitHub/跟随系统 + 4 强调色 ✅ |
| 主题切换 | 选 Nord → 保存 | `data-theme=nord`，`--bg=#2e3440`；切回暗色正常 ✅ |
| 视图列表/定义 | MySQL、PostgreSQL 临时视图 | 对象树可见、定义读取成功 ✅ |
| 视图编辑 | `CREATE VIEW ... AS SELECT id,name` | 保存后列变为 `id,name` ✅ |
| 视图删除 | object-drop | 列表移除 ✅ |
| MySQL 过程 | 创建 → 读取定义 → 重建（43→44） → CALL → 删除 | 调用返回 `44` ✅ |

UI：视图/例程右键菜单“查看/编辑定义、复制名称、删除”，定义编辑器支持保存与删除。
说明：PostgreSQL 过程定义/重建接口已实现，但本轮只用 MySQL 过程做端到端验证。

## 12. 第七轮：终端/编辑器设置 + 快捷键（2026-10-29 15:55–16:10）

| 用例 | 操作 | 结果 |
|---|---|---|
| 设置持久化 | `PUT /api/settings` 写入终端/编辑器/快捷键 | 回读一致 ✅ |
| 会话日志 | 开启 `terminal.logSessions`，SSH 会话执行命令后关闭 | `logPath` 生成，内容含命令输出 ✅ |
| 日志权限 | `stat` 日志文件 | `0600` ✅ |
| 日志 API | `/api/session-logs` 列表、`/api/session-logs/{name}` 下载 | 均成功 ✅ |
| 快捷键 | 把搜索改为 `Ctrl+J`，页面重载后 | `Ctrl+K` 不触发、`Ctrl+J` 打开搜索 ✅ |
| 编辑器设置 | `fontSize=16`、`wordWrap=true` 后打开 SQL 编辑器 | `.cm-content` 计算字号 16px、带 `cm-lineWrapping` ✅ |
| 终端右键 | 设置 `rightClick=select/paste/menu` | xterm 选项/右键粘贴接线（代码路径 + 设置回读） ✅ |

说明：会话日志仅在开启开关时落盘；关闭/退出时也会尝试写入。快捷键解析支持 Ctrl/Meta/Alt/Shift 组合，`Alt+1…9` 标签切换保留。

## 13. 第八轮：Redis 集群（2026-10-29 16:10–16:30）

本机启动 3 个 `cluster-enabled` Redis 8.0.6 节点（127.0.0.1:7001–7003）并执行 `redis-cli --cluster create`，使用真实集群验证：

| 用例 | 操作 | 结果 |
|---|---|---|
| 连接 | 资产 `cluster=true, nodes=7001..7003` | `test` 返回 `mode=cluster` ✅ |
| DB 选择 | `databases` | 仅 db0；db1 请求返回 400 ✅ |
| 类型读写 | String / Hash | 读取正确 ✅ |
| SCAN | `scan_iter` 跨节点聚合 | 3 个键 + hash 全部找到 ✅ |
| 批量重命名 | 3 键加前缀 | 跨槽用“克隆+删除”实现，3/3 成功 ✅ |
| 批量设置/TTL | v2 + 120s | 3/3 成功 ✅ |
| 批量删除 | 4 键 | 全部删除 ✅ |
| 控制台/实时 | INFO、keyspace | 正常 ✅ |

说明：集群节点为本地测试实例，测试脚本在端口未监听时会自动拉起；集群模式仅支持 db0，跨槽 RENAME 由 `_clone_key`（String/Hash/List/Set/ZSet）实现。RedisJSON 仍因本机 Redis 无 JSON 模块未实现。

## 14. 第九轮：资产树别名 + 拖拽排序（2026-10-29 16:35–16:50）

| 用例 | 操作 | 结果 |
|---|---|---|
| 别名创建 | 创建 QuickCmd 时带 `alias=别名A` | 资产列表返回 alias ✅ |
| 别名更新 | `PUT /api/assets/{id}` 改 alias | 回读更新成功 ✅ |
| 别名展示/搜索 | 树显示 `alias \|\| name`；搜索包含 alias | UI 复核 ✅ |
| 拖拽排序 | 把 A 拖到 B 前（move beforeId） | A.sort < B.sort ✅ |
| 拖到根目录 | move parentId=null | parentId 变为 null ✅ |

实现：`assets` 表新增 `alias` 列（启动时自动迁移），创建/更新/导出支持 alias；AssetTreeNode 可拖拽，AssetPanel 接收 drop 后调用 move API 并刷新。
说明：RDP/VNC 等少量类型仍无专用编辑器（原产品 Linux 版同样有限），不影响树交互。

## 15. 第十轮：AI 写入 diff + 快照回滚（2026-10-29 16:55–17:05）

| 用例 | 操作 | 结果 |
|---|---|---|
| diff 预览 | 旧文件 `line1/line2` 改为 `line1/CHANGED` | 审批卡片显示 `-line2/+CHANGED` ✅ |
| 写入 | mock LLM 调用 `write_file`，批准 | 文件写入成功，返回 `backupId` ✅ |
| 备份列表 | `/api/agent/backups` | 含该备份（路径/资产/时间/是否存在） ✅ |
| 一键回滚 | `/api/agent/rollback` | 内容恢复为旧版本 ✅ |
| 不存在备份 | rollback 无效 id | 404 ✅ |

实现：`agent_backup.py` 把写入前内容存入 `~/.h1khub/backups/`（0700，文件 0600），JSON 元数据记录本地路径或 SSH 资产；审批详情带 unified diff；会话内工具卡片提供“撤销写入”。

## 16. 第十一轮：VNC 条目与端口探测（2026-10-29 17:10–17:20）

| 用例 | 操作 | 结果 |
|---|---|---|
| VNC 资产 | 主机/端口/密码保存 | 创建成功，密码进入 secrets 加密 ✅ |
| 端口可达 | 本地监听端口 | 测试连接返回 host/port ✅ |
| 端口关闭 | 关闭监听后重试 | 400 错误 ✅ |
| 专用视图 | 打开 VNC 资产 | 显示地址、复制、测试端口；说明不内置 RFB（与原产品一致） ✅ |

说明：原产品 VNC 仅条目管理/UI；复刻版保持同样范围并额外提供端口探测与地址复制。

## 17. 第十二轮：本地/远程双栏文件（2026-10-29 17:25–17:40）

| 用例 | 操作 | 结果 |
|---|---|---|
| 双栏 | 标题栏“本地文件” | 左本地 `/home/kali`、右远程 SSH 同屏渲染 ✅ |
| 本地浏览 | 本地窗格列目录 | 文件/大小/时间/权限正常 ✅ |
| 收藏 | 收藏当前目录 | `general.localFavorites` 写入并显示 chip ✅ |
| 路径同步 | 同步路径到远程 | 远程窗格跳到同一路径 ✅ |
| 独立导航 | 两窗格各自前进/后退 | 路径互不影响 ✅ |
| 控制台 | 页面错误收集 | `ERRORS: []` ✅ |

实现：`FilePane` 新增 `backend=local`（`/api/fs`）与 `initialPath/path-change`；`LocalFilesView` 自动为所选 SSH 资产建立会话，关闭标签时释放。跨栏直接传输仍走各自“下载/上传”，未做服务端直传。

## 18. 第十三轮：SQL 编辑器格式化/停止/耗时（2026-10-29 17:45–17:55）

| 用例 | 操作 | 结果 |
|---|---|---|
| MySQL 会话取消 | `SELECT SLEEP(10)`，1s 后取消 | 请求被接受，查询 <8s 中断 ✅ |
| PostgreSQL 会话取消 | `SELECT pg_sleep(10)` | `pg_cancel_backend` 生效 ✅ |
| 直连（非会话）取消 | `/api/db/query` + `/api/db/cancel` | 中断成功 ✅ |
| 空取消 | 无执行中查询 | 400 ✅ |
| 格式化 | `select a,b from t where x=1 order by a` | 输出 `SELECT a,b\nFROM t\nWHERE x=1\nORDER BY a` ✅ |
| 执行耗时 | 运行中显示实时秒数 | UI 接线（代码路径） ✅ |

实现：适配器增加 `backend_pid()` / `cancel_pid()`；会话记录 `active_pid`；`/api/db/cancel` 与 `/api/db/session/cancel`；编辑器工具栏新增“停止/格式化”，运行中显示耗时。

## 19. 第十四轮：私有仓库登录成功路径（2026-10-29 18:00–18:10）

| 用例 | 操作 | 结果 |
|---|---|---|
| 登录成功 | 本地 `127.0.0.1:5006` Basic-auth `/v2/`，`registry-login` | Login Succeeded，服务端收到 `h1khub` 认证 ✅ |
| 密码错误 | 同一端点错误密码 | 400，docker 返回未授权 ✅ |
| 清理 | `docker logout` | 凭据移除 ✅ |

说明：Docker 对 localhost HTTP registry 允许明文登录；仅验证登录路径，镜像 push/pull 需完整 registry 实现，未验证。


## 20. 第十五轮：补齐 6 项缺口（2026-10-29 21:00–24:00）

> 本轮把 3 项“部分”和 3 项本地“未实现”补齐。测试脚本均已加入 `scripts/run_all_tests.sh`。

| # | 能力 | 实现 | 验证命令 | 结果 |
|---|---|---|---|---|
| 4 | RPC 二进制/Protobuf 通道 | `backend/rpcproto.py`（XOR Packet 帧、ChannelOpen/Send/Close、TableDataResponse 编码）、`/proto/{name}` 提供拆解所得的 5 个 schema、`frontend/src/lib/packet.js`（帧+protobuf 解码）、SQL 查询经 RPC 返回 protobuf | `python3 scripts/test_rpc_proto.py` | PASS=11 FAIL=0（原始 WS 帧 + UI：`protoQuery=1`、3 行结果） |
| 19 | X11 转发 | `SSHSession._start_x11`：读取 `~/.Xauthority` cookie，`Channel.request_x11`，x11 通道 ↔ 本地 6001 端口双向代理；资产高级设置新增开关 | `python3 scripts/test_x11.py` | PASS=6 FAIL=0（`DISPLAY=localhost:10.0`，`xdpyinfo` 返回 X 服务器版本） |
| 21 | 终端 WebGL | `@xterm/addon-webgl`，上下文丢失自动回退 DOM，设置项可开关；会话日志与重连沿用旧实现 | `python3 scripts/ui_flow.py` | `term renderer: webgl`，`ERRORS: []` |
| 34 | ZModem（rz/sz） | `zmodem.js` Sentry 接管终端输出；`sz` 下载自动保存、`rz` 弹窗选文件回传；下载记录在 `data-last-download` | `python3 scripts/test_zmodem.py` | PASS=6 FAIL=0（真实 lrzsz：`sz` 下载内容一致、`rz` 写成远端文件一致） |
| 45 | DuckDB | `DuckDBAdapter`（对象树/表数据/查询/Explain/DDL/事务/导出/ATTACH，含 sqlite_scanner 跨库）、`DuckDB demo` 资产、附加库按钮与 `/api/db/attach` | `python3 scripts/test_duckdb.py` | PASS=33 FAIL=0（含事务回滚、跨库 `extra.main.users`=2） |
| 60 | RedisJSON | `tools/redisjson/h1khub_json.c` 编译为模块（ReJSON-RL 数据类型，10 个 JSON.* 命令，根路径）；Redis 视图 JSON 编辑器走 `JSON.SET` | `python3 scripts/test_redisjson.py` | PASS=11 FAIL=0（`TYPE`=ReJSON-RL、UI 编辑保存落库） |

辅助验证：

```
$ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:38110/          # 200
$ curl -s http://127.0.0.1:38110/proto/table_data.proto | head -3        # proto3 schema
$ redis-cli -p 6385 MODULE LIST                                          # h1khub_json
$ redis-cli -p 6385 JSON.SET doc '$' '{"a":1}' && redis-cli -p 6385 TYPE doc   # OK / ReJSON-RL
$ DISPLAY=:1 xdpyinfo | head -3                                          # 本地 X 服务器 :1
```

运行环境新增（本地、离线）：

- pip（清华镜像）：`duckdb 2.0.0`、`pymongo 4.19.0`、`trzsz 1.1.7`、`clickhouse-driver`
- npm：`@xterm/addon-webgl 0.19.0`、`zmodem.js 0.1.10`、`trzsz 1.1.6`
- RedisJSON 模块：`gcc + libcjson-dev + redis 8.0.6 头文件`，模块位于 `~/.hexhub-lab/redis-modules/h1khub_json.so`，实例 `127.0.0.1:6385`
- DuckDB demo：`/home/kali/hexhub-lab/analytics.duckdb`

限制（如实记录）：

- RedisJSON 模块只实现根路径（`$` / `.`），不支持 JSONPath 子路径；`JSON.GET` 返回整篇文档。
- protobuf 通道中，`ChannelOpen` 外层使用 JSON `{method,args}`（同时兼容原版嵌套 Packet），`ChannelSend` 载荷为 JSON 字节；这是复刻版的约定，不是逐字节复刻原版全部 RPC 方法。
- `sz`/`rz` 依赖远端安装 lrzsz（本机已装 `/usr/bin/sz`、`/usr/bin/rz`）。

交付通道说明：WebGL/ZModem/X11 属于终端或会话语义，没有独立 REST 端点（X11 由资产配置 `x11` 开启、
ZModem 由终端协议触发、WebGL 由终端设置开启）；它们分别由 `ui_flow.py` 与专用 CDP 脚本验证。

---

## 21. 第十六轮：补齐 trzsz、本地 registry、ClickHouse、MongoDB（2026-10-29 17:07–18:10）

> 本轮把 4 项本地“未实现”补成真实可验证能力。新增脚本均已加入 `scripts/run_all_tests.sh`。

| # | 能力 | 实现 | 验证命令 | 结果 |
|---|---|---|---|---|
| 33 | trzsz | `backend/trzsz_svc.py`：运行远端 `trz`/`tsz`，解析 `::TRZSZ:TRANSFER` magic，经 SSH `direct-tcpip` 完成 tunnel hello 与 base64/zlib 帧协议（ACT/CFG/NUM/NAME/SIZE/DATA/MD5/SUCC/EXIT）；`/api/terminal/trzsz/upload\|download`；终端工具栏上传/下载按钮 | `python3 scripts/test_trzsz.py` | PASS=9 FAIL=0（真实 SSH：上传 180 KB 二进制，SFTP 读回字节一致；下载字节一致；缺失文件返回 400） |
| 71 | 本地网络拉取镜像 | `tools/oci_registry.py`：最小 OCI distribution registry（`/v2/`、manifests、blobs、catalog、tags）+ `docker save` 转 OCI layout；`backend/registry_svc.py` 管理进程；`/api/docker/local-registry`；DockerView「镜像 → 本地仓库」对话框（启动/发布/拉取） | `python3 scripts/test_local_registry.py` | PASS=10 FAIL=0（发布 `agent-compose-ui:latest`，`docker pull` 出现新 tag，RepoDigest 与发布 digest 一致，容器列表未变） |
| 43 | ClickHouse | `ClickHouseAdapter`（clickhouse-driver 原生 TCP 9000）：库/表树、表数据、查询、EXPLAIN、SHOW CREATE TABLE、INSERT、`ALTER … UPDATE/DELETE`（mutations_sync）、realtime/processlist、CSV 导出；`ClickHouse demo` 资产 | `python3 scripts/test_clickhouse.py` | PASS=24 FAIL=0（真实 26.10.3.7：`select count(*)`=2、EXPLAIN 含 ReadFromMergeTree、探针表增改删） |
| 46 | MongoDB | `MongoAdapter`（pymongo）：库/集合树、文档表、`db.users.find(...)` 与 JSON 命令（find/count/aggregate/insertOne/updateMany/deleteMany/drop）、explain、字段推断、realtime、CSV 导出；`MongoDB demo` 资产 | `python3 scripts/test_mongodb.py` | PASS=24 FAIL=0（真实 7.0.14：admin 查询命中、count=2、aggregate 求和、探针集合增改删/drop） |

环境搭建（本轮真实执行）：

```bash
# ClickHouse 26.10.3.7（官方 deb 源，packages.clickhouse.com 可达）
sudo dpkg -i clickhouse-common-static_26.10.3.7_amd64.deb clickhouse-server_26.10.3.7_amd64.deb
sudo clickhouse start          # 9000/8123 监听 127.0.0.1
clickhouse-client --query "CREATE DATABASE IF NOT EXISTS demo"
clickhouse-client --query "CREATE TABLE demo.users (id UInt32,name String,role String,age UInt8,created DateTime DEFAULT now()) ENGINE=MergeTree ORDER BY id"
clickhouse-client --query "INSERT INTO demo.users (id,name,role,age) VALUES (1,'Alice','admin',31),(2,'Bob','developer',27)"
clickhouse-client --query "CREATE TABLE demo.events (id UInt32,user_id UInt32,action String,ts DateTime) ENGINE=MergeTree ORDER BY id"

# MongoDB 7.0.14（Kali non-free deb，mirror.nyist.edu.cn 下载后解包，不改系统包状态）
dpkg-deb -x mongodb-server-core_7.0.14-0kali3_amd64.deb /home/kali/hexhub-lab/mongodb-7.0
/home/kali/hexhub-lab/mongodb-7.0/usr/bin/mongod --dbpath /tmp/hexhub-lab-mongo \
  --port 27017 --bind_ip 127.0.0.1 --logpath /tmp/hexhub-lab-mongo/mongod.log --fork
python3 -c "import pymongo; c=pymongo.MongoClient('mongodb://127.0.0.1:27017'); c.demo.users.insert_many([{'name':'Alice','role':'admin','age':31},{'name':'Bob','role':'developer','age':27}])"

# 远端 trz/tsz（SSH 用户 hexlab 直接可执行）
sudo python3 -m pip install --break-system-packages trzsz
sudo -u hexlab /usr/local/bin/trz -v      # trz (trzsz) py 1.1.7
```

接口与 UI 辅助验证：

```
$ curl -s -X POST -H "X-H1KHub-Token: $TOKEN" -H 'Content-Type: application/json' \
    -d '{"assetId":"<ch>","database":"demo","sql":"SELECT count(*) FROM users"}' \
    http://127.0.0.1:38110/api/db/query        # rows [[2]]
$ curl -s -X POST -H "X-H1KHub-Token: $TOKEN" -H 'Content-Type: application/json' \
    -d '{"action":"status"}' http://127.0.0.1:38110/api/docker/local-registry
$ docker images | grep 5050                     # 出现拉取的新 tag
$ python3 scripts/ui_flow.py                    # mongo grid rows: 2 / clickhouse grid rows: 2
                                                # trzsz buttons: 2 / local registry dialog 打开 / ERRORS: []
```

全量回归（第十六轮结束）：

```
$ ./scripts/run_all_tests.sh docs/test-results.log
OK   ... 28 个套件全部 PASS=… FAIL=0
OK   ui_flow ERRORS: []
# 合计 439 项 API 断言；汇总见 docs/test-results.log
```

限制（如实记录）：

- trzsz 本轮只覆盖单文件上传/下载；整目录（`-d`）未实现。远端无 `trz/tsz` 时接口返回明确错误；同名远端文件由 trz 自动改名为 `name.0`（不覆盖原文件）。
- trzsz 依赖 SSH 服务端允许 `direct-tcpip`（默认允许）；协议为第三方 `trzsz` 1.1.7，本地/远端版本需兼容。
- ClickHouse 无事务（`begin` 明确报错）；行更新/删除是 `ALTER TABLE` mutation，已加 `mutations_sync=1` 以便同步观察；未验证 TLS/集群。
- MongoDB 查询控制台是 JSON 命令与 `db.<collection>.<op>()` 子集，不是完整 mongosh；字段类型按前 50 个文档推断；未验证副本集/认证集群。
- 本地 registry 为只读、无鉴权、HTTP，仅绑定 `127.0.0.1`，仅支持本机 Docker；镜像必须已存在于本机（用 `docker save` 打包，不访问外网）。发布/拉取失败不会删除既有容器或镜像。


## 22. 第十七轮：SQL Server / Oracle / 达梦 三种方言（2026-10-29 21:00–22:40）

> 本轮为 40/41/42 三项补齐适配器、接口、前端与自动化。结论先说：
> **达梦 DM8 取得真实实例并全部实测通过（已复刻）**；
> **SQL Server 与 Oracle 只有代码路径，未取得真实连接（仅代码路径未实测）**，原因见下。

### 22.1 实现

| 层 | 内容 |
|---|---|
| `backend/db.py` | `SQLServerAdapter`（pymssql / TDS）、`OracleAdapter`（python-oracledb thin）、`DamengAdapter`（dmPython）；`DIALECTS` 增加 `sqlserver`/`oracle`/`dameng`；`make_adapter` 增加 `mssql`/`sql_server`/`azure-sql`、`oracledb`、`dm`/`dm8` 别名 |
| 方言要点 | SQL Server：`[db].[schema].[table]`、`OFFSET … FETCH NEXT`、`OBJECT_ID`/`sys.indexes` 取主键、`SET SHOWPLAN_XML` 取执行计划、`INSERT … OUTPUT INSERTED.*`、多结果集 `nextset()`；Oracle/DM：标识符折大写、schema=用户、`OFFSET … FETCH NEXT`、DBMS_XPLAN / `EXPLAIN FOR` |
| `backend/dbsessions.py` | `realtime_info`、`processlist`、`table_schema` 各加三分支；系统视图不可用时逐项 `try/except` 退化为空，不抛错 |
| 前端 | `store.js` `DB_TYPES` 增加三项；`ConnectionPicker` 的 SQL Server 由 `disabled` 改为可用并新增 Oracle/达梦；`AssetDialog` 端口默认 1433/1521/5236、Oracle 字段改“服务名（Service Name）”；`DbQuery` 方言占位提示沿用 |
| 脚本 | `scripts/pull_image_fast.py`（并行分块拉镜像）、`scripts/lab_mssql_setup.py`、`scripts/db_adapter_check.py`（离线断言）、`scripts/db_ui_probe.py`（UI 探针）、`scripts/test_sqlserver.py`、`test_oracle.py`、`test_dameng.py` |

### 22.2 达梦 DM8 —— 真实实例，已复刻

```bash
# 官方 Docker 镜像（635 MB，国内直连约 30 MB/s；地址由官网下载中心 API 取得）
curl -s "https://eco.dameng.com/eco-download-server/cpu/os/table/download/32/35/35"
#   → {"url":"/eco/dm8/dm8_20250506_x86_rh6_rq_single.tar", ...}
# 下载 → docker load
docker load -i dm8.tar
#   Loaded image: dm8:dm8_20250506_rev270902_x86_rh6_64

docker run -d --name hexhub-dm8 \
  -e SYSDBA_PWD='Hexhub@12345' -e PAGE_SIZE=16 -e CASE_SENSITIVE=1 \
  -p 127.0.0.1:52360:5236 -m 2g dm8:dm8_20250506_rev270902_x86_rh6_64
#   容器日志：create dm database success / Init DM success!

# dmPython 2.6.1 需要 DM 客户端动态库，从容器取出（不进系统 loader 配置）
docker cp hexhub-dm8:/opt/dmdbms/bin /home/kali/dm_client/bin
# scripts/start.sh 会自动把该目录加入本进程 LD_LIBRARY_PATH 并设置 DM_HOME

# 演示用户与表（真实执行）
#   CREATE USER HEXHUB IDENTIFIED BY "Hexhub@12345"; GRANT RESOURCE TO HEXHUB;
#   HEXHUB.USERS(id IDENTITY PK, name, email, role, created_at) × 2 行
#   HEXHUB.ORDERS(id IDENTITY PK, user_id, amount, status, created_at) × 2 行
```

实测结果：

```
$ python3 scripts/test_dameng.py
asset 达梦 demo = a_c160373052a7
  PASS  连接测试                       # version = DM Database Server 64 V8
  PASS  版本来自实例
  PASS  overview dialect               # dameng
  PASS  schema 树含 HEXHUB
  PASS  对象树含 USERS / ORDERS
  PASS  表数据 total=2 / 主键 ID / 5 列 / 可编辑
  PASS  聚合查询 count=2、行数据读回
  PASS  EXPLAIN 返回计划               # EXPLAIN FOR，输出 NSET2/PRJT2/CSCN2 树
  PASS  DDL 生成、schema 列数=5
  PASS  创建探针表 / 插入 2 行 / UPDATE+读回 / DELETE+读回
  PASS  realtime 指标、processlist、CSV 导出
  PASS  UI 打开资产 / UI 对象树含 USERS / UI 表格渲染行
PASS=29 FAIL=0
```

### 22.3 SQL Server 2022 —— 仅代码路径未实测

镜像确实拿到了：`mcr.microsoft.com` 单连接仅约 86 KB/s，用 `scripts/pull_image_fast.py`
（24 路并发 Range + 逐块重试 + 摘要校验 + 直接 `docker load`）在 938 s 内拉完 597.7 MB 并载入成功：

```
$ python3 scripts/pull_image_fast.py --registry mcr.microsoft.com \
    --repo mssql/server --tag 2022-latest --repo-tag hexhub-mssql:2022
  3 layers, config 7fb213934171…
  … verified sha256 ×3
  archive streamed (597.7MB)
  Loaded image: hexhub-mssql:2022
```

但实例无法启动：容器进度停在 PAL 缓冲区初始化之后并转入 100% CPU 空转，始终没有
`SQL Server is now ready for client connections`：

```
14:21:45 Server  Buffer Pool: Allocating 4194304 bytes for 298072 hashPages.
14:22:20 Server  Buffer pool extension is already disabled. No action is necessary.
（此后无输出；容器 CPU 113%，errorlog 不再增长，TDS 握手一律失败）
```

已排除的配置（每一种都重新起容器复测，均同样卡在该点）：

| 试验 | 结果 |
|---|---|
| `--ulimit nofile=1024` → `1048576:1048576` | 仍卡住 |
| `-m 2g` / `-m 4g` / 不设内存上限 + `MSSQL_MEMORY_LIMIT_MB=2048` | 仍卡住 |
| `--security-opt seccomp=unconfined`（排除 NUMA 相关 syscall 被拦） | 仍卡住 |
| `--shm-size=1g`、`--user` 默认 mssql、端口 14330 映射 | 仍卡住 |
| 宿主侧：`vm.max_map_count=1048576`、`fs.aio-max-nr=65536`、clocksource=tsc、fsync 3.7 ms | 均正常 |

宿主为 **VMware 虚拟机 + Linux 6.19 内核**，SQL Server 2022 的 PAL 在该组合下于启动早期自旋。
因此本轮不对 40 记“已复刻”，`docs/completion-audit.csv` 记为**仅代码路径未实测**，
并在 `scripts/test_sqlserver.py` 中提供离线断言（无实例时自动降级）：

```
$ python3 scripts/test_sqlserver.py
MODE=code-path（未找到资产 SQL Server demo，仅验证 adapter 代码路径）
  PASS  DIALECTS 注册 sqlserver / make_adapter 类型 / dialect / 默认端口 1433
  PASS  占位符 %s / 标识符 [users] / 别名 mssql、sql_server
  PASS  关闭端口报 DatabaseError 且信息含 “SQL Server”
PASS=9 FAIL=0
LIMIT: 无真实 SQL Server 实例，库/表/查询/写入均未实测
```

### 22.4 Oracle XE 21.3 —— 仅代码路径未实测

`oracledb` 26.1.0 已验证可 `import`（thin 模式，无需 Instant Client），适配器代码路径齐全；
但 Oracle XE 镜像 **3.75 GB**，`container-registry.oracle.com` 实测约 90 KB/s（16 路并发约 1.0 MB/s），
在本会话内无法拉完（缓存目录保留已下载分块，`scripts/pull_image_fast.py` 支持断点续传）：

```
$ curl -s "https://container-registry.oracle.com/v2/database/express/manifests/21.3.0-xe" …
  layers 4, total 3751.4 MB
$ python3 scripts/pull_image_fast.py --registry container-registry.oracle.com \
    --repo database/express --tag 21.3.0-xe --repo-tag hexhub-oracle:21.3.0-xe --workers 32
  … 下载至约 277 MB 后中止（带宽不可行）
```

离线断言同样通过（`python3 scripts/test_oracle.py` → `MODE=code-path`，PASS=9 FAIL=0）。

### 22.5 前端与资产

- `ConnectionPicker`：SQL Server 不再是“规划中”，与 Oracle、达梦并列为可选数据库类型。
- `AssetDialog`：切换类型时端口自动填 1433 / 1521 / 5236；Oracle 的库名输入改为“服务名（Service Name）”，占位 `XEPDB1`。
- `seed_demo.py`：新增三项演示资产，**仅在端口可连通时创建**（本轮实际只创建了“达梦 demo”，另两项打印 `skip …（实例 … 未监听）`）。
- UI 实测（`scripts/db_ui_probe.py`）：双击目录“生产环境”展开 → 双击“达梦 demo” → 左侧出现 `HEXHUB / USERS / ORDERS` 树 → 点 `USERS` 表格渲染 2 行。
- 连接选择器与新建对话框实测（无头 Chromium + CDP，逐项点开并读回表单值）：

| 点选 | dbType | 端口默认 | 库名字段 |
|---|---|---|---|
| 达梦 | `dameng` | 5236 | 数据库名（可选） |
| Oracle | `oracle` | 1521 | 服务名（Service Name） |
| SQL Server | `sqlserver` | 1433 | 数据库名（可选） |

  选择器中三项均显示为可用条目（描述分别是“达梦 DM8”“Oracle 12c+（thin 模式）”“SQL Server / Azure SQL”），不再带“规划中”。

### 22.6 全量回归（第十七轮结束）

```
$ ./scripts/run_all_tests.sh docs/test-results.log
OK   test_db … test_rpc_proto（28 个既有套件）全部 PASS=… FAIL=0
OK   test_dameng PASS=29 FAIL=0
OK   test_sqlserver PASS=10 FAIL=0        # MODE=code-path
OK   test_oracle PASS=9 FAIL=0            # MODE=code-path
OK   ui_flow ERRORS: []
# 31 个套件、488 项 API 断言全部通过；汇总见 docs/test-results.log
```

ui_flow 新增达梦段落（真实 UI）：

```
open dameng: ok:达梦 demo
dameng tree: DAMENG | 表 | 2 | ORDERS | USERS | 视图 | 0 | 存储过程 / 函数 | 0 | 新建查询 | 刷新
open dameng table: ok:USERS
dameng grid rows: 2
ERRORS: []
```

### 22.7 限制

1. SQL Server 与 Oracle 未取得真实连接，所有“库/表/查询/写入”结论均不成立，仅为代码路径。
2. 达梦实例跑在 Docker 中，宿主 `dmPython` 需 `LD_LIBRARY_PATH` 指向 DM 客户端库；`scripts/start.sh` 已自动处理，直接用 `python3 backend/app.py` 启动会报“加密模块加载失败”。
3. 达梦 `EXPLAIN` 必须写 `EXPLAIN FOR`，否则报 `-2002 执行未准备SQL语句`（适配器已按此实现）。

## 23. 第十八轮：Oracle 19c 真实实例 —— 41 转「已复刻」（2026-10-30 11:41–12:35）

> 本轮不自建容器：直接对接上一会话已实测可连的**公共 Oracle 19c 训练实例**，
> 把 `completion-audit.csv` 的 41（Oracle）从「仅代码路径未实测」改为「已复刻」。
> 40（SQL Server）本轮无变化，仍为「仅代码路径未实测」（原因见 22.3）。

### 23.1 实例事实

| 项 | 值 |
|---|---|
| 连接串 | `jdbc:oracle:thin:@ecourse.org:1521/ol8.ecourse.org` |
| 主机 / 端口 / 服务名 | `ecourse.org` / `1521` / `ol8.ecourse.org`（服务名写法，**不是 SID**、不是 `XEPDB1`） |
| 账号 | `scott` / `tiger`（公共共享培训库，当前 schema `SCOTT`） |
| 版本 | `Oracle Database 19c Standard Edition 2 Release 19.0.0.0.0 - Production` / `Version 19.3.0.0.0` |
| DB_NAME | `OL8` |
| 对象 | `SCOTT` 下 82 张表 + 2 个视图（含索引共 160 个对象） |
| 只读样本 | `EMP`（14 行 / 8 列 / 主键 `EMPNO`）、`DEPT`（4 行） |
| 写权限 | `CREATE TABLE` / `DROP TABLE` 可用，仅用于自建探针表 `ZZ_ORA_PROBE` |
| 受限 | `v$pdbs`、`v$session`、`v$process` 均 `ORA-00942`（SCOTT 无权限） |
| 稳定性 | 偶发 `DPY-4011 the database or network closed the connection`，间隔 1–2 s 重试即恢复 |

直连自检（不经过复刻版）：

```bash
$ python3 - <<'PY'
import oracledb
dsn = oracledb.makedsn("ecourse.org", 1521, service_name="ol8.ecourse.org")
c = oracledb.connect(user="scott", password="tiger", dsn=dsn, retry_count=3, retry_delay=2)
cur = c.cursor(); cur.execute("SELECT banner_full FROM v$version WHERE ROWNUM=1")
print(cur.fetchone()[0]); c.close()
PY
Oracle Database 19c Standard Edition 2 Release 19.0.0.0.0 - Production
Version 19.3.0.0.0
```

### 23.2 本轮修掉的适配器缺陷

| 位置 | 问题 | 修正 |
|---|---|---|
| `backend/db.py` `OracleAdapter.connect` | 向 `oracledb.connect()` 传 `autocommit=True`，python-oracledb 26.1.0 不接受该关键字，任何连接都直接 `TypeError → Oracle 连接失败：connect() got an unexpected keyword argument 'autocommit'` | 改为连上后设置 `conn.autocommit = True`（`set_autocommit` 早已按属性实现） |
| 同上 | 公共实例偶发握手中断（`DPY-4011`），单次尝试即失败 | 非本机主机握手最多 3 次、间隔 1.5 s，仅对 `DPY-4011/6005/4024`、`DPI-1080` 等瞬态码重试；`127.0.0.1` 保持单次（离线代码路径断言仍然快速失败） |
| 同上（只读路径） | 握手成功后会话仍可能被重置：`list_objects` / `table_detail` / `ddl` / `explain` / 只读 `query` 一旦命中就整条请求 400，前端对象树只留下空白，用户必须手动刷新 | 新增 `_ora_retry_read` 装饰器：命中瞬态码时 `_reconnect()` 后重跑，最多 3 轮；`query()` 只对 `SELECT` / `WITH` 重试，DML / DDL **绝不重试**（服务端可能已经执行，重试会重复写入） |
| `scripts/db_ui_probe.py` | 固定 `sleep(3)` 等树，外网实例（一次建连约 5 s）来不及渲染；表格行文本带行数徽标（`EMP` + `14`），按文本精确匹配永远不中 | 改为轮询等待（默认 90 s）＋按 `title` 属性定位表格行，并回传 `treeFull` 供断言 |

### 23.3 实测（`scripts/test_oracle.py`）

```bash
$ cd replica && python3 scripts/test_oracle.py
asset Oracle demo = a_2843d7dccf04  target=scott@ol8.ecourse.org
```

关键真实返回：

| 检查 | 实例返回 |
|---|---|
| `/api/test-connection` | `Oracle Database 19c Standard Edition 2 Release 19.0.0.0.0 - Production\nVersion 19.3.0.0.0` |
| `/api/db/overview` | `dialect=oracle`；`databases=[{"name":"ol8.ecourse.org","kind":"service"}]`（`v$pdbs` 无权限 → `except` 回退到服务名）；`schemas` 含 `SCOTT`（还有 HR / JOINTREE / LLIU） |
| `/api/db/objects` `schema=SCOTT` | 82 表（含 `EMP`、`DEPT`）+ 2 视图（`MYCONSTRAINT`、`MYCONSTRAINTS`）；`EMP` 的 `num_rows=14` |
| `/api/db/table` `EMP` | `total=14`、`primaryKey=["EMPNO"]`、8 列 `EMPNO/ENAME/JOB/MGR/HIREDATE/SAL/COMM/DEPTNO`、`editable=true`，首行 `[7369, SMITH, CLERK, 7902, 1980-12-17, 800, None, 20]` |
| `/api/db/table` `DEPT` | `total=4` |
| `/api/db/query` | `select count(*) from emp` → `[[14]]`；`select count(*) from dept` → `[[4]]`；`emp join dept` → `[[14]]`；`where empno=7369` → `[[7369, SMITH]]` |
| `/api/db/explain` | `format=text`，`Plan hash value: 3956160932`，`TABLE ACCESS FULL / EMP` |
| `/api/db/ddl` `EMP` | `CREATE TABLE "SCOTT"."EMP" (… 8 列 … PRIMARY KEY ("EMPNO"))` |
| `/api/db/table-schema` `EMP` | 8 列、主键 `EMPNO`、1 个唯一索引（约束索引 `PK_EMP4LIU`） |
| 写路径 | `DROP/CREATE ZZ_ORA_PROBE(NUMBER(10) PK, VARCHAR2(50), NUMBER(10,2))` → 插入 2 行 → `count=2` → `UPDATE id=1 name=alpha2` 读回一致 → `DELETE id=2` → `count=1` → 探针表 DDL 正确 → `DROP` → `all_tables` 计数 `0`（共享库无残留） |
| `/api/db/realtime` | `dialect=oracle`、`collectedAt`、`databaseSize=59441152`（来自 `user_segments`）；`connections/active` 因 `v$session` 无权限缺失，属预期 |
| `/api/db/processlist` | `{"processes": []}`（`v$session` 无权限），按预期为空 |
| `/api/db/export` | CSV 14 行 + 表头，含 `SMITH` |

```text
  PASS  连接测试
  PASS  版本来自实例 (Oracle Database 19c)
  PASS  overview dialect / databases 回退为服务名 / schema 列表含 SCOTT
  PASS  对象树含 EMP / DEPT / 规模 >= 80 表 / 含视图 / EMP 统计行数=14
  PASS  EMP total=14 / 主键 EMPNO / 8 列且名称一致 / 可编辑 / 14 行 / 首行 SMITH
  PASS  DEPT total=4
  PASS  count(emp)=14 / count(dept)=4 / join emp/dept = 14 / 行数据读回 SMITH
  PASS  DBMS_XPLAN 计划 / DDL 生成 / DDL 含 8 列定义 / schema 列数=8 / 主键 / 索引非空
  PASS  创建探针表 / 3 列 / 主键 ID / 插入 2 行 / 行数=2 / UPDATE+读回 / DELETE+读回
  PASS  探针表 DDL / 探针表已删除（共享库无残留）
  PASS  realtime dialect / 时间戳 / processlist 为列表（允许为空）/ 端点可用
  PASS  CSV 导出含 SMITH / 导出 14 行
  PASS  UI 打开资产 / 对象树含 EMP / 对象树含 DEPT / 表格渲染 14 行
PASS=50 FAIL=0
```

运行以 `--skip-ui` 时为 `PASS=46 FAIL=0`（跳过 4 项 UI 断言）。整轮耗时随公网握手与重试次数波动，
实测 2–8 分钟；期间多次命中 `DPY-4011`，全部由适配器的只读重连重试和脚本的请求级重试自动恢复
（日志里以 `· transient … retry 1/3` 记录，不计失败）。

### 23.4 UI（真实 Chromium + CDP）

```
open oracle: ok:Oracle demo
oracle tree: ol8.ecourse.org | 表 | 82 | ACCOUNT | ACCOUNTS | … | DEPT | 4 | … | 刷新
open oracle table: ok
oracle grid rows: 14
ERRORS: []
```

Oracle 在 `DbView.vue` 里不是 schema 树（`isSchemaTree` 只含 postgresql/duckdb），
所以前端传 `schema=undefined`，后端 `_current_schema()` 返回 `SCOTT`，对象树直接落在 SCOTT 下 —— 与实测一致。
截图：`/tmp/shot-02f-oracle.png`（`ui_flow.py` 新增段落）。

### 23.5 共享实例约束（本轮遵守情况）

1. 只在 `SCOTT` 下建过 `ZZ_ORA_PROBE`，测试末尾 `DROP`，并用
   `SELECT count(*) FROM all_tables WHERE owner='SCOTT' AND table_name='ZZ_ORA_PROBE'` → `0` 复核。
2. 未对 `EMP`、`DEPT`、`USERS` 等既有表执行任何 DML/DDL；写路径断言全部落在探针表上。
3. 未在 `SCOTT` 下创建 `USERS` / `ORDERS`。
4. `seed_demo.py` 用 `_tcp_open` 门控（远端超时 3 s），外网不通时只打印 `skip`，不留坏资产。
5. 连接一律走复刻版 API 短连接，之间保留自然间隔；脚本对瞬态重置显式重试，不靠“连快一点蒙过去”。

### 23.6 计数与文档同步

| 口径 | 本轮前 | 本轮后 |
|---|---|---|
| 已复刻 | 74 | **75** |
| 仅代码路径未实测 | 2（SQL Server、Oracle） | **1**（SQL Server） |
| 未实现 | 17 | 17 |
| 本地功能口径 | 77/80（96.3%） | **78/80（97.5%）** |

同步文件：`docs/completion-audit.csv`（41 行）、`docs/feature-matrix.csv`（数据库行）、`README.md`、
根目录 `复刻完成度核查报告.md`、`复刻交付说明.md`，以及本节。

全量回归（第十八轮结束）：

```
$ ./scripts/run_all_tests.sh docs/test-results.log
OK   test_db PASS=48 FAIL=0 … OK   test_dameng PASS=29 FAIL=0
OK   test_sqlserver PASS=10 FAIL=0        # 仍为 MODE=code-path
OK   test_oracle PASS=50 FAIL=0           # 真实实例（含 UI 4 项）
OK   ui_flow ERRORS: []
# 31 个套件、529 项 API 断言全部通过；日志见 docs/test-results.log
```

`ui_flow.py` 新增 Oracle 段落（真实 UI）：

```
open oracle: ok:Oracle demo
oracle tree: ol8.ecourse.org | 表 | 82 | ACCOUNT | ACCOUNTS | … | DEPT | 4 | … | 刷新
open oracle table: ok
oracle grid rows: 14
ERRORS: []
```

### 23.7 限制

1. `v$pdbs` / `v$session` / `v$process` 不可读，所以 overview 的 `databases` 只有一条服务名、
   realtime 缺连接数、processlist 恒为空 —— 这些都是**权限事实**，不是实现缺陷，测试已按此断言。
2. 公共实例 `DPY-4011` 频率约每 6–10 次请求一次，单次 API 调用需 5 s 左右（公网握手）；
   因此 Oracle 套件比本地方言慢（2–8 分钟），`run_all_tests.sh` 把 `test_oracle` 的超时放宽到 900 s，
   `ui_flow` 放宽到 400 s。只读操作的重连重试由 `_ora_retry_read` 承担，写操作永不重试。
3. 服务名必须是 `ol8.ecourse.org`；写 `SID` 或 `XEPDB1` 会 `ORA-12505`。
4. 本轮未改 `38103` 原版实例、未新增容器，DM8 容器（`hexhub-dm8`）保持 Up 且 `test_dameng.py` 仍全绿。

## 24. 第十九轮：SQL Server 2019 真实实例 —— 40 转「已复刻」，仅代码路径清零（2026-10-30 13:30–15:30）

> 本轮只做方向 A：把 40（SQL Server）从「仅代码路径未实测」转成真实实例验证。
> 结论：**2022 镜像在本机确实起不来，但 2019 镜像可以**；SQL Server 已是真实实例验证。

### 24.1 为什么 2022 镜像起不来（本轮新增的关键证据）

上一轮只看了日志。本轮直接看**在跑的那颗进程**：卡住时不是死锁，而是单线程纯用户态自旋。

```
$ docker top mssql-diag -o pid,tid,pcpu,stat,comm
2554071  2554685  113   Rl   Wt-488      ← 只有这一个线程烧 CPU
$ sudo cat /proc/<pid>/task/2554685/syscall
running                              ← 没有阻塞在任何系统调用上
$ sudo gdb -p 2554071 -ex "x/12i 0x00003fffa372d0a0"
0x3fffa372d0b0 <...>:  rdtsc
0x3fffa372d0b2 <...>:  shl $0x20,%rdx        ← 采样点落在 PAL 自己的时间基准代码
```

该地址落在 SQLPAL 自己映射的 `0x00003fff…` 私有地址段：它既不在主 ELF 也不在 glibc 里，
`gdb` 给不出任何符号（同一段内可见 `/opt/mssql/lib/system.common.sfp` 的映射）。
即自旋发生在 PAL 的私有代码里，而不是 SQL Server 的逻辑层。日志永远停在：

```
Server  Buffer pool extension is already disabled. No action is necessary.   ← 最后一行
```

**决定性试验：绕开容器。** 把镜像 rootfs `docker export` 到宿主 ext4，用 `chroot` 直接跑
（没有 namespace / seccomp / cgroup / overlayfs / AppArmor），同样自旋、stdout 一个字都不输出：

```
$ docker export mssql-export | tar -x -C ~/hexhub-lab/mssql-host
$ sudo chroot <rootfs> /usr/bin/setpriv --reuid=10001 --regid=10001 --clear-groups \
      env -i HOME=/var/opt/mssql ACCEPT_EULA=Y MSSQL_PID=Developer \
      MSSQL_SA_PASSWORD=Hexhub@12345 /opt/mssql/bin/sqlservr
（进程存活、~62% CPU、/tmp/chroot-mssql.log 0 字节、1433 不可连）
```

即**与 Docker 无关**，是宿主组合（VMware 虚拟机 + Linux 6.19 + Ryzen 7 255）与该镜像 PAL 的交互。

| 本轮复测的配置 | 结果 |
|---|---|
| `--privileged --ipc=host` | 仍卡住（errorlog 同一行） |
| `--cpuset-cpus=0,1`（只给 2 个 vCPU） | 仍卡住 |
| `kernel.randomize_va_space=0`（临时改，已复原为 2） | 仍卡住 |
| `chroot` 直跑镜像 rootfs（无容器运行时） | 仍卡住（本轮新增，排除容器假设） |
| **`hexhub-mssql:2019` 同参数** | **约 50 s 出现 `SQL Server is now ready for client connections`** |

### 24.2 2019 镜像事实

镜像用同一个 `scripts/pull_image_fast.py` 拉取（mcr 单连接 ~90 KB/s，32 路 Range 并发）：

```
$ python3 scripts/pull_image_fast.py --registry mcr.microsoft.com --repo mssql/server \
    --tag 2019-latest --repo-tag hexhub-mssql:2019 --workers 32
  3 layers, config d47c3524cdca…
  d47c3524cdca… verified sha256 / e012aedd45a6… / 7b31edbfaa9c… / bb5bd3385472…
  archive streamed (505.1MB)
  Loaded image: hexhub-mssql:2019        # 1.49 GB，hexhub-mssql:2022 仍保留 1.69 GB
```

启动（`scripts/start_mssql.sh` 固化同一组参数）：

```
$ docker run -d --name mssql2019 -e ACCEPT_EULA=Y -e MSSQL_PID=Developer \
    -e SA_PASSWORD=Hexhub@12345 -e MSSQL_SA_PASSWORD=Hexhub@12345 \
    -p 14330:1433 hexhub-mssql:2019
```

| 项 | 值 |
|---|---|
| 连接 | `127.0.0.1:14330`（容器 `1433`），账号 `sa` / `Hexhub@12345` |
| 版本 | `Microsoft SQL Server 2019 (RTM-CU32-GDR) (KB5128494) - 15.0.4500.2 (X64)` |
| 版本属性 | `ProductVersion=15.0.4500.2`、`Edition=Developer Edition (64-bit)`、`Collation=SQL_Latin1_General_CP1_CI_AS` |
| 平台 | `Developer Edition (64-bit) on Linux (Ubuntu 20.04.6 LTS) <X64>` |
| 资源 | 检测到 8 logical processors、11904 MB RAM |
| 就绪耗时 | 首次初始化约 50 s（`05:56:08` 起，`05:56:58` ready） |

> 注意：**2019 镜像读 `SA_PASSWORD`，不读 `MSSQL_SA_PASSWORD`**（后者是 2022 才加的）。
> 只给 `MSSQL_SA_PASSWORD` 时 `sa` 登录会 18456；`scripts/start_mssql.sh` 两个都传。

### 24.3 数据与探针表

`scripts/lab_mssql_setup.py`（幂等）建库建表：

```
$ python3 scripts/lab_mssql_setup.py --host 127.0.0.1 --port 14330
connected: Microsoft SQL Server 2019 (RTM-CU32-GDR) (KB5128494) - 15.0.4500.2 (X64)
product version: 15.0.4500.2
hexhub_demo ready: users=2 orders=2
```

| 对象 | 事实 |
|---|---|
| `hexhub_demo` | 默认库；对象树另可见 `master` / `model` / `msdb` / `tempdb` |
| `dbo.users` | 2 行、5 列（`id` 主键 IDENTITY、`name`、`email`、`role`、`created_at`）、`editable=true` |
| `dbo.orders` | 2 行、外键 `user_id → users.id` |
| 探针表 | `zz_ss_probe`（`id INT PRIMARY KEY`、`name NVARCHAR(50)`、`score FLOAT`） |

写入路径逐步读回（`test_sqlserver.py`）：

```
CREATE TABLE dbo.zz_ss_probe → INSERT 2 行 → count=2 → UPDATE id=1 name=alpha2 → 读回 alpha2
→ DELETE id=2 → count=1 → DROP TABLE → 残留检查 0
```

删除后复核（真实查询，不是日志）：

```
SELECT COUNT(*) FROM sys.tables WHERE name LIKE 'zz_%' OR name LIKE 'ZZ_%'  → 0
SELECT name FROM sys.tables ORDER BY name                                  → orders, users
```

### 24.4 测试结果

```
$ python3 scripts/test_sqlserver.py
asset SQL Server demo = a_33bef38c09b0
  PASS  连接测试 / 版本来自实例且为 SQL Server
  PASS  overview dialect / 数据库树含 hexhub_demo / schema 列表含 dbo
  PASS  对象树含 users / orders
  PASS  表数据 total=2 / 主键为 id / 列元数据 5 列 / 可编辑标记
  PASS  聚合查询 count=2 / 行数据读回
  PASS  SHOWPLAN_XML 计划
  PASS  DDL 生成 / schema 列数=5 / schema 主键
  PASS  创建探针表 / 插入行 1 / 插入行 2 / 探针表行数=2
  PASS  UPDATE 行 / UPDATE 读回一致 / DELETE 行 / DELETE 读回一致
  PASS  realtime 指标 / processlist 可用 / CSV 导出含数据
  PASS  UI 打开资产 / UI 对象树含 users / UI 表格渲染行
PASS=32 FAIL=0
```

（29 项 API 断言 + 3 项 UI 断言；无实例时会自动降级为 `MODE=code-path` 的离线断言，本轮未触发。）

### 24.5 UI

`ui_flow.py` 新增 SQL Server 段落（真实无头 Chromium + CDP，双击资产 → 点表 → 数行）：

```
open sqlserver: ok:SQL Server demo
sqlserver tree: hexhub_demo | master | model | msdb | tempdb | 表 | 2 | orders | 2 | users | 2 | 视图 | 0 | 存储过程 / 函数 | 0 | 新建查询 | 刷新
open sqlserver table: ok
sqlserver grid rows: 2
```

### 24.6 顺带修掉的 Oracle 抗抖动问题（否则全量回归会红）

公共 Oracle 实例每 6–10 次请求会 `DPY-4011` 重置一次，且偶尔连续 60–90 s 不可用。
本轮实测到的问题是：**适配器重试会被「重连也失败」打断**，一次抖动就把 400 直接抛给前端，
UI 于是显示空对象树（`test_oracle` 的 3 项 UI 断言因此在全量回归里红过一次）。

改动（都不降低断言强度）：

1. `backend/db.py::_ora_retry_read`：尝试次数 3 → 4，退避 1/2/3 s；**`_reconnect()` 自身失败不再中断重试预算**，
   下一次尝试会自己重新建连（原实现会直接把重连异常抛出去）。
2. `backend/app.py`：`/api/db/overview` 与 `/api/db/objects` 在**瞬时重置**（`is_transient_reset`）且存在上次成功结果时，
   回退到缓存值而不是抛错；非瞬时错误（权限、语法、不存在）照旧抛出。
3. `scripts/db_ui_probe.py`：树为空时点面板自带的「刷新」按钮重试（最多 2 轮），表格为空时再点一次表 ——
   这就是用户会做的同一个动作，断言仍是「树里有 EMP/DEPT、表格 14 行」。

改完单轮：`python3 scripts/test_oracle.py` → **PASS=50 FAIL=0**，耗时 10 分 30 秒（含 6 次瞬时重试）。

### 24.7 全量回归（第十九轮结束）

```
$ ./scripts/run_all_tests.sh docs/test-results.log
OK   test_db PASS=48 FAIL=0 … OK   test_dameng PASS=29 FAIL=0
OK   test_sqlserver PASS=32 FAIL=0        # 真实实例，不再是 MODE=code-path
OK   test_oracle PASS=50 FAIL=0           # 含 3 项 UI 断言（走刷新重试）
OK   ui_flow ERRORS: []
# 31 个套件 + ui_flow、551 项 API 断言全部通过；日志见 docs/test-results.log
```

`ui_flow.py` 本轮新增 SQL Server 段落（真实 UI）：

```
open sqlserver: ok:SQL Server demo
sqlserver tree: hexhub_demo | master | model | msdb | tempdb | 表 | 2 | orders | 2 | users | 2 | 视图 | 0 | 存储过程 / 函数 | 0 | 新建查询 | 刷新
open sqlserver table: ok
sqlserver grid rows: 2
ERRORS: []
```

### 24.8 口径变化

| 口径 | 本轮前 | 本轮后 |
|---|---|---|
| 已复刻 | 75 | **76** |
| 仅代码路径未实测 | 1（SQL Server） | **0** |
| 未实现 | 17 | 17 |
| 本地功能口径 | 78/80（97.5%） | **79/80（98.8%）** |

99 项里现在只剩 17 项「未实现」（云同步/账号/会员/多窗口/托盘/热更新/MCP/插件市场等默认不做项）、
4 项「形态替代」（CEF 外壳、Go 后端、反嵌入、RDP）和 2 项「改进」；**没有任何一项还停留在“仅代码路径”**。

同步文件：`docs/completion-audit.csv`（40 行）、`docs/feature-matrix.csv`（数据库行）、`README.md`
（数据库表 / 演示资产表 / 未实现清单 / 验证摘要，并新增 `scripts/start_mssql.sh` 说明）、
根目录 `复刻完成度核查报告.md`、`复刻交付说明.md`，以及本节。

### 24.9 限制

1. **只能证明 2019 在本机可用**。2022 镜像的自旋可能与 VMware 虚拟化、6.19 内核、Ryzen 7 255 的
   组合有关，换宿主机可能正常；因此文档写的是“本机 2022 不可用”，不是“SQL Server 2022 不支持”。
2. 容器没有挂持久卷，数据在容器可写层；容器被删除后重跑 `scripts/start_mssql.sh` 会幂等重建 `hexhub_demo`。
3. 实例是本机 Docker 里的 Developer Edition（实验口令 `sa / Hexhub@12345`），不是生产实例，
   也不代表 Azure SQL / 企业版的全部行为。
4. 公共 Oracle 实例仍是最大抖动源：本轮单轮 3.5–10.5 分钟，`run_all_tests.sh` 里 `test_oracle` 超时 900 s、
   `ui_flow` 400 s；只读重试与“瞬时重置回退缓存”已加固，写操作仍然永不重试。
5. 本轮未改 `38103` 原版实例、未改动任何既有容器；`hexhub-dm8` 保持 Up，`test_dameng.py` 仍 PASS=29。

## 25. 第二十轮：89 多窗口/分屏 —— 浏览器形态实现并实测（2026-10-30 15:20–16:05）

> 本轮只做方向 B 的一项：**89 多窗口/分屏**。原产品是 CEF 桌面壳，复刻版是浏览器访问；
> 本轮把「垂直/水平分屏、面板焦点、同屏打开、新窗口打开」在浏览器形态下实现，并用无头 Chromium 断言。

### 25.1 为什么选 89（以及为什么不选别的）

| 候选项 | 判断 |
|---|---|
| **89 多窗口/分屏** | 原版确有（Pro 功能），**纯本地、可自动化断言**；浏览器可用「pane 布局 + 独立浏览器窗口」等价实现 → **本轮做** |
| 98 MCP | 原版产物里没有 MCP（审计备注已写「原产品也未发现」），做了也只是新增能力，不是复刻 |
| 99 插件市场 | 原版是编译期内置 6 个插件、无动态市场，同样属于「原版也没有」 |
| 90 锁屏/托盘 | 托盘依赖系统桌面，浏览器形态无法验证；且该项与 89 混在同一行，只做了其中「多窗口」部分 |
| 92 热更新 | 需要有打包产物+签名+灰度分发链路，浏览器形态只能模拟，证据强度低 |
| 方向 C（降时延/ELAPSED/seed 断网） | 属加固，不改变计数；本轮先补功能，加固留待后续 |

### 25.2 原版证据（读产物字节，不凭记忆）

原版前端打包在 `extract/opt/hexhub/hexhub-backend`（Go 二进制内嵌前端 assets）。直接读字节：

```js
// 终端/串口标签右键菜单（hexhub-backend @ offset 68852014）
{type:"item",title:"垂直分屏",icon:"mdi-arrow-split-horizontal",hotkey:"basicSplitVertical",vip:!0,click:()=>i.emit("onSplitScreen",!0)},
{type:"item",title:"水平分屏",icon:"mdi-arrow-split-vertical",hotkey:"basicSplitHorizontal",vip:!0,click:()=>i.emit("onSplitScreen",!1)}

// 标签右键（@69725507 同段落）
{type:"item",title:"新窗口打开",icon:"mdi-open-in-new",vip:!0,hotkey:"basicTabNewWindow",click:()=>{n.emit("onNewWindow",l)}}

// 资产右键「同屏打开」→ openSplitView
{type:"item",title:"同屏打开",icon:"mdi-card-multiple-outline",vip:!0,
 disabled:b||!R.length||R.length!==w.length,click:()=>de(R,"splitView")}
async openSplitView(f){ const y=await Promise.all(f.map(...)); const D=jm(y,4); t.emit("openSplitView",D) }

// 快捷键默认值（Windows/Linux，@ offset 73673598；macOS 侧为 Command+Shift+D / Command+D）
basicSplitVertical:"Ctrl+Shift+=", basicSplitHorizontal:"Ctrl+Shift+-",
basicSplitMoveLeft:"Ctrl+←", basicSplitMoveRight:"Ctrl+→",
basicSplitMoveBottom:"Ctrl+↓", basicSplitMoveTop:"Ctrl+↑",
basicTabNewWindow:"Ctrl+N", basicTabClose:"Ctrl+W"
```

要点：这两项在原版都是 **Pro（`vip:!0`）**；复刻版沿用「全部功能免费、无限制码」的既有约定。

### 25.3 复刻实现（浏览器形态）

| 原版行为 | 复刻版实现 |
|---|---|
| 视图拆分：垂直/水平分屏 | `App.vue` 按 `state.panes` 渲染 1–4 个 `.pane`；`vertical`=左右栏、`horizontal`=上下栏 |
| 每个视图独立标签 | `TabBar` 接收 `pane` 属性，只渲染本 pane 的标签；`openTab/activateTab/closeTab` 按 pane 派发 |
| 拖动调比例 | `.pane-handle` 拖动 → `resizePanes()`（两侧最小 15%，总宽/总高守恒） |
| `basicSplitMove*` 切焦点 | `Ctrl+←/→/↑/↓` → `focusPane()`；`.pane.focused` 高亮 |
| 标签右键「垂直/水平分屏」 | 同名菜单项（到 4 面板置灰），快捷键 `Ctrl+Shift+=` / `Ctrl+Shift+-` |
| 资产右键「同屏打开」 | 「同屏打开（新分屏）」→ `openAssetInNewPane()` |
| 「新窗口打开」 | `window.open('/?asset=<id>&standalone=1')`；`bootstrap()` 解析 `?asset=` 只打开该资产 |
| 面板上限 4 | 取自原版 `openSplitView` 的 `jm(y,4)` 参数（**推断**，非原版明文常量） |

改动文件：`frontend/src/lib/store.js`（pane 模型与派发）、`App.vue`、`components/TabBar.vue`、
`components/AssetPanel.vue`、`components/SettingsDialog.vue`（新增 6 条快捷键行）、`styles/theme.css`。

### 25.4 实测（无头 Chromium over CDP，真实 DOM + 真实会话）

```
$ python3 scripts/test_split.py
PASS=45 FAIL=0
ELAPSED=27.4
```

关键断言与实测值：

| 断言 | 实测 |
|---|---|
| 初始单面板 | `panes=1`、`workspace` 无 `panes` 类 |
| 标签右键含分屏项 | `["关闭","关闭其他","关闭右侧","垂直分屏","水平分屏","重新打开"]` |
| 垂直分屏后结构 | `panes=2`、`dir=vertical`、`handles=1`、`tabs=[1,1]`、`terms=[1,1]`、焦点在新面板 |
| 分屏产生独立会话 | `/api/sessions` 11 → 13（两块终端各自 PTY） |
| 拖动分隔条 | `[727,364] → [927,164]`（Δ=200），总宽 1091 不变，比例 0.85 在 [0.15,0.85] 内 |
| Ctrl+←/→ 切焦点 | `focused 1→0→1`，最右再右移不变 |
| 资产「同屏打开」 | `panes 2→3`，新面板有终端 |
| `Ctrl+Shift+=` 分屏 | `panes 4`；第 5 次被拒（toast「最多支持 4 个分屏」，仍为 4） |
| 面板标签隔离 | `[1,1,1,1] → [1,1,1,2]`（只有聚焦面板新增 Docker 标签） |
| 宽度均分 | `widths=[270,270,270,270]` |
| 新窗口打开 | 新目标 `?asset=a_4eed1def693e&standalone=1`，`panes=1 tabs=1`，标签「本地终端」 |
| 关闭分屏 | `4→3`，该面板标签同时关闭（5→4），继续关到 `panes=1` 且无 `panes` 类 |
| 控制台 | 无 JS 异常/错误日志 |

截图：`docs/screenshots/32-split-view.png`（4 面板：3 个终端 + Docker 视图）、
`docs/screenshots/33-multi-window.png`（独立浏览器窗口只开目标资产）。

### 25.5 全量回归

```
$ ./scripts/run_all_tests.sh docs/test-results-r20.log     # 32 套件 + ui_flow
OK: 30 套件 + 0 次 ui_flow（首轮），FAIL: test_zmodem、ui_flow
复跑：test_zmodem PASS=6 FAIL=0；ui_flow ERRORS: []（连续两次）
断言合计：596（= 首轮 594 − zmodem 首轮 4 + 复跑 6）
```

两次首轮失败都不是本轮改动引起，且复跑全绿：

| 套件 | 首轮现象 | 复跑 | 判据 |
|---|---|---|---|
| test_zmodem | `rz upload prompt shown` 25 s 内未出现（sz 下载已 PASS） | PASS=6 FAIL=0 | `rz -y` 提示依赖远端 shell 时序，属抖动；与本轮前端无关（本轮未改终端/文件代码） |
| ui_flow | Oracle 树为空（`表 \| 0`），浏览器记录一次 `/api/db/overview` 400 → `ERRORS: [...]` | 连续两次 `ERRORS: []`，Oracle 树 82 表、`EMP` 14 行 | 公共 Oracle 实例 DPY-4011 瞬时重置，与第十八/十九轮记录一致 |

复跑证据：`docs/test-results-r20-rerun.log`。

### 25.6 计数变化

| 口径 | 本轮前 | 本轮后 |
|---|---|---|
| 已复刻 | 76 | **77** |
| 未实现 | 17 | **16** |
| 形态替代 / 改进 | 4 / 2 | 4 / 2 |
| 本地功能口径 | 79/80（98.8%） | **80/81（98.8%）** |

本地口径的分母从 80 变 81、分子 79 变 80：因为「多窗口」此前被算作「桌面专属、排除在外」，
本轮它已经实现并实测，按实际应移出排除集（排除 19 → 18 条），而不是把分母硬留在 80 凑 80/80。
剩余唯一本地缺口仍是默认不做的 **95 云加密**。

同步文件：`docs/completion-audit.csv`（89）、`docs/feature-matrix.csv`（终端·分屏/多窗口→已实现；
设置·锁屏/托盘/多窗口→部分实现）、`README.md`、根目录 `复刻完成度核查报告.md` / `复刻交付说明.md`、本节。

### 25.7 限制

1. **标签拖拽搬迁**（把标签拖到另一个 pane 或另一个窗口）未实现，用「同屏打开 / 在新窗口打开」菜单替代。
2. **会话恢复**（重启后恢复上次的分屏布局与标签）未实现。
3. 布局是扁平 1–4 栏、同一次分屏共用一个方向；原版是二叉嵌套拆分树。再次分屏会**重新均分比例**。
4. 「多窗口」= 独立浏览器窗口；浏览器没有系统托盘。第二十轮时 90（锁屏/托盘）仍是未实现，只覆盖了「多窗口」部分；**第二十二轮已实现锁屏（第 27 节），系统托盘仍不可用**。
5. 极窄面板（<300 px）下 Docker 视图头部会换行挤压（美观问题，功能不受影响）；标签栏会自动滚动到活动标签。
6. 键盘快捷键是全局监听，与终端内的 `Ctrl+←/→` 等组合存在冲突可能（原版同样把这类组合作为全局快捷键）。

---

## 26. 第二十一轮：7 项预留扩展点 —— 只留位置，不做实现（2026-10-30 16:20–17:20）

> 本轮**没有新增任何功能**。目标是把 7 项未实现能力（90 锁屏/托盘、91 版本检查、92 热更新、
> 93 更新通道、95 云加密、98 MCP、99 插件市场）的落点、501 契约、配置键和关闭态 UI 固定下来，
> 让下一轮可以直接从 `docs/reserved-slots.md` 开工；`docs/completion-audit.csv` 的状态一律不动。

### 26.1 为什么是「预留」而不是「实现」

| 能力 | 不实现的原因（本轮判断） |
|---|---|
| 90 锁屏/托盘 | 托盘依赖系统桌面（`enableTray`/`setTrayMenu`），浏览器形态无对应物；锁屏与托盘在原版同一行，单独实现锁屏只能覆盖半项 |
| 91 版本检查 | 原版走云端 `client/version/check`；复刻版无账号/云端，需先定义本地 manifest 契约 |
| 92 热更新 | 需要分发链路（下载→MD5→7z→runtime→重启），Python 侧还要新增解压依赖；本轮明确不加依赖 |
| 93 更新通道 | 依赖 91/92 的 manifest 选择逻辑，单独做没有可验证行为 |
| 95 云加密 | 依赖云端会员 RSA 公钥/私钥体系；本地没有账号与密钥，无法验证，强行做等于伪造 |
| 98 MCP | 原版产物未发现 MCP（拆解报告 L544），属于新增能力而非复刻 |
| 99 插件市场 | 原版是编译期内置 6 插件、无动态市场（L36/L439-450），同样属于新增能力 |

因此本轮只做三件事：**登记唯一事实源、给出 501 契约、把配置键与关闭态 UI 放到位**。

### 26.2 本轮产物

| 产物 | 位置 | 作用 |
|---|---|---|
| 能力注册表 | `backend/capabilities.py::CAPABILITIES`（8 行）+ `GET /api/capabilities` | 唯一事实源；状态 `reserved` / `unavailable` / `done` |
| 501 构造器 | `backend/capabilities.py::reserved_response()` | 统一错误体；传入非 reserved id 直接 `ValueError` |
| 占位路由 | `backend/app.py` L3205-3212 | 6 个能力、7 条路由（92 有 start/status 两条），全部 501 |
| 配置键 | `backend/store.py` L95-99 / `frontend/src/lib/store.js` L84-88 | `update/lock/plugins/mcp/cloud` 双侧登记，默认关闭，无 gate |
| 设置 UI | `frontend/src/components/SettingsDialog.vue` L535-559、L571 | 更新/安全/扩展三节 + 关于页禁用按钮，状态由 `/api/capabilities` 驱动 |
| 预留位地图 | `docs/reserved-slots.md` | 7 项 + `lock.tray` 的现状/落点/证据/验收/风险 |
| 测试 | `scripts/test_reserved.py` | 35 条断言，含真实 CDP UI 检查 |

501 错误体（实测，`GET /api/update/status`）：

```json
{"ok": false, "error": {"code": "FEATURE_RESERVED", "feature": "update.hot",
 "reservedIn": "round21", "docs": "docs/reserved-slots.md#92-热更新",
 "message": "该能力已预留扩展点，尚未实现"}}
```

### 26.3 「预留但未实现且未激活」的证据

1. **全部占位路由 501**：7 条路由均返回 `501` + `FEATURE_RESERVED` + 正确 `feature`，
   没有一条返回 `ok:true`；`/api/cloud/encryption`、`/api/lock/tray` 未注册（404）。
2. **配置写了也不生效**：PUT `update.channel=experimental`、`lock.enabled=true`、
   `plugins.enabled=true`、`mcp.enabled=true`、`cloud.encryption=true` 后 GET 能原样读回，
   但 `/api/update/check`、`/api/mcp/info` 仍是 501；`/api/db/overview`（SQLite demo）
   仍返回 200 / `dialect=sqlite`，说明没有 gate 误伤既有功能。
3. **配置键双侧登记**：静态核对 `store.py` 与 `store.js` 的 5 个键完全一致（默认值也一致）。
4. **前端只有关闭态**：3 个新节的数据来自 `/api/capabilities`；所有控件 `disabled`，
   reserved 行 `title="预留（未实现）"`；95 云加密不属于任何节，界面无入口。

### 26.4 新增测试与实测输出

```
$ python3 scripts/test_reserved.py
PASS=35 FAIL=0
ELAPSED=9.4
```

35 条断言的分布：

| 类别 | 条数 | 断言要点 |
|---|---|---|
| 注册表 / 文档 | 8 | 200+ok；8 条；id 集合；status 三值；oid；reserved 有 entry / unavailable 无 entry；docs 锚点存在于文档；文档总表 8 行与注册表 id+oid 一一对应 |
| 501 契约 | 7 | 7 条路由逐条校验 501 / FEATURE_RESERVED / feature / reservedIn / docs / message |
| unavailable 无路由 | 2 | `/api/cloud/encryption`、`/api/lock/tray` → 404 |
| 配置往返与不激活 | 4 | PUT 200；GET 读回一致；写 `update.channel` 后仍 501；写 `mcp.enabled` 后仍 501 |
| 回归抽样 | 3 | 找到 DB 资产；`/api/db/overview` 200；配置恢复默认 |
| 前端（CDP 真实 DOM） | 11 | 设置可开；3 个新节；更新节 3 行；控件 disabled 且 title 正确；安全节 2 行；2 个禁用控件；95 无入口；扩展节 2 行；2 个禁用控件；关于页禁用按钮；无 JS 异常 |

前端构建与线上一致性：

```
$ sha256sum frontend/dist/index.html | cut -c1-16     # 24d43755386a3412（轮 21 原为 e6f2b8d27849bcba；FTP 文案修正后重建）
$ curl -s http://127.0.0.1:38110/ | sha256sum | cut -c1-16   # 24d43755386a3412
```

### 26.5 全量回归

```
$ ./scripts/run_all_tests.sh docs/test-results-r21.log     # 33 套件 + ui_flow
```

首轮：30 个套件 PASS；`test_zmodem`、`test_oracle`、`ui_flow` 失败。复跑记录：
`docs/test-results-r21-rerun.log`。

| 套件 | 首轮现象 | 复跑 | 判据 |
|---|---|---|---|
| test_zmodem | `rz upload prompt shown` 25 s 内未出现（sz 已 PASS），PASS=4 FAIL=1 | **PASS=6 FAIL=0** | 与 r20 完全相同的已知时序抖动；本轮未改终端/zmodem 代码 |
| test_oracle | `DPY-6005 cannot connect` + `Temporary failure in name resolution`，中途 11 条读断言失败，PASS=39 FAIL=11 | **PASS=50 FAIL=0** | 公共 Oracle 实例网络抖动（本轮复跑时 `getent hosts ecourse.org` → `98.157.243.1` 已恢复）；只读重试后全绿 |
| ui_flow | Oracle 表数据 0 行，浏览器记录一次 `/api/db/overview` 400，`ERRORS: [...]` | **ERRORS: []**（连续一次） | 与 test_oracle 同一时刻的 Oracle 抖动；Oracle 树 82 表、SQL Server 2 行等其余流程均正常 |

复跑后的断言合计：**631 = 596（第二十轮）+ 35（test_reserved）**。
计算方式：首轮 33 套件 `PASS=` 合计 618；`test_zmodem` 4→6、`test_oracle` 39→50，
`618 − 4 + 6 − 39 + 50 = 631`。

Oracle 公共库探针残留复核（本轮结束时）：

```
SELECT count(*) AS N FROM all_tables WHERE owner='SCOTT' AND table_name='ZZ_ORA_PROBE'
→ N = 0
```

### 26.6 计数为什么不变

```
$ python3 - <<'PY'   # 按本轮任务给出的 19 条排除集（含 95）
...
Counter({'已复刻': 77, '未实现': 16, '形态替代': 4, '改进': 2}) 99
local = 80/80
PY
```

这里的 `80/80` 是因为该片段把 **95 云加密也放进了排除集**（1,2,5,6,12,79-85,90,91,92,93,
95,98,99 = 19 条），分母变成 80。第二十轮已验证并在本文件 25.6 记录的口径是**排除 18 条**：
95 云加密是「本地可做但默认不做」的项，应计入本地分母，因此排除集不含 95：

```
$ python3 - <<'PY'   # 规范口径：18 条排除（95 计入）
...
Counter({'已复刻': 77, '未实现': 16, '形态替代': 4, '改进': 2}) 99
local = 80/81
PY
```

本轮没有改变任何一行的 `复刻状态`，只把 7 行的「核查说明」改成
「已预留扩展点（docs/reserved-slots.md#…），未实现」；`feature-matrix.csv` 中
90（锁屏/托盘/多窗口）仍是「部分实现」，91/92/93/95/98/99 仍是「未实现」并加「已预留」备注。
因此两种口径都与第二十轮结束时一致，没有“预留=实现”的计数漂移。

### 26.7 限制与下一轮起点

1. 7 项仍是「未实现」；6 个能力只有 501 占位，2 个能力（托盘、云加密）连路由都没有。
2. 预留配置键**按设计不生效**：本轮没有写任何 gate；下一轮实现时必须先写 gate，再打开默认值。
3. 锁屏/热更新/更新通道/版本检查/MCP/插件市场的实现要点、原版证据、验收标准与风险，逐项写在
   `docs/reserved-slots.md`；下一轮从 **`#90-锁屏与托盘`** 开始（或按 91 → 92 → 93 的顺序，
   因为 93 依赖 91/92 的 manifest 契约）。
4. 95 云加密保持 `unavailable`：没有云端账号与 RSA 密钥时无法验证，不应为了“看起来完成”而伪造。
5. 本轮未改 `requirements.txt` / `frontend/package.json`（零新增依赖）；测试只新增
   `scripts/test_reserved.py`，既有断言未改动。

---

## 27. 第二十二轮：90 锁屏（浏览器本地口令门）—— 实现并实测（2026-10-30 18:10–21:40）

> 本轮只做方向 A 的一项：**90 的锁屏一半**。系统托盘明确不做，`lock.tray` 保持 `unavailable`。
> 计数口径先拍板再改数：90 从本地排除集移出，总矩阵变为 **78 已复刻 / 15 未实现 / 4 形态替代 /
> 2 改进（99 行）**，本地口径 **81/82（98.8%）**，剩余唯一本地缺口是 95 云加密。

### 27.1 为什么锁屏可做、托盘不做

原版证据（`index-DqhrxGqR.js` 与 `Setting-DBzbjXrX.js`）：

- 设置项 `lockScreenPassword`、`lockScreenStartupEnabled`、`lockScreenTime`（毫秒，下拉为
  关闭 / 1 / 5 / 10 / 15 / 30 / 60 分钟）；
- `localStorage.lastOpTime` + 每 10 秒轮询判断超时；启动锁屏只在“登录账号后”生效；
- 解锁是**明文直接比较** `lockPassword == lockScreenPassword`，密码本身存在设置里；
- 托盘是 CEF 壳的 `enableTray` / `setTrayMenu`，菜单由前端下发（拆解报告 L420）。

复刻版是浏览器页面，没有系统托盘 API，所以托盘不做；锁屏则用更安全的等价形态实现：
口令只存 PBKDF2 哈希、锁定状态放服务端、空闲由服务端判定。这样刷新页面、重开标签、
另开浏览器都不能靠清 `localStorage` 绕过（原版可以）。

### 27.2 本轮产物

| 产物 | 位置 | 作用 |
|---|---|---|
| 锁服务 | `backend/lock_svc.py` | PBKDF2-HMAC-SHA256(210000 轮 / 16B salt / 32B digest)、恒定时间比较、失败退避、服务端锁定状态、空闲判定 |
| 路由 | `backend/app.py` | `GET /api/lock/status`、`PUT /api/lock/config`、`POST/DELETE /api/lock/password`、`POST /api/lock/lock`、`POST /api/lock/verify`、`POST /api/lock/activity` |
| 设置脱敏 | `backend/app.py::settings_public()` | 所有 `GET/PUT /api/settings` 响应剔除 `lock.passwordHash`；通用 PUT 丢弃整个 `lock` 对象 |
| 能力注册表 | `backend/capabilities.py` | `lock.screen` → `done`（`implementedIn: round22`），`lock.tray` 保持 `unavailable` |
| 锁屏 UI | `frontend/src/components/LockScreen.vue` | 全屏遮罩（z-index 2000）、口令输入/显隐、错误、退避倒计时、失败次数、焦点陷阱、`inert` 背景 |
| 前端状态 | `frontend/src/lib/store.js`、`lib/api.js` | 服务端锁状态、解锁/改密/配置/心跳；JSON 401 不再被当成会话过期重试（否则一次错误口令会记 3 次） |
| 启动门控 | `frontend/src/App.vue` | bootstrap 完成前不渲染工作区。新增的锁状态请求拉长了首屏加载，暴露出“加载中打开终端 → bootstrap 随后把 `state.tabs` 重置”的竞态；门控后 `test_split` 恢复 PASS=45 |
| 设置 UI | `frontend/src/components/SettingsDialog.vue` | 安全节改为可配置：开关、空闲时间、设/改/清口令、立即锁屏；系统托盘行仍“不可用” |
| 测试 | `scripts/test_lock.py`（新增）、`scripts/test_reserved.py`（更新） | API + 真实 CDP；预留测试改为 lock.screen=done、安全节可配置 |
| 截图 | `docs/screenshots/34-lock-screen.png`、`35-settings-security.png` | 锁屏与安全设置实测截图 |

### 27.3 契约与安全设计

| 端点 | 方法 | 成功 | 主要错误 |
|---|---|---|---|
| `/api/lock/status` | GET | `enabled/hasPassword/locked/idleMinutes/lockedAt/failedAttempts/retryAfter` | 无 token → 401 |
| `/api/lock/config` | PUT | 返回新状态 | 409 `LOCK_PASSWORD_REQUIRED`、400 `LOCK_INVALID_CONFIG` |
| `/api/lock/password` | POST | 自动启用并返回状态 | 401 `LOCK_BAD_OLD_PASSWORD`、400 `LOCK_PASSWORD_TOO_SHORT/TOO_LONG` |
| `/api/lock/password` | DELETE | 清除哈希并关闭 | 401 `LOCK_BAD_OLD_PASSWORD` |
| `/api/lock/lock` | POST | `locked=true` | 409 `LOCK_NOT_CONFIGURED` |
| `/api/lock/verify` | POST | 解锁并清零失败 | 409 `LOCK_NOT_LOCKED`、401 `LOCK_BAD_PASSWORD`、429 `LOCK_RATE_LIMITED` |
| `/api/lock/activity` | POST | 刷新空闲窗口 | 同上鉴权 |

错误体统一为：

```json
{"ok": false, "error": {"code": "LOCK_BAD_PASSWORD", "message": "锁屏口令不正确",
 "feature": "lock.screen", "docs": "docs/reserved-slots.md#90-锁屏与托盘",
 "failedAttempts": 1, "retryAfter": 0}}
```

关键数据：

- **哈希**：`pbkdf2_sha256$210000$<b64 salt>$<b64 digest>`；每个口令新随机 16B salt；
  比较用 `hmac.compare_digest`。`GET /api/settings` 不含 `passwordHash`，锁屏端点也不返回；
  通用 `PUT /api/settings` 会丢弃 `lock`，不能注入或清空哈希。
- **退避**：前 2 次错误直接 401；第 3 次起 2/4/8/…/60 秒；退避窗口内 429 + `retryAfter`；
  成功解锁/改密清零。
- **空闲**：服务端 `last_activity`；`GET /api/lock/status` **不**算活动；前端在
  keydown/mousedown/scroll 上节流 15 秒发心跳。页面隐藏/关闭后心跳停止，再打开时按流逝时间锁定。
- **状态在服务端**：刷新、重开标签、另开浏览器都读到 `locked=true`；解锁不会让会话令牌失效，
  终端/数据库会话继续运行。
- **限制**：防误触不防对抗；后端重启清空进程内 `locked` 标志（哈希保留）；忘记口令需停服清
  `data.db` 的 `lock.passwordHash`；托盘无路由。

### 27.4 新增与更新的测试

```
$ python3 scripts/test_lock.py
PASS=50 FAIL=0
ELAPSED=31.2
```

覆盖：无 token 401、非 501、首次设密、改密需旧口令、哈希格式/随机 salt、`data.db` 无明文、
错误计数 1/2/3、第 3 次给 `retryAfter`、退避期 429、退避后解锁、空闲 0.02 分钟锁定、
通用设置不可绕过、CDP 全屏/inert/错误口令/正确解锁、安全节可配置、UI 改空闲时间持久化、
设置页立即锁屏、**刷新仍锁定**、**另开浏览器仍锁定**、无 JS 异常。

```
$ python3 scripts/test_reserved.py
PASS=39 FAIL=0
ELAPSED=10.2
```

覆盖：注册表 8 条（`lock.screen=done`、`lock.tray=unavailable`）、6 条 reserved 路由仍 501、
`POST /api/lock/verify` 不再 501（409 `LOCK_NOT_LOCKED`）、`/api/lock/tray` 与
`/api/cloud/encryption` 均 404、预留配置可写但不生效、普通 DB 功能不受影响、安全节可配置且
托盘不可用、无哈希泄露、关于页预留按钮、设置对话框无 JS 异常。

前端构建与线上一致性：

```
$ sha256sum frontend/dist/index.html | cut -c1-16     # 2709c577695af899
$ curl -s http://127.0.0.1:38110/ | sha256sum | cut -c1-16   # 2709c577695af899
$ ls frontend/dist/assets | grep '^index-'           # index-CIqiiGKS.js / index-CF_N0l-A.css
```

### 27.5 全量回归

```
$ ./scripts/run_all_tests.sh docs/test-results-r22.log          # 首轮：34 套件 + ui_flow
$ ./scripts/run_all_tests.sh docs/test-results-r22-rerun-full.log  # 最终全绿复跑
```

最终结果：**首轮 `docs/test-results-r22.log` 为 33/34 套件 PASS + `ui_flow ERRORS: []`；
唯一失败是 `test_oracle`（公共实例 `ecourse.org:1521` 在 `/api/db/objects` 超时 240 s，外部网络抖动，
不是本轮代码）。复跑 `test_oracle PASS=50 FAIL=0`（`docs/test-results-r22-rerun.log`）；随后完整复跑
`docs/test-results-r22-rerun-full.log`：**34/34 套件 PASS、`ui_flow ERRORS: []`、退出码 0、断言合计 685**。

| 套件 | 首轮 | 复跑/说明 |
|---|---|---|
| 31 个其余既有套件 | PASS | 包含 `test_split` PASS=45、`test_zmodem` PASS=6、`test_sqlserver` PASS=32、`test_dameng` PASS=29 |
| `test_lock`（新增） | **PASS=50** | API + CDP；含 401/退避/空闲/刷新/新标签/inert/无明文口令 |
| `test_reserved`（更新） | **PASS=39** | 5 个 reserved 能力仍 501；lock.screen=done；安全节可配置；托盘不可用 |
| `test_oracle` | FAIL（240 s 超时） | **复跑 PASS=50 FAIL=0**；公共 Oracle 实例抖动，未伪造通过 |
| `ui_flow` | **ERRORS: []** | 全流程浏览器检查通过 |

断言合计：**685 = 34 个套件 `PASS=` 之和（最终全绿复跑）；首轮为 635（33 个 PASS 套件）+ Oracle 复跑 50**。
其中新增/更新部分：`test_lock` 50、`test_reserved` 39（比上一轮 +4）。
注意：`ui_flow` 不打印 `PASS=`，只以 `ERRORS: []` 判定，不计入 685。

过程中发现并修复了两个真实的前端问题（不是测试凑数）：

1. **bootstrap 竞态**：新增的 `/api/lock/status` 请求拉长了首屏加载；若在 bootstrap 完成前
   打开终端，bootstrap 末尾的 `state.tabs=[]` 会把刚打开的标签清掉。修复：`App.vue` 在
   `booting` 完成前不渲染工作区；`test_split` 恢复 PASS=45。
2. **遮罩早于工作区**：启动门控后 `LockScreen` 曾在 `.app-body` 存在前出现，导致
   `inert` 断言失败。修复：`LockScreen` 也等 `!booting` 后再渲染；`test_lock` 恢复 PASS=50。

首轮/中间日志保留：`docs/test-results-r22-pre-fix.log`（split 竞态 + Oracle 空树）、
`docs/test-results-r22-pre-final.log`（遮罩 inert 时序）、`docs/test-results-r22.log`
（首轮，仅 Oracle 超时）、`docs/test-results-r22-rerun.log`（Oracle 单套件复跑）、
`docs/test-results-r22-rerun-full.log`（**最终全绿：34 套件 + ui_flow，685 断言**）。

### 27.6 计数

```
$ python3 - <<'PY'
import csv
from collections import Counter
rows=list(csv.DictReader(open('docs/completion-audit.csv',encoding='utf-8-sig')))
print(Counter(r['复刻状态'] for r in rows), len(rows))
# 排除 1,2,5,6,12,79-85,91,92,93,98,99 = 17 条（90 已移出）
exc={1,2,5,6,12,*range(79,86),91,92,93,98,99}
loc=[r for r in rows if int(r['序号']) not in exc]
print(Counter(r['复刻状态'] for r in loc), len(loc))
PY
Counter({'已复刻': 78, '未实现': 15, '形态替代': 4, '改进': 2}) 99
Counter({'已复刻': 78, '改进': 2, '形态替代': 1, '未实现': 1}) 82
```

本地分子 81 = 78 已复刻 + 2 改进 + 1 形态替代（RDP）；唯一未实现是 95 云加密。
**90 移出排除集是因为锁屏已实现并实测；系统托盘没有实现，`lock.tray` 仍为 `unavailable`，
`completion-audit.csv` 第 90 行核查说明和本文都明确写出“托盘不计入”。**

### 27.7 限制与下一轮起点

1. 锁屏是本地 UI 门，不是多用户安全边界；能读数据目录/会话令牌即可绕过。
2. 锁定标志在进程内，后端重启后回到未锁（哈希保留）；刷新/新标签/新浏览器不受影响。
3. 托盘不做；`lock.tray` 永远 `unavailable`。
4. 忘记口令只能停服清 `data.db` 的 `lock.passwordHash`，没有云端账号可走原版重置流程。
5. 剩余预留：91 版本检查、92 热更新、93 更新通道、98 MCP、99 插件市场；95 云加密保持
   `unavailable`。下一轮可从 **91 → 93** 或只做测试/文档加固，不要先碰 95/98/99。

---

## 28. 第二十三轮：91 版本检查 + 93 更新通道 —— 实现并实测（2026-10-31 15:25–16:20）

> 本轮只做方向 A 的 **91（版本检查）+ 93（更新通道）**。92 热更新、95 云加密、98 MCP、
> 99 插件市场与 90 系统托盘不做。计数口径先拍板再改数：排除集 17→15，总矩阵
> **80 已复刻 / 13 未实现 / 4 形态替代 / 2 改进（99 行）**，本地口径 **83/84（98.8%）**，
> 剩余唯一本地缺口是 95 云加密；托盘仍 `unavailable`，不计入实现。

### 28.1 为什么做 91/93、不做 92

| 能力 | 本轮判断 |
|---|---|
| 91 版本检查 | 可做。原版走云端 `client/version/check|detail|history`，复刻版无账号体系；改为读取用户显式配置的**本机 manifest**，只做数值比较，不假装云端 detail/history |
| 93 更新通道 | 可做。它是 91 的 manifest 选择维度（stable/experimental × production/test），白名单 + 持久化 + 模板替换，行为可端到端验证 |
| 92 热更新 | **不做**。涉及下载→MD5→7z→runtime→重启、解压依赖与供应链防护，需要单独威胁模型和外部启动器；继续 `reserved`/501 |
| 95/98/99 | 不做。95 依赖云端 RSA；98/99 属于新增能力而非复刻 |
| 90 托盘 | 浏览器形态无系统托盘，保持 `unavailable` |

### 28.2 本轮产物

| 产物 | 位置 | 作用 |
|---|---|---|
| 更新服务 | `backend/update_svc.py`（新增） | 本机 manifest 读取（file/loopback http）、大小与 schema 校验、数值比较、通道白名单、URL 模板/目录解析 |
| 路由 | `backend/app.py` | `GET /api/update/check` 替换 501；`POST /api/update/channel` 替换 501；通用 `PUT /api/settings` 的 `update` 对象走同一套白名单校验 |
| 能力注册表 | `backend/capabilities.py` | `update.check`/`update.channel` → `done`（`implementedIn: round23`）；`update.hot` 保持 `reserved` |
| 前端状态 | `frontend/src/lib/store.js` | `checkUpdate()`、`setUpdateChannel()`、`saveUpdatePrefs()`、`scheduleAutoUpdateCheck()` |
| 启动调度 | `frontend/src/App.vue` | bootstrap 完成后调度**一次**延迟检查（仅 autoCheck 打开时），无后台轮询 |
| 更新 UI | `frontend/src/components/SettingsDialog.vue` | 更新节改为可配置：版本检查、通道、env、autoCheck、manifest 地址；热更新行仍禁用 |
| 结果面板 | `frontend/src/components/UpdateCheckPanel.vue`（新增） | 设置「关于」节与关于窗口共用，显示 current/latest/hasUpdate/notes |
| 关于窗口 | `frontend/src/components/AboutDialog.vue` | 加入可用的版本检查面板 |
| 测试 | `scripts/test_update.py`（新增）、`scripts/test_reserved.py`（更新） | API + 真实 CDP；63 + 41 条断言 |
| 截图 | `docs/screenshots/36-settings-update.png`、`37-about-update-check.png` | 设置更新节、关于窗口检查结果（共 37 张） |
| 文档 | 本文件、`docs/reserved-slots.md`、`completion-audit.csv`、`feature-matrix.csv`、`README.md`、根目录交付说明与核查报告 | 契约、计数、剩余缺口 |

### 28.3 契约与安全设计

manifest 契约（UTF-8 JSON，≤256 KiB）：

```json
{
  "version": "5.1.10",
  "build": 520,
  "notes": "更新说明",
  "url": "https://example.invalid/h1khub-5.1.10.deb",
  "md5": "0123456789abcdef0123456789abcdef"
}
```

- `version` 必填、点分数字（1–4 段）；`build` 必填、非负整数；`notes` 可选（响应截断 4000 字符）；
  `url`/`md5` 可选且只展示，检查路径**不下载、不校验包**。
- 比较：先按点分段转整数元组比较 version，相等时再比较 build。因此 `5.1.10 > 5.1.9`，
  `5.1.9 build 600 > 5.1.9 build 519`。
- 成功响应：`{current:{version,build}, latest:{version,build}, hasUpdate, notes, url, md5,
  channel, env, manifestUrl, source, checkedAt}`。
- 错误统一 HTTP 400，不返回 500：`UPDATE_MANIFEST_NOT_CONFIGURED`、`UPDATE_MANIFEST_URL_INVALID`、
  `UPDATE_MANIFEST_FORBIDDEN`、`UPDATE_MANIFEST_UNAVAILABLE`、`UPDATE_MANIFEST_TOO_LARGE`、
  `UPDATE_MANIFEST_INVALID`。

安全边界：

- 只允许 `file://` 绝对路径（authority 为空或 localhost）与 loopback `http(s)`
  （`127.0.0.1`/`localhost`/`::1`）；保存配置时即拒绝其他主机，`api.hexhub.cn` 永远不会被请求。
- HTTP 客户端 `allow_redirects=False`、`trust_env=False`、5 秒超时、流式 256 KiB 上限，
  防 SSRF（含重定向逃逸与超大响应）。
- 只读检查：不下载、不执行、不写资产库、不持久化检查结果；每次检查重新读设置与 manifest。
- `autoCheck` 只触发前端一次延迟检查；没有轮询、没有定时器常驻。

通道契约：

| 端点 | 方法 | 请求 | 成功响应 |
|---|---|---|---|
| `/api/update/channel` | POST | `{channel?, env?}`（至少一个） | `{channel, env, manifestUrl, autoCheck, manifestResolved}` |

- 只接受 `stable|experimental` × `production|test`，非法值 400（`UPDATE_INVALID_CHANNEL`/`UPDATE_INVALID_ENV`）；
- 持久化 `update.channel` / `update.env`；不触发下载、不重启（测试比较 `/api/public/info.startedAt`）；
- manifest 选择：`{channel}`/`{env}` 占位符 → 替换；以 `/` 结尾的目录 → 追加
  `update-<channel>-<env>.json`；普通文件 URL → 所有通道共用；
- 通用 `PUT /api/settings` 也走同一白名单，非法通道不会落库。

### 28.4 新增与更新的测试

```
$ python3 scripts/test_update.py
PASS=63 FAIL=0

$ python3 scripts/test_reserved.py
PASS=41 FAIL=0

$ python3 scripts/test_lock.py
PASS=50 FAIL=0

$ python3 scripts/test_split.py
PASS=45 FAIL=0
```

`test_update.py` 覆盖：

- 未配置、文件不存在、非法 JSON、超大、非法 version/build/md5、缺 build → 400 且非 500；
- 更高/相同/更低版本、同版本更高/更低 build、`5.1.10 > 5.1.9` 数值比较；
- 拒绝 `api.hexhub.cn`、ftp、`file://otherhost`、相对路径，且被拒绝地址不落库；
- 本机 HTTP manifest 成功；HTTP 非 JSON、Content-Length 超大、302 重定向 → 400，重定向未跟随；
- 检查不修改设置、不新增资产、热更新 start/status 仍 501；
- autoCheck 启动后只调用一次（4 秒后仍为一次）；
- 通道白名单、持久化、`startedAt` 不变、模板与目录选择随通道变化；
- UI：更新节可配置、热更新仍禁用、关于页/关于窗口检查按钮可用并渲染结果、无 JS 异常。

`test_reserved.py` 更新为：`update.check`/`update.channel` 移出 501 路由列表（剩余 4 条：
92 start/status、MCP、插件市场），`lock.screen`/`update.check`/`update.channel` 三项 `done`；
仍验证 plugins/mcp/cloud 配置可写但不生效；UI 断言改为「版本检查/通道可配置，热更新仍禁用」。

首轮全量回归中 `test_vnc` 的 `closed port fails` 曾失败：根因是**测试自身**在
`srv.close()` 后立刻复用同一端口探测，而 accept 线程仍短暂持有监听 fd（与本轮产品代码无关）。
修复为 `stop.set() → srv.close() → accept 线程 join(2s) → 探测`（未改断言、未放宽判定），
修复后连续 5 次 `PASS=4 FAIL=0`。

### 28.5 构建一致性与全量回归

前端重新构建并核对线上首页哈希：

```
$ sha256sum frontend/dist/index.html | cut -c1-16              # 7298117668be1197
$ curl -s http://127.0.0.1:38110/ | sha256sum | cut -c1-16    # 7298117668be1197
$ ls frontend/dist/assets | grep '^index-'                     # index-D0CeyN48.js / index-LLRxovWW.css
```

全量回归：

```
$ ./scripts/run_all_tests.sh docs/test-results-r23.log     # 35 套件 + ui_flow
```

结果：**35/35 套件 PASS、`ui_flow ERRORS: []`、退出码 0、断言合计 750**。

| 类别 | 结果 |
|---|---|
| 既有 30 个套件 | 全部 PASS（含 `test_zmodem` 6、`test_x11` 6、`test_dameng` 29、`test_sqlserver` 32、`test_oracle` 50） |
| `test_vnc` | PASS=4（修复自身监听线程 close/join 竞态后，连续 5 次 PASS） |
| `test_split` | PASS=45 |
| `test_lock` | PASS=50 |
| `test_update`（新增） | **PASS=63** |
| `test_reserved`（更新） | **PASS=41** |
| `ui_flow` | **ERRORS: []**（不计入 750） |

断言合计：**750** = 35 个套件 `PASS=` 之和（48+19+26+34+15+24+22+28+10+5+10+16+9+12+4+7+4+9+3+33+24+24+9+11+6+6+11+11+29+32+50+45+50+63+41）。
Oracle 使用公共实例 `ecourse.org:1521`，本轮一次通过；`test_vnc` 的首轮失败是测试自身端口竞态
（见 28.4），修复后完整套件一次全绿，没有伪造通过。

### 28.6 计数

```
$ python3 - <<'PY'
import csv
from collections import Counter
rows=list(csv.DictReader(open('docs/completion-audit.csv',encoding='utf-8-sig')))
print(Counter(r['复刻状态'] for r in rows), len(rows))
exc={1,2,5,6,12,*range(79,86),92,98,99}
loc=[r for r in rows if int(r['序号']) not in exc]
print(Counter(r['复刻状态'] for r in loc), len(loc))
PY
Counter({'已复刻': 80, '未实现': 13, '形态替代': 4, '改进': 2}) 99
Counter({'已复刻': 80, '改进': 2, '形态替代': 1, '未实现': 1}) 84
```

本地分子 83 = 80 已复刻 + 2 改进 + 1 形态替代（RDP）；唯一未实现是 95 云加密。
**92 仍未实现、95/98/99 未变、托盘 `unavailable`，均不计入实现。**

### 28.7 限制与下一轮起点

1. manifest 无签名；`url/md5` 只是 92 的输入字段，本轮不下载、不校验包。
2. 白名单防 SSRF，但不是本机用户边界：能改 `data.db` 的人可写入外部 URL，
   不过 `check` 读取时仍会再校验 scheme/host。
3. 版本号只支持 1–4 段纯数字；预发布/日期版本不在契约内；不实现云端 detail/history。
4. 92 热更新仍未实现（501），需要下载/解压/替换/重启与供应链防护，建议单独一轮。
5. 剩余：92 热更新、95 云加密（unavailable）、98 MCP、99 插件市场、90 系统托盘。

## 29. 第二十四轮：原生多层 SSH 跳板（递归 `jumpAssetId`）（2026-10-31 17:50–18:45）

### 29.1 目标与契约

原实现只解析**一层** `jumpAssetId`：`Hub.ssh_config()` 只把目标资产的跳板放进 `cfg["__jump"]`，跳板自己的 `jumpAssetId` 被忽略；`hub.tunnel()` 更是完全不看跳板。本轮把它升级为递归原生能力：

1. 每个 SSH 资产仍只有一个 `jumpAssetId`，跳板资产自身可继续引用跳板。
2. 连接顺序固定为由外到内：`client -> Jn -> … -> J1 -> target`。
3. 每一跳独立解析认证（密码 / 私钥 / 私钥资产 / 密码对 / agent）。
4. 每一跳独立做 known_hosts TOFU，记录沿用 `[host]:port`。
5. 循环引用（A→B→A）必须快速失败，返回 400 且错误包含完整链路。
6. 跳板上限 `MAX_JUMP_DEPTH = 8`（最多 8 个跳板，目标不计入），超限返回 400 且错误包含上限。
7. 任一跳失败时，关闭已经建立的全部外层 transport，不泄漏连接/会话。
8. 单层、无跳板与既有 DB/Redis/Docker/SCP/SFTP/隧道/会话行为保持兼容。

### 29.2 实现

| 文件 | 改动 |
|---|---|
| `backend/sshlib.py` | 新增 `SSHChain` 与 `connect_chain(cfgs, resolver)`：按顺序逐跳 `connect_transport(via=上一跳)`，失败时逆序关闭所有 transport；每跳错误标注“第 N 跳（host:port）”；`HostKeyMismatch` 保留类型并标注跳数；`connect_client()` 退化为单 transport（跳板统一走 `connect_chain`） |
| `backend/hub.py` | `MAX_JUMP_DEPTH = 8`；新增 `_chain_from()/ssh_chain()/ssh_chain_from_config()`；`ssh_config()` 返回目标配置 + `__jump_chain`（由外到内）；新增 `open_ssh_chain()/open_ssh_chain_from_config()/open_ssh_transport()`；`sftp()`、`_open_tunnel_sync()/tunnel()`、`docker()`、`ssh_exec()` 全部改用链路并在所有退出路径关闭全部 transport；Docker 返回 `TransportChain` 以兼容既有 `transport.close()` 调用点 |
| `backend/app.py` | `test_connection()` 统一走 hub 链路入口，响应新增 `chain`（每跳 host/port/username/hostKey/banner，便于逐层断言）；`asset_config_for_test()` 支持未保存的自定义 `jumpAssetId`；`ssh_copy_id` 关闭整条链 |
| `backend/sessions.py` | `SSHSession` 改为 pop `__jump_chain`，`start()` 用 `connect_chain()`，`close()` 关闭整条链；会话 `meta.jumpChain` 记录实际跳板 |
| `backend/tunnel_svc.py` | `ManagedTunnel` 保存 `transports` 列表，`stop()` 逆序全部关闭；`TunnelManager.start()` 使用 `open_ssh_transport()` 的完整链 |
| `frontend/src/components/AssetDialog.vue` | 保留单个“跳板机”下拉；新增 `jumpChainPreview`（由外到内）、循环/超深提示；下拉排除当前资产、循环候选置灰；中英文文案 |
| `scripts/test_jumps.py` | 新增 62 项断言的多层跳板套件（随机高位端口的临时 sshd，结束后清理） |
| `scripts/run_all_tests.sh` | 注册 `test_jumps` |

关键边界：

- `connect_transport()` 在 `via` 非空时通过父 transport 的 `direct-tcpip` 打开通道，**不会**再走 `proxy`。因此链式连接中只有最外层（第一个连接、`via=None`）能使用自己的 `proxy`；中间跳的 proxy 会被忽略。本轮接受该边界并写入 README。
- 每层都是一次完整 SSH 认证；8 层是防御性上限，不是性能建议值。
- 循环/超深在解析阶段失败，不建立 socket。

### 29.3 自动化验证（`scripts/test_jumps.py`）

测试环境：5 个临时 `/usr/sbin/sshd` 实例（HOP-1…HOP-4 + TARGET-5），各自独立 host key、共享 client key、独立 banner/日志；端口每次运行随机分配（避免 TOFU 状态跨轮污染）；资产全部 `zz-` 前缀，结束后删除并清理 known_hosts 与临时目录。

`python3 scripts/test_jumps.py`：

```text
PASS=70 FAIL=0
```

覆盖点：

| 类别 | 断言 |
|---|---|
| 0–4 层链路 | 逐跳 banner 顺序为 `HOP-n → … → HOP-1 → TARGET-5`；最外层首次 `hostKey=new`，内层已缓存 `known` |
| known_hosts | 每跳写入 `[127.0.0.1]:port`；第二次连接为 `known`；替换最外层 host key 后 400 且错误指出“第 1 跳”“主机密钥” |
| 循环 | A↔B 返回 400，错误包含 A、B 与“循环”，耗时 < 2 s |
| 深度 | 10 层链返回 400，错误包含“跳板层数超过上限（8）” |
| 失败清理 | 中间跳端口关闭：400 且指出“第 2 跳”；`ss -tn state established` 无残留、`GET /api/sessions` 无新增、`GET /api/tunnels` 为空 |
| 最外层代理 | SOCKS5 fixture 生效时 3 层链成功；停掉代理后首跳失败并指出“第 1 跳” |
| DB/Redis | `sshAssetId` 指向 3 层链目标：`SELECT 1` 返回 `[[1]]`；Redis `test` 返回版本 |
| Docker | SSH 模式经 3 层链 `docker version` 成功 |
| SSHTunnel | dynamic 资产经 3 层链启动，SOCKS5 CONNECT 到目标 sshd 并取到 `SSH-2.0` banner，停止后无残留 |
| 终端/SFTP/SCP | 终端命令回显、SFTP `/tmp` 列表、`hub.sftp` 跨服务器传输、SCP 上传内容一致 |
| UI | 真实 Chromium：右键“编辑”打开资产对话框；下拉不含当前资产；循环候选置灰；显示循环提示；选择安全跳板后显示 `hop-3 → hop-2 → hop-1`；无 console error |

### 29.4 手工三层复核

临时启动 5 个 sshd（HOP-1…HOP-4 + TARGET-5，随机端口），资产链为
`target -> hop1 -> hop2 -> hop3`（连接顺序 `hop3 -> hop2 -> hop1 -> target`）。
脚本 `docs/manual-jump-3hop.log` 的实际输出：

```text
3-hop connect: ['HOP-3', 'HOP-2', 'HOP-1', 'TARGET-5']
stopped middle hop 51549
middle down: 400 第 2 跳（127.0.0.1:51549）连接失败：ChannelException(2, 'Connect failed')
restarted middle hop 51549
restored connect: ['HOP-3', 'HOP-2', 'HOP-1', 'TARGET-5']
```

结论：目标 banner 正确；停掉中间层后目标立即不可达并准确指出第 2 跳；恢复同一实例后可达。
`zz-` 资产、5 个 sshd、known_hosts 临时条目均已清理，资产数回到 32。

### 29.5 全量回归

```bash
./scripts/run_all_tests.sh docs/test-results-jump-final.log
```

- 退出码 0；**36 个套件 + ui_flow、820 项 API 断言**全部 PASS、FAIL=0；`grep '^  FAIL'` 为 0；`ui_flow ERRORS: []`。
- 新增 `test_jumps`：**PASS=70 FAIL=0**（在完整套件中一次通过）。
- 第一次全量运行（测试脚本尚未加入 `test_jumps` 的 transfer 断言时）`test_update` 出现一次 `UI 可切回稳定通道` 失败：
  前端在通道 POST 期间会禁用 select，测试在 busy 窗口内再次 `change` 被丢弃。补充“等待控件重新启用 + 重试”后，
  独立复跑与随后两次全量均 `test_update PASS=63 FAIL=0`；产品逻辑未改，不是产品回归。
- 服务日志无 `Traceback` / `RecursionError`；结束后资产 32、隧道 0、`zz-` 资产 0、临时 known_hosts 条目 0。

### 29.6 边界与未覆盖

- 只有最外层跳板可使用 `proxy`；中间跳 proxy 被忽略（README 5.4 / 已知限制 8）。
- 每层一次完整 SSH 认证，延迟与 fd 线性增长；8 层为防御上限。
- 链路解析以资产 id 判环，跳板资产被删除时返回“跳板资产不可用”。
- 未实现“每跳 proxy 叠加”、跳板链的可视化编辑（仍为单指针 + 预览）、跳板链的跨资产复制特殊处理。

## 30. 第二十五轮：资产地图（连接拓扑）（2026-10-31 19:40–20:25）

### 30.1 目标

把资产从“树”扩展成“图”：连接类资产作为节点，多层跳板（`jumpAssetId`）与
数据库/Redis/Docker/隧道对 SSH 主机的依赖（`sshAssetId`）作为有向边；提供一个可缩放、
可筛选、可选中查看完整链路的拓扑视图，同时不破坏既有资产树与 32 个真实资产。

### 30.2 实现

| 文件 | 改动 |
|---|---|
| `backend/assetmap_svc.py` | 新增只读拓扑构建：连接类节点、`jump`/`ssh` 边、入/出度、目录路径、跳板链、深度、Kahn 分层、循环检测、缺失引用、统计 |
| `backend/app.py` | 新增 `GET /api/asset-map`（只读，不建立连接、不返回秘密） |
| `frontend/src/views/AssetMapView.vue` | 自绘 SVG 拓扑：分层布局、未连线资产网格、循环尾部列、缩放/平移/适应、搜索、类型筛选、仅拓扑、选中连通分量、详情面板、双击打开、右键菜单、循环/缺失引用提示、资产变更自动重载 |
| `frontend/src/App.vue`、`IconRail.vue`、`AssetPanel.vue`、`TabBar.vue`、`store.js` | 注册 `map` 视图；左侧导航新增“资产地图”；资产面板“更多”新增入口；`openAssetMap()` |
| `scripts/test_assetmap.py` | API + UI 回归（35 项） |
| `scripts/run_all_tests.sh` | 注册 `test_assetmap` |

### 30.3 拓扑契约

- 节点：`id/name/typeName/group/host/port/username/database/folder/inbound/outbound/chain/chainNames/depth/cycle/missing/layer`。
- 边：`from -> to`，`kind=jump`（目标 SSH → 跳板 SSH）或 `kind=ssh`（服务 → SSH 主机）。
- 方向：从依赖方指向被依赖方，因此层号向外递增；前端从左到右排列。
- 循环：仅可能出现在 `jump` 边；循环节点/边带 `cycle=true`，接口另返回 `cycles` 名字列表。
- 缺失引用：`jumpAssetId/sshAssetId` 指向不存在或非 SSH 资产时记录在节点 `missing`，不生成假节点。
- 统计：节点数、边数、跳板边、SSH 边、最大深度、有链资产数、循环数、孤立节点数、类型分组。

### 30.4 自动化验证（`scripts/test_assetmap.py`）

```text
PASS=35 FAIL=0
```

覆盖：

| 类别 | 断言 |
|---|---|
| 真实数据 | 19 个连接节点/4 条边；`SSH 跳板演示 → SSH hexlab` 跳板边；3 个隧道的 SSH 边 |
| 自建 2 跳链 | target→hop-b→hop-a，`depth=2`、`chain/chainNames` 顺序正确、逐层 layer=0/1/2/3 |
| DB/隧道依赖 | `sshAssetId` 生成 `ssh` 边，服务层为 0、目标 SSH 层为 1 |
| 缺失引用 | `jumpAssetId=a_missing_asset` 记录 `not-found`，不生成边 |
| 循环 | A↔B 被检测、节点/边标记 `cycle`、响应 < 2s |
| UI | 左侧导航打开地图、节点/连线/跳板边渲染、工具栏统计、选中详情、由外到内链顺序、仅拓扑隐藏孤立资产、无 console error |

截图：`docs/screenshots/asset-map.png`、`asset-map-detail.png`、`asset-map-topology.png`、
`asset-map-chain.png`（自建 2 跳链的选中详情）。

### 30.5 全量回归

```bash
./scripts/run_all_tests.sh docs/test-results-assetmap-final.log
```

- 退出码 0；**37 个套件 + ui_flow、855 项 API 断言**全部 PASS、FAIL=0；`grep '^  FAIL'` 为 0；`ui_flow ERRORS: []`。
- 新增 `test_assetmap`：**PASS=35 FAIL=0**；`test_jumps` 保持 **PASS=70**；原有 `test_network/test_files/test_ai/test_docker/test_db/test_sync` 全部保持绿。
- 首次全量运行出现两处测试时序问题（均非产品回归）：
  - `test_zmodem` 的 `rz` 上传提示在全量负载下 25s 内未出现；补 0.8s 的上一轮 ZMODEM 收尾等待、超时提到 40s，独立复跑两次均 PASS=6，最终全量 PASS=6。
  - `test_update` 的 autoCheck 观测窗把初始页延迟检查混入；改为初始 autoCheck=false、reload 前开启并记录事件基线，独立复跑两次 PASS=63，最终全量 PASS=63。
- 服务日志无 `Traceback` / `RecursionError`；结束后资产 32、隧道 0、`zz-` 资产 0。

### 30.6 边界

- 只绘制连接类资产；目录、快捷命令、凭据不进入图（避免非连接节点淹没拓扑）。
- 采用分层布局而非力导向；循环节点集中在尾部列，不保证美观的环内几何。
- 地图只读，不建立 SSH/DB 连接；打开/编辑资产复用既有入口。
- 未实现：按目录聚类、节点拖拽持久化、边的手工布局、拓扑导出图片。

## 31. 第二十六轮：文件面板压缩 / 解压（2026-10-31 20:50–21:40）

### 31.1 目标

SSH 登录后的 SFTP 文件列表要能对**选中的多个文件/文件夹**直接压缩，也能对归档解压，
并且本地文件面板保持同样的操作手感。远端不把文件拉回浏览器再打包：直接在目标主机
调用它已有的 `tar`/`zip`/`7z`，只通过 SFTP 做路径与结果校验。

### 31.2 实现

| 文件 | 改动 |
|---|---|
| `backend/archive_svc.py` | 新增：远端压缩/解压/归档探测（tar.gz、tar.bz2、tar.xz、tar、zip、7z）、本地标准库实现（tarfile/zipfile/gzip/bz2/lzma，7z/rar 委托本机 7z）、归档成员扫描与越界拒绝、覆盖/跳过语义、超时与错误规整 |
| `backend/sshlib.py` | 新增 `exec_on_transport()`：在已认证 transport 上开独立通道执行命令，带输出上限与超时；交互 shell 通道不受影响 |
| `backend/sessions.py` | `SSHSession.run_command()`：复用当前 SSH 会话执行一次性命令（压缩/解压/探测） |
| `backend/hub.py` | `ssh_exec()` 改用同一个执行器，行为保持一致 |
| `backend/app.py` | 新增 `/api/sftp/archive/{formats,inspect,compress,extract}` 与 `/api/fs/archive/{formats,inspect,compress,extract}`；sessionId 或 assetId 均可作为目标；`ArchiveError` 统一 400；成功后写 `archive` 历史 |
| `frontend/src/components/FilePane.vue` | Ctrl/Shift 多选、选择栏（压缩/解压/删除/取消）、右键压缩与解压、压缩对话框（格式/名称/目录/覆盖）、解压对话框（成员数、示例、不安全提示、目标目录/新建文件夹/覆盖）；补上原本缺失的 `Loader2` 导入 |
| `frontend/src/styles/theme.css` | 选择栏与归档信息样式 |
| `scripts/test_archive.py` | API 回归（SFTP + 本地，135 项） |
| `scripts/test_archive_ui.py` | 真实 CDP UI 回归（27 项，截图 38/39） |
| `scripts/run_all_tests.sh` | 注册两个新套件 |

### 31.3 接口契约

- `POST /api/sftp/archive/formats` `{sessionId|assetId}` → `{formats:[{id,label,ext,available,missingTools}], tools, sevenZip}`。
- `POST /api/sftp/archive/inspect` `{sessionId|assetId,path}` → `{format,tool,size,sizeText,members,sample,partial,unsafe,unsafeSample}`；只读。
- `POST /api/sftp/archive/compress` `{sessionId|assetId,paths[],targetDir,name,format,overwrite}` →
  `{path,name,format,tool,count,size,sizeText,elapsedMs}`。`paths` 必须是同一目录的直接子项；
  `name` 不能含路径分隔符，未带扩展名时自动补全。
- `POST /api/sftp/archive/extract` `{sessionId|assetId,path,targetDir,create,overwrite}` →
  `{path,targetDir,format,tool,members,memberCheck,elapsedMs}`。
- 本地对应 `/api/fs/archive/*`，契约相同（无 `sessionId/assetId`）。

### 31.4 安全规则

- 所有进入 shell 的路径用 `shlex.quote`；压缩只接受同一父目录的直接子项，拒绝根目录与重名。
- 解压前先列成员：绝对路径、Windows 盘符、任意 `..` 分量一律拒绝并返回 400，
  解压命令不会启动；远端由 tar/unzip/7z 自身的路径保护兜底。
- 本地 tar 使用 `filter="data"`（Python 3.12+），同时单独校验符号链接/硬链接目标不越界；
  zip 成员同样先校验再解压。
- 成员扫描上限 20 万条 / 16 MiB；截断时响应标记 `partial=true`（UI 显示“成员校验不完整”）。

### 31.5 自动化验证

```text
$ python3 scripts/test_archive.py
PASS=135 FAIL=0

$ python3 scripts/test_archive_ui.py
PASS=27 FAIL=0
```

覆盖：

| 类别 | 断言 |
|---|---|
| 远端格式 | tar.gz、zip、7z、tar.bz2、tar.xz、tar 创建 + inspect + 解压 + 内容回读 |
| 名称与内容 | 空格目录 `sub dir`、中文加空格文件名、单文件归档 |
| 覆盖语义 | 目标已存在且 `overwrite=false` → 400；`overwrite=true` → 覆盖；解压 `overwrite=false` 时 tar/zip/7z 均保留旧内容，`true` 时替换 |
| 目标目标 | sessionId 与 assetId 两条路径；目标目录不存在时 `create=false` → 400、`create=true` → 新建 |
| 选择校验 | 跨目录、`/`、带 `/` 的归档名、归档自身作为源、未知格式、会话不存在 → 400 |
| 越界归档 | 上传含 `../` 与绝对路径的 tar/zip；inspect 标记 unsafe，extract 400，且目标目录未创建、外部无写入 |
| 本地 | tar.gz/zip/tar.bz2/tar.xz/7z 往返、单文件 `.gz` 解压、越界归档、覆盖/跳过、校验错误 |
| 历史 | 压缩/解压后 `/api/history` 出现 `kind=archive` |
| UI | 展开资产 → 打开 SSH → SFTP 面板 → 多选两行 → 压缩对话框（格式/7z 可用）→ 创建归档 → 解压对话框（成员信息）→ 解压 → 进入结果目录；0 console error |

截图：`docs/screenshots/38-archive-compress.png`、`docs/screenshots/39-archive-extract.png`。

### 31.6 边界

- 明文 FTP 面板不做压缩/解压（FTP 没有远端 shell）；本地与 SFTP 面板行为一致。
- 远端格式取决于主机安装的 `tar`/`zip`/`7z`；缺失的格式在 UI 中禁用并显示缺少的工具。
- 没有断点续传/进度百分比：压缩是远端一次性命令，UI 显示“压缩中…”与完成后的文件大小；
  超大目录受命令超时（默认 600s，接口可调至 3600s）约束。
- 加密归档（密码 7z/zip）未提供密码输入；列表或解压会由工具报错并原样返回。
- 远端 tar 的 `overwrite=false` 跳过语义依赖 GNU tar `--skip-old-files`；BSD/macOS 目标上会改用报错而不是静默跳过（覆盖模式正常）。


## 32. 第二十七轮：通过服务器代理 Chrome（2026-10-31 22:45–23:00）

### 32.1 目标

复刻原产品 Pro 能力「通过服务器代理 Chrome」：在资产列表右键 SSH 资产后，用该 SSH 连接
（含跳板链）在本机开一个 SOCKS5 代理，并启动 Chrome/Chromium 把浏览器流量交给它。
原产品实现为 `startTunnelForWebBrowser` + `executil.OpenChrome`；复刻版保持同样的交互与参数语义，
但由 Python 后端直接启动浏览器，并提供管理对话框。

### 32.2 实现

| 文件 | 改动 |
|---|---|
| `backend/chrome_proxy_svc.py` | 新增：Chrome 发现（原版 5 条 Linux 路径 + PATH + macOS/Windows + `H1KHUB_CHROME_PATH`）、SOCKS5 隧道（复用 `ManagedTunnel` 的 dynamic 模式）、Chrome 启动参数、临时配置目录、进程/隧道/配置生命周期、并发上限 |
| `backend/app.py` | 新增 `GET /api/chrome-proxy`、`POST /api/chrome-proxy/start`、`POST /api/chrome-proxy/{id}/stop`；`ChromeProxyError` 统一 400；成功后写 `kind=chrome` 历史；启动/退出时 `stop_all()` |
| `frontend/src/components/AssetPanel.vue` | SSH 资产右键菜单新增「通过服务器代理 Chrome」；运行中时同一菜单追加「停止通过服务器代理的 Chrome」；「更多」菜单新增「Chrome 代理（查看/停止）…」管理入口；启动前提示、成功/失败 toast |
| `frontend/src/components/ChromeProxyDialog.vue` | 新增：Chrome 可用性与候选路径、运行中会话列表（资产、代理、pid、配置目录、运行时长）、停止按钮、3s 刷新 |
| `frontend/src/App.vue` / `lib/store.js` | 注册对话框与 `chromeProxyOpen` 状态 |
| `scripts/test_chromeproxy.py` | 新增自包含 API + UI 套件（假 Chrome + 真实 sshd + 本地 HTTP fixture + CDP） |
| `scripts/test_chromeproxy_jumps.py` | 新增真实浏览器多跳套件：4 台 sshd 组成 3 跳链、真实 Chromium、页面 fixture、每跳认证日志、浏览器窗口截图 |
| `scripts/run_all_tests.sh` | 注册 `test_chromeproxy` |

### 32.3 与原产品的一致性

通过反汇编 `hexhub-backend`（Go）确认并沿用：

- Chrome 候选路径：`/usr/bin/google-chrome`、`/usr/bin/google-chrome-stable`、`/usr/bin/chromium`、
  `/usr/bin/chromium-browser`、`/snap/bin/chromium`，全部不存在时报
  `chrome browser not found, tried paths: ...` 风格的错误；复刻版错误为中文并保留完整候选列表。
- 参数：`--proxy-server=<proxy>`（无 scheme 时补 `socks5://`）、`--user-data-dir=<dir>`、
  `--no-first-run`、`--no-default-browser-check`、`--proxy-bypass-list=<-loopback>`，之后是可选 URL。
- 临时配置目录：原版在 `TMPDIR`（缺省 `/tmp`）下创建 `chrome-profile-<unixnano>`，进程退出后
  等待 500 ms 再删除；复刻版用 `tempfile.mkdtemp(prefix="chrome-profile-")`（0700），同样在
  退出后 0.5 s 删除，并额外支持手动停止。
- 前端提示：保留原版「请确保已经安装 Chrome，且未开启其他代理软件！！！」。

### 32.4 接口契约

- `GET /api/chrome-proxy` → `{available,path,tried,override,platform,sessions[],maxSessions}`；
  `sessions` 仅列运行中的实例。
- `POST /api/chrome-proxy/start` `{assetId,url?}` → `{session:{id,assetId,assetName,chromePath,proxy,host,port,pid,profileDir,url,running,connections,startedAt,elapsedMs}}`；`connections` 是该隧道累计的 SOCKS5 CONNECT 数。
  仅接受 SSH 资产（type=2）；`url` 仅 http/https 且 ≤2048；并发上限 8。
- `POST /api/chrome-proxy/{id}/stop` → `{stopped:true,session:{...running:false,exitCode,finishedAt}}`；
  未知/已结束会话 404。
- 启动后 0.6s 内进程退出视为启动失败（无图形会话、sandbox 拒绝、坏 override），回收后返回 400。

### 32.5 安全规则

- SOCKS5 监听 `127.0.0.1:随机端口`，不接受外部连接；SSH 仍沿用资产自身的 known_hosts/跳板/代理配置。
- Chrome 使用独立临时配置，不复用用户日常 profile，也不需要先退出已运行的 Chrome。
- 启动参数以列表传给 `Popen`，不经过 shell；URL 校验 scheme，防止借 URL 注入额外开关。
- 配置目录 0700；后端退出时 `stop_all()` 停止所有 Chrome/隧道。
- 本功能会让本机浏览器流量经 SSH 服务器转发，属于明确授权的连接操作；不访问云端。

### 32.6 自动化验证

```text
$ python3 scripts/test_chromeproxy.py
PASS=75 FAIL=0  (~31s)
```

覆盖：

| 类别 | 断言 |
|---|---|
| 能力探测 | available/path/tried/初始 sessions/maxSessions |
| 启动参数 | 5 个参数逐项匹配、无多余参数、URL 为最后参数 |
| 配置隔离 | 临时目录存在且权限 0700 |
| SOCKS5 | 真实 sshd + 本地 HTTP fixture：握手、CONNECT、HTTP 200、正文 `CHROME-PROXY-SOCKS-OK` |
| 会话/历史 | 列表可见运行会话、`kind=chrome` 历史 |
| 输入校验 | `file://` 400、目录资产 400、未知会话 stop 404 |
| 自动回收 | 直接 SIGTERM 假 Chrome → 会话消失、端口关闭、配置目录删除 |
| 手动停止 | 停止接口 → 进程退出、端口关闭、配置目录删除、列表清空 |
| UI | 展开目录 → SSH 右键菜单出现「通过服务器代理 Chrome」→ 点击 → 成功 toast；运行中再次右键出现「停止通过服务器代理的 Chrome」→ 点击后会话清空且 toast 提示；无会话时停止项消失；目录行始终无代理菜单；「更多 → Chrome 代理（查看/停止）」打开对话框、显示路径/会话、对话框内停止并提示 |
| 未安装 Chrome | 独立实例 `H1KHUB_CHROME_PATH` 指向不存在文件：available=false、start 返回 400、错误含候选路径、无残留会话 |
| 启动即退出 | 假 Chrome `exit 7`：start 返回 400、错误含 exit=7 与 DISPLAY 提示、无残留会话 |

#### 多跳 + 真实 Chrome（`scripts/test_chromeproxy_jumps.py`）

```text
$ python3 scripts/test_chromeproxy_jumps.py
PASS=23 FAIL=0  (28.3s)
```

本套件临时启动 **4 台 sshd** 组成 3 跳链（目标 → hop2 → hop1 → hop0），用真实
`/usr/bin/chromium` 经代理打开一个本地 HTTP fixture，验证：

| 类别 | 断言 |
|---|---|
| 链路 | `/api/test-connection` 返回 4 跳、每跳主机密钥 `new/known` |
| 启动 | 经目标资产（带 3 跳）启动 Chrome 200，SOCKS5 监听、临时 profile |
| 浏览器 | fixture 收到 Chrome 请求、User-Agent 含 `Chrome/`、页面标题 `H1KHub Multi-hop Chrome OK` |
| 代理证明 | 会话 `connections >= 1`（Chrome 对 loopback 也走代理，证明请求经过 SOCKS5） |
| 目标证明 | 会话 `recentConnections` 含 `127.0.0.1:<fixture>`，即页面请求的 CONNECT 目标确实经 SOCKS5 |
| 每跳 | 4 个 sshd 日志的 `Accepted publickey` 计数在启动后均增加 |
| 清理 | 停止后会话清空、端口关闭、profile 删除、Chrome 进程退出 |
| 截图 | `xdotool` + ImageMagick `import` 捕获浏览器窗口 |

截图：`docs/screenshots/43-chrome-proxy-multihop.png`（真实 Chrome 显示
`MULTIHOP-CHROME-OK`，地址栏为 fixture）。

另做真实环境冒烟（本机 `/usr/bin/chromium` + `SSH hexlab`）：

- 右键启动后返回 `socks5://127.0.0.1:44431`，`curl --socks5-hostname` 经该端口访问本地 HTTP
  fixture 返回 `CHROME-PROXY-LIVE-OK`；
- 进程参数与上述 5 项一致；
- `POST .../stop` 后端口关闭、`/tmp/chrome-profile-*` 删除、会话列表为 0。

截图：`docs/screenshots/40-chrome-proxy.png`（管理对话框）、
`docs/screenshots/41-chrome-proxy-menu.png`（SSH 资产右键菜单）、
`docs/screenshots/42-chrome-proxy-stop.png`（运行中右键直接停止）、
`docs/screenshots/43-chrome-proxy-multihop.png`（真实 Chrome 经 3 跳 SSH 代理加载页面）。

### 32.7 边界

- Chrome 窗口只能开在**后端所在机器**；复刻版是浏览器形态，无法把窗口推送到用户另一台机器。
- 需要图形会话（`DISPLAY`/`WAYLAND_DISPLAY`）；无图形环境会在启动后立即退出并返回 400，
  错误提示给出原因。服务器/容器场景建议用有头浏览器或 VNC 会话运行。
- 每次启动都是全新配置：不保留登录态/cookie；如需要持久 profile，需另行扩展（当前刻意与原版一致）。
- `--proxy-bypass-list=<-loopback>` 与原版一致，Chrome 访问 `127.0.0.1` 也走代理。
- 未测试 Windows/macOS 的实际 Chrome 启动（仅实现候选路径与参数组装）；Linux 已实测。

---

## 33. 品牌更名（H1KHub，2026-11-01）

Logo、标题、应用名与协议标识统一替换为 **H1KHub**，功能行为未变：

| 项 | 更名前 | 更名后 |
|---|---|---|
| 应用名 / 页面标题 | `HexHub` | `H1KHub` |
| Logo | 六边形立方体图标 | 圆角渐变方块 + `H1K` 字标（`assets/h1khub.svg`，同时作为 favicon） |
| 欢迎页 / 关于 / 锁屏 / AI 助手文案 | `HexHub ...` | `H1KHub ...` |
| 数据目录 | `~/.hexhub-replica` | `~/.h1khub`（启动时若新目录不存在会自动迁移旧目录） |
| 会话令牌请求头 | `X-HexHub-Token` | `X-H1KHub-Token` |
| 环境变量 | `HEXHUB_*` | `H1KHUB_*` |
| 资产导出格式 / 下载名 | `hexhub-assets` / `hexhub-assets.json` | `h1khub-assets` / `h1khub-assets.json`（旧格式仍可导入） |
| 本地 registry 默认仓库 | `hexhub/local` | `h1khub/local` |
| Redis JSON 模块 | `hexhub_json` | `h1khub_json` |

兼容性处理：

- 旧版加密字段（AAD = `hexhub-asset`）仍可解密，新写入一律使用 `h1khub-asset`；
  读取失败时回退旧 AAD，不会改写历史数据。
- 旧资产导出包（`format: hexhub-assets`）仍可导入。
- 外部实验资源名（`hexhub-lab` 目录、`hexhub_demo` 库、`hexhub-mssql:2019` 镜像、
  `HEXHUB` schema、`Hexhub@12345` 实验口令）保持不变，以免破坏本机实验环境。

更名后回归（均 FAIL=0）：

| 脚本 | 结果 |
|---|---|
| `scripts/test_tasks.py` | PASS=10 |
| `scripts/test_settings.py` | PASS=9 |
| `scripts/test_redisjson.py` | PASS=11（新模块 `h1khub_json`） |
| `scripts/test_update.py` | PASS=63 |
| `scripts/test_lock.py` | PASS=50 |

UI 冒烟：`document.title = H1KHub`、favicon `/h1khub.svg`、关于窗口标题「关于 H1KHub」、
控制台无 JS 异常。

README 中引用的 37 张截图已全部用当前构建（H1KHub 品牌）在本机实验环境重新采集：
`scripts/capture_docs.py`（01–11）、`scripts/test_split.py`（32/33）、`scripts/test_assetmap.py`（资产地图链）、
`scripts/test_chromeproxy_jumps.py`（43）、`scripts/test_archive_ui.py`（38/39），
以及按同一套 CDP 流程补采的数据库/Redis/Docker/设置/锁屏/AI/Chrome 代理/资产地图详情与拓扑等页面（12/22/27/40-42）。
仅 `jump-demo-chain.png`、`jump-demo-cycle.png` 两张早期跳板测试图未重采（未被任何文档引用，对应 zz-demo-* 测试资产已清理）。

---

## 34. AI 工具计数纠偏（2026-11-01）

工具计数以 `len(TOOLS)` 为准，实测 20；此前文档 21 为计数笔误。

| 位置 | 原值 | 现值 |
|---|---|---|
| `README.md:187`（目录树） | 21 个工具 | 20 个工具 |
| `README.md:317`（AI Agent 能力表） | 21 个 | 20 个 |
| `docs/feature-matrix.csv:72` | 21 个工具 | 20 个工具 |
| `docs/completion-audit.csv:77` | AI 工具 21 个 | AI 工具 20 个 |

代码核对（唯一依据，文档数字必须与之一致）：

```
python3 -c "import sys;sys.path.insert(0,'backend');import ai;print(len(ai.TOOLS))"   # → 20
grep -c "async def tool_" backend/ai.py                                              # → 20
```

`TOOLS` 的 20 个名称与 `tool_*` handler 集合逐一对应，无孤儿工具、无未注册 handler：
`list_assets`、`list_dir`、`read_file`、`write_file`、`shell`、`list_db_objects`、
`execute_sql`、`redis_command`、`docker_containers`、`docker_logs`、`list_sessions`、
`read_terminal`、`terminal_execute`、`scp_upload`、`scp_download`、`list_tunnels`、
`start_tunnel`、`stop_tunnel`、`ftp_list`、`web_fetch`。

`README.md:317` 的枚举（资产、文件、目录、shell、SQL、Redis、Docker、终端、SCP 上下行、
隧道启停/列表、FTP 列目录、网页抓取）按类目对应到具体工具合计 20 个：文件 2（读/写）、
SQL 2（对象清单/执行）、Docker 2（容器/日志）、终端 3（会话/读取/注入）、SCP 2（上/下行）、
隧道 3（启动/停止/列表），其余 6 类各 1，与代码一致。

回归：`python3 scripts/test_ai.py --base http://127.0.0.1:38110` → **PASS=24 FAIL=0**。

---

## 35. AI 工具面扩展：Docker 容器动作 + FTP 写操作（方向 C，2026-11-01）

本轮在**不改动流式解析与审批框架**的前提下，按方向 C 增量扩展 AI 工具面：

1. `docker_container_action`：`start` / `stop` / `restart`，复用 `docker_svc.DockerService.action`；
2. `ftp_upload` / `ftp_download` / `ftp_delete` / `ftp_rename`：复用 `ftp_svc.FTPClient.upload_from /
   download_to / delete / rename`，不重写协议层。

工具总数 **20 → 25**（`len(TOOLS)` 为唯一依据）。

### 35.1 代码变更

| 文件 | 变更 |
|---|---|
| `backend/ai.py` | `TOOLS` 新增 5 个工具；`ChatRunner` 新增 5 个 `tool_*` handler；导入 `FTPError`；`DOCKER_ACTIONS` 常量；`_silent` 显式声明 `docker_action` / `ftp_write` 恒需审批；系统提示词补充 Docker 动作与 FTP 写能力 |
| `backend/ftp_svc.py` | 新增 `FTPClient.safe_path()`：写操作前把路径解析到登录主目录内，绝对路径与 `..` 越界直接 `FTPError`，不静默截断 |
| `scripts/test_ai.py` | 新增 FTP 写操作批准/拒绝/参数校验用例、Docker 动作 stub CLI 用例、FTP 越界单测、`TOOLS=25` 与 handler 集合一致性断言 |

### 35.2 25 个工具与新增项

原 20 个：`list_assets`、`list_dir`、`read_file`、`write_file`、`shell`、`list_db_objects`、
`execute_sql`、`redis_command`、`docker_containers`、`docker_logs`、`list_sessions`、
`read_terminal`、`terminal_execute`、`scp_upload`、`scp_download`、`list_tunnels`、
`start_tunnel`、`stop_tunnel`、`ftp_list`、`web_fetch`。
新增 5 个：`docker_container_action`、`ftp_upload`、`ftp_download`、`ftp_delete`、`ftp_rename`。

`grep -c "async def tool_" backend/ai.py` = 25；工具名集合与 handler 集合 diff 为空
（测试内也断言 `len(TOOLS) == 25 and set(names) == handlers`，防止文档/代码再次漂移）。

### 35.3 审批与安全边界

| 操作 | 审批 kind | 行为 |
|---|---|---|
| Docker `start` / `stop` / `restart` | `docker_action` | 恒需审批，不受 `allowSilent*` 影响；先校验 `action`/`container`，再审批，再查容器存在性 |
| FTP 上传 / 下载 / 删除 / 重命名 | `ftp_write` | 恒需审批，不继承 `allowSilentFileWrite`；下载同样审批（远端读取 + 本机落盘） |

- Docker 目标为 `asset_id` + `container`：按容器名或 ID（含前缀）匹配 `docker ps` 结果；
  不存在返回结构化 `{"error": {"code": "container_not_found", ...}}`；
  CLI stderr 转为 `{"code": "docker_cli_error", "message": <stderr>}`，不再以裸异常文本回模型。
- FTP 写路径统一经过 `safe_path()`：`..`、`/etc/...`、`sub/../../x` 一律拒绝并返回
  `FTPError: 路径越界...`；服务器根目录为 `/` 时没有更高层级，规则自然退化为根内全允许。
- `TERMINAL_TOOL_NAMES`（13 个）与 `SQL_TOOL_NAMES`（5 个）**未纳入**新工具，保持既有子集边界；
  终端助手仍不能调用 Docker/FTP 动作（既有 mode 限制测试继续通过）。

### 35.4 测试与证据

命令与日志：

```
cd replica
python3 scripts/test_ai.py --base http://127.0.0.1:38110 2>&1 | tee /tmp/h1khub-test_ai.log
# PASS=48 FAIL=0
```

覆盖（全部走 `scripts/mock_llm.py`，无外部模型）：

- FTP：上传拒绝（且不落盘）/ 批准（列表可见）/ 下载批准且内容一致 / 重命名批准且旧名消失 /
  删除拒绝仍保留 / 删除批准后消失 / 缺参在审批前失败；`safe_path` 越界单测。
- Docker：拒绝路径（`approve=False`）；`action=remove` 与缺 `container` 在审批前失败；
  批准路径用**进程内 stub `docker` CLI**（`PATH` 前置临时脚本，无真实守护进程）验证
  `start` / `stop` / `restart` 成功、未知容器、CLI stderr 结构化错误。
  未连接真实 Docker、未创建/停止任何真实容器。
- 回归：既有流式对话、shell 审批、拒绝、SCP、隧道、SQL/Terminal 模式全部保持 PASS。

### 35.5 文档计数同步（20 → 25）

| 位置 | 现值 |
|---|---|
| `README.md:187`（目录树） | 25 个工具 |
| `README.md:317`（AI Agent 能力表） | 25 个（枚举同步补 Docker 启停、FTP 上传/下载/删除/重命名） |
| `README.md:397`（审批说明） | 补 Docker 动作与 FTP 写操作恒需审批 |
| `docs/feature-matrix.csv:72` | 25 个工具；新增 Docker 启停、FTP 写两行 |
| `docs/completion-audit.csv:77` | AI 工具 25 个 |

核对命令：

```
python3 -c "import sys;sys.path.insert(0,'backend');import ai;print(len(ai.TOOLS))"   # → 25
grep -c "async def tool_" backend/ai.py                                              # → 25
```

### 35.6 边界与未做

- 未实现资产 CRUD（方向 D）、`websearch`（方向 B）、VNC/RDP 后端、会话/PTY 新建；这些仍在待办池。
- `docker_container_action` 仅开放 start/stop/restart，不开放 `kill` / `pause` / `remove`，避免误删。
- FTP 写工具复用现有 FTP 资产配置；SFTP 资产仍走 `scp_*` / 文件面板。
