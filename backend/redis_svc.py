"""Redis explorer service."""
from __future__ import annotations

import base64
import json
import shlex
from typing import Any

import redis

from util import Timer, human_bytes, jsonable


class RedisError(Exception):
    pass


def _dec(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return {"__type": "binary", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, list):
        return [_dec(v) for v in value]
    if isinstance(value, dict):
        return {str(_dec(k)): _dec(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_dec(v) for v in value]
    return value


class RedisService:
    def __init__(self, cfg: dict, tunnel: tuple[str, int] | None = None) -> None:
        self.cfg = cfg
        host = cfg.get("host") or "127.0.0.1"
        port = int(cfg.get("port") or 6379)
        if tunnel:
            host, port = tunnel
        self.cluster = bool(cfg.get("cluster"))
        self.nodes = str(cfg.get("nodes") or f"{host}:{port}")
        if self.cluster:
            from redis.cluster import ClusterNode, RedisCluster

            startup = []
            for token in self.nodes.replace(";", ",").split(","):
                token = token.strip()
                if not token:
                    continue
                node_host, _, node_port = token.rpartition(":")
                startup.append(ClusterNode(node_host or "127.0.0.1", int(node_port or 6379)))
            if not startup:
                raise RedisError("集群节点为空")
            self.client = RedisCluster(
                startup_nodes=startup,
                username=cfg.get("username") or None,
                password=cfg.get("password") or None,
                ssl=bool(cfg.get("tls")),
                decode_responses=False,
                socket_timeout=10,
                socket_connect_timeout=10,
                protocol=2,
            )
        else:
            self.client = redis.Redis(
                host=host,
                port=port,
                username=cfg.get("username") or None,
                password=cfg.get("password") or None,
                db=int(cfg.get("db") or 0),
                ssl=bool(cfg.get("tls")),
                decode_responses=False,
                socket_timeout=10,
                socket_connect_timeout=10,
                protocol=2,
            )

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ info
    def test(self) -> dict:
        timer = Timer()
        try:
            pong = self.client.ping()
            info = self.client.info("server")
            return {
                "ok": bool(pong),
                "version": info.get("redis_version", ""),
                "mode": info.get("redis_mode", "standalone"),
                "elapsed": timer.ms(),
            }
        except redis.AuthenticationError as exc:
            raise RedisError(f"Redis 认证失败：{exc}") from exc
        except Exception as exc:
            raise RedisError(f"Redis 连接失败：{exc}") from exc

    def databases(self) -> list[dict]:
        if self.cluster:
            try:
                keys = int(self.client.dbsize())
            except Exception:
                keys = 0
            return [{"name": "db0", "index": 0, "keys": keys, "expires": 0, "cluster": True, "nodes": self.nodes}]
        try:
            info = self.client.info("keyspace")
        except Exception as exc:
            raise RedisError(f"读取 Redis keyspace 失败：{exc}") from exc
        out = []
        seen = set()
        for key, value in info.items():
            if not key.startswith("db"):
                continue
            idx = int(key[2:])
            seen.add(idx)
            out.append(
                {
                    "name": key,
                    "index": idx,
                    "keys": int(value.get("keys", 0)),
                    "expires": int(value.get("expires", 0)),
                }
            )
        if not out:
            out = [{"name": "db0", "index": 0, "keys": 0, "expires": 0}]
        return sorted(out, key=lambda d: d["index"])

    def scan(self, db: int, cursor: str = "0", pattern: str = "*", count: int = 200) -> dict:
        if self.cluster and int(db or 0) != 0:
            raise RedisError("Redis 集群仅支持 db0")
        try:
            self.client.connection_pool.connection_kwargs["db"] = db
        except Exception:
            pass
        if self.cluster:
            import itertools

            collected = list(itertools.islice(self.client.scan_iter(match=pattern or "*", count=count), 500))
            items = []
            pipe = self.client.pipeline(transaction=False)
            for key in collected:
                pipe.type(key)
                pipe.ttl(key)
            try:
                replies = pipe.execute() if collected else []
            except Exception:
                replies = []
            for i, key in enumerate(collected):
                items.append(
                    {
                        "key": _dec(key),
                        "type": _dec(replies[i * 2]) if len(replies) > i * 2 else "unknown",
                        "ttl": int(replies[i * 2 + 1]) if len(replies) > i * 2 + 1 else -1,
                    }
                )
            items.sort(key=lambda x: str(x["key"]).lower())
            return {"cursor": "0", "keys": items, "count": len(items), "cluster": True}
        try:
            cur, keys = self.client.scan(cursor=int(cursor or 0), match=pattern or "*", count=count)
        except Exception as exc:
            raise RedisError(f"SCAN 失败：{exc}") from exc
        items = []
        pipe = self.client.pipeline(transaction=False)
        for key in keys[:500]:
            pipe.type(key)
            pipe.ttl(key)
        try:
            replies = pipe.execute() if keys else []
        except Exception:
            replies = []
        for i, key in enumerate(keys[:500]):
            ktype = replies[i * 2] if len(replies) > i * 2 else "unknown"
            ttl = replies[i * 2 + 1] if len(replies) > i * 2 + 1 else -1
            items.append(
                {
                    "key": _dec(key),
                    "type": _dec(ktype),
                    "ttl": int(ttl) if isinstance(ttl, int) else -1,
                }
            )
        items.sort(key=lambda x: str(x["key"]).lower())
        return {"cursor": str(cur), "keys": items, "count": len(items)}

    def key_info(self, db: int, key: str) -> dict:
        self._select(db)
        pipe = self.client.pipeline(transaction=False)
        pipe.type(key)
        pipe.ttl(key)
        pipe.memory_usage(key)
        try:
            ktype, ttl, mem = pipe.execute()
        except Exception as exc:
            raise RedisError(f"读取键信息失败：{exc}") from exc
        encoding = "unknown"
        try:
            raw_encoding = self.client.execute_command("OBJECT", "ENCODING", key)
            encoding = _dec(raw_encoding) or "unknown"
        except Exception:
            pass
        ktype = _dec(ktype)
        size = 0
        try:
            size = self.client.strlen(key) if ktype == "string" else self.client.dbsize()
        except Exception:
            pass
        return {
            "key": key,
            "type": ktype,
            "ttl": ttl,
            "memory": mem or 0,
            "memoryText": human_bytes(mem or 0),
            "encoding": encoding,
            "size": size,
        }

    def value(self, db: int, key: str) -> dict:
        self._select(db)
        ktype = _dec(self.client.type(key))
        data: Any = None
        if ktype == "string":
            raw = self.client.get(key)
            data = _dec(raw)
            if isinstance(data, str) and len(data) > 2_000_000:
                data = data[:2_000_000] + "… (truncated)"
        elif ktype == "hash":
            data = [{"field": _dec(k), "value": _dec(v)} for k, v in self.client.hgetall(key).items()]
        elif ktype == "list":
            vals = self.client.lrange(key, 0, 1999)
            data = [{"index": i, "value": _dec(v)} for i, v in enumerate(vals)]
        elif ktype == "set":
            data = [_dec(v) for v in list(self.client.smembers(key))[:2000]]
        elif ktype == "zset":
            vals = self.client.zrange(key, 0, 1999, withscores=True)
            data = [{"member": _dec(m), "score": s} for m, s in vals]
        elif ktype == "stream":
            entries = self.client.xrange(key, min="-", max="+", count=50)
            data = [{"id": _dec(i), "fields": {str(_dec(k)): _dec(v) for k, v in fields.items()}} for i, fields in entries]
        elif ktype.lower() in ("rejson-rl", "rejson"):
            try:
                raw = _dec(self.client.execute_command("JSON.GET", key))
                data = json.loads(raw) if raw else None
            except Exception as exc:
                raise RedisError(f"读取 JSON 值失败：{exc}") from exc
        elif ktype == "none":
            raise RedisError("键不存在")
        else:
            try:
                data = _dec(self.client.execute_command("DUMP", key))
            except Exception:
                data = None
        return {"key": key, "type": ktype, "value": data, "ttl": int(self.client.ttl(key))}

    def set_string(self, db: int, key: str, value: str, ttl: int | None = None) -> None:
        self._select(db)
        self.client.set(key, value)
        if ttl and int(ttl) > 0:
            self.client.expire(key, int(ttl))

    def set_json(self, db: int, key: str, value: str) -> None:
        """Write a whole JSON document through JSON.SET (RedisJSON module)."""
        self._select(db)
        try:
            json.loads(value)
        except Exception as exc:
            raise RedisError(f"JSON 格式无效：{exc}") from exc
        try:
            self.client.execute_command("JSON.SET", key, "$", value)
        except Exception as exc:
            raise RedisError(f"JSON.SET 失败（需要 RedisJSON 模块）：{exc}") from exc

    def hash_set(self, db: int, key: str, field: str, value: str) -> None:
        self._select(db)
        self.client.hset(key, field, value)

    def hash_del(self, db: int, key: str, field: str) -> None:
        self._select(db)
        self.client.hdel(key, field)

    def list_push(self, db: int, key: str, value: str, tail: bool = True) -> None:
        self._select(db)
        (self.client.rpush if tail else self.client.lpush)(key, value)

    def list_set(self, db: int, key: str, index: int, value: str) -> None:
        self._select(db)
        self.client.lset(key, index, value)

    def list_remove(self, db: int, key: str, index: int) -> None:
        self._select(db)
        marker = "__h1khub_tmp__"
        self.client.lset(key, index, marker)
        self.client.lrem(key, 1, marker)

    def set_add(self, db: int, key: str, member: str) -> None:
        self._select(db)
        self.client.sadd(key, member)

    def set_remove(self, db: int, key: str, member: str) -> None:
        self._select(db)
        self.client.srem(key, member)

    def zset_add(self, db: int, key: str, member: str, score: float) -> None:
        self._select(db)
        self.client.zadd(key, {member: float(score)})

    def zset_remove(self, db: int, key: str, member: str) -> None:
        self._select(db)
        self.client.zrem(key, member)

    def _clone_key(self, key: str, target: str) -> None:
        ktype = _dec(self.client.type(key))
        ttl = int(self.client.ttl(key) or -1)
        if ktype == "string":
            self.client.set(target, self.client.get(key))
        elif ktype == "hash":
            mapping = self.client.hgetall(key)
            if mapping:
                self.client.hset(target, mapping=mapping)
        elif ktype == "list":
            values = self.client.lrange(key, 0, -1)
            if values:
                self.client.rpush(target, *values)
        elif ktype == "set":
            members = self.client.smembers(key)
            if members:
                self.client.sadd(target, *members)
        elif ktype == "zset":
            rows = self.client.zrange(key, 0, -1, withscores=True)
            if rows:
                self.client.zadd(target, dict(rows))
        else:
            raise RedisError(f"集群模式不支持 {ktype} 类型重命名")
        if ttl > 0:
            self.client.expire(target, ttl)

    def rename(self, db: int, key: str, new_key: str) -> None:
        self._select(db)
        if self.cluster:
            self._clone_key(key, new_key)
            self.client.delete(key)
            return
        self.client.rename(key, new_key)

    def delete(self, db: int, keys: list[str]) -> int:
        self._select(db)
        if not keys:
            return 0
        if self.cluster:
            total = 0
            for key in keys:
                try:
                    total += int(self.client.delete(key))
                except Exception:
                    continue
            return total
        return int(self.client.delete(*keys))

    # ------------------------------------------------------------ batch ops
    def scan_keys(self, db: int, pattern: str = "*", limit: int = 1000) -> list[str]:
        self._select(db)
        found: list[str] = []
        cursor = 0
        while True:
            cursor, batch = self.client.scan(cursor=cursor, match=pattern or "*", count=500)
            found.extend(_dec(item) for item in batch)
            if not cursor or len(found) >= limit:
                break
        return found[:limit]

    def batch_delete(self, db: int, keys: list[str]) -> dict:
        self._select(db)
        unique = [k for k in dict.fromkeys(keys) if k]
        if not unique:
            raise RedisError("未选择任何键")
        if self.cluster:
            deleted = 0
            for key in unique:
                try:
                    deleted += int(self.client.delete(key))
                except Exception:
                    continue
        else:
            deleted = int(self.client.delete(*unique))
        return {"deleted": deleted, "requested": len(unique)}

    def batch_rename(self, db: int, keys: list[str], rule: dict) -> dict:
        self._select(db)
        mode = (rule.get("mode") or "prefix").lower()
        value = str(rule.get("value") or "")
        find = str(rule.get("find") or "")
        replace = str(rule.get("replace") or "")
        results = []
        for key in dict.fromkeys([k for k in keys if k]):
            if mode == "prefix":
                target = value + key
            elif mode == "suffix":
                target = key + value
            elif mode == "replace":
                if not find:
                    results.append({"key": key, "ok": False, "error": "缺少查找字符串"})
                    continue
                target = key.replace(find, replace)
            elif mode == "strip":
                target = key[len(value):] if value and key.startswith(value) else key
            else:
                results.append({"key": key, "ok": False, "error": f"未知重命名方式：{mode}"})
                continue
            if not target or target == key:
                results.append({"key": key, "ok": False, "error": "名称未变化"})
                continue
            try:
                if self.client.exists(target):
                    results.append({"key": key, "target": target, "ok": False, "error": "目标键已存在"})
                    continue
                if self.cluster:
                    self._clone_key(key, target)
                    self.client.delete(key)
                else:
                    self.client.rename(key, target)
                results.append({"key": key, "target": target, "ok": True})
            except Exception as exc:
                results.append({"key": key, "ok": False, "error": str(exc)})
        return {
            "renamed": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "results": results,
        }

    def batch_set(self, db: int, keys: list[str], value: str, ttl: int | None = None) -> dict:
        self._select(db)
        results = []
        for key in dict.fromkeys([k for k in keys if k]):
            ktype = _dec(self.client.type(key))
            if ktype == "none":
                results.append({"key": key, "ok": False, "error": "键不存在"})
                continue
            if ktype != "string":
                results.append({"key": key, "ok": False, "error": f"类型为 {ktype}，仅支持 string"})
                continue
            try:
                if ttl and int(ttl) > 0:
                    self.client.set(key, value, ex=int(ttl))
                else:
                    self.client.set(key, value)
                results.append({"key": key, "ok": True})
            except Exception as exc:
                results.append({"key": key, "ok": False, "error": str(exc)})
        return {
            "updated": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "results": results,
        }

    def batch_expire(self, db: int, keys: list[str], ttl: int) -> dict:
        self._select(db)
        results = []
        for key in dict.fromkeys([k for k in keys if k]):
            try:
                if self.client.exists(key) == 0:
                    results.append({"key": key, "ok": False, "error": "键不存在"})
                    continue
                if int(ttl) > 0:
                    self.client.expire(key, int(ttl))
                    results.append({"key": key, "ok": True, "ttl": int(ttl)})
                else:
                    self.client.persist(key)
                    results.append({"key": key, "ok": True, "ttl": -1})
            except Exception as exc:
                results.append({"key": key, "ok": False, "error": str(exc)})
        return {
            "updated": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
            "results": results,
        }

    def expire(self, db: int, key: str, ttl: int) -> None:
        self._select(db)
        if ttl <= 0:
            self.client.persist(key)
        else:
            self.client.expire(key, int(ttl))

    def create_key(self, db: int, key: str, ktype: str) -> None:
        self._select(db)
        if ktype == "string":
            self.client.set(key, "")
        elif ktype == "hash":
            self.client.hset(key, "__field__", "")
        elif ktype == "list":
            self.client.rpush(key, "item")
        elif ktype == "set":
            self.client.sadd(key, "member")
        elif ktype == "zset":
            self.client.zadd(key, {"member": 0})
        elif ktype == "stream":
            self.client.xadd(key, {"field": "value"})
        else:
            raise RedisError(f"不支持的类型：{ktype}")

    def console(self, db: int, command: str) -> dict:
        self._select(db)
        args = shlex.split(command)
        if not args:
            raise RedisError("命令为空")
        timer = Timer()
        try:
            result = self.client.execute_command(*args)
        except Exception as exc:
            raise RedisError(str(exc)) from exc
        return {"command": command, "result": _dec(result), "elapsed": timer.ms()}

    def realtime(self) -> dict:
        self._select(0)
        try:
            info = self.client.info()
        except Exception as exc:
            raise RedisError(f"读取 INFO 失败：{exc}") from exc
        keyspace = {k: dict(v) for k, v in (info.get("keyspace") or {}).items()} if isinstance(info.get("keyspace"), dict) else {}
        return {
            "version": info.get("redis_version"),
            "mode": info.get("redis_mode"),
            "uptime": info.get("uptime_in_seconds"),
            "clients": info.get("connected_clients"),
            "memory": info.get("used_memory"),
            "memoryPeak": info.get("used_memory_peak"),
            "keyspaceHits": info.get("keyspace_hits"),
            "keyspaceMisses": info.get("keyspace_misses"),
            "ops": info.get("instantaneous_ops_per_sec"),
            "keyspace": keyspace,
            "roles": info.get("role"),
            "raw": {
                "总内存": human_bytes(info.get("used_memory") or 0),
                "客户端": info.get("connected_clients"),
                "命中率": self._hit_rate(info),
            },
        }

    @staticmethod
    def _hit_rate(info: dict) -> str:
        hits = info.get("keyspace_hits") or 0
        misses = info.get("keyspace_misses") or 0
        total = hits + misses
        return f"{(hits / total * 100):.2f}%" if total else "-"

    def _select(self, db: int) -> None:
        if self.cluster:
            if int(db or 0) != 0:
                raise RedisError("Redis 集群仅支持 db0")
            return
        try:
            self.client.execute_command("SELECT", int(db))
        except Exception:
            pass
