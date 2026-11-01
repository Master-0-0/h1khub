# 拆解所得 protobuf schema（只读抓取）

来源：本机原版 HexHub 5.1.9（`http://127.0.0.1:38103/proto/<name>.proto`，只读 GET，未修改/停止原版）。

| 文件 | 用途 |
|---|---|
| `table_data.proto` | `TableDataResponse`（查询/表数据），复刻版 SQL 查询的 protobuf 回复 |
| `ssh.proto` | `FilesResponse`（SFTP 文件列表） |
| `sql_gen.proto` | `SqlGenReq`（生成 INSERT/UPDATE 语句） |
| `executor_data.proto` | 执行器数据 |
| `redis_scan.proto` | Redis SCAN 结果 |

复刻版后端在 `/proto/{name}` 原样提供这些文件（与原版一致），前端 `src/lib/packet.js`
内置 `TableDataResponse` 解码器；后端 `backend/rpcproto.py` 负责编码。

限制：原版 RPC 的 `ChannelOpen` 外层在复刻版用 JSON `{method,args}`（同时兼容原版嵌套
Packet），并非对原版全部 RPC 方法的逐字节复刻。
