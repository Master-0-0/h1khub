"""Docker management through the Docker CLI (local socket or remote SSH)."""
from __future__ import annotations

import base64
import io
import json
import os
import posixpath
import shlex
import shutil
import subprocess
import tarfile
import time
from datetime import datetime
from typing import Any

import paramiko

from util import Timer


class DockerError(Exception):
    pass


def _run(argv: list[str], env: dict[str, str] | None = None, timeout: int = 60, input_text: str | None = None) -> str:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            env=full_env,
            text=True,
            errors="replace",
            input=input_text,
        )
    except FileNotFoundError as exc:
        raise DockerError("未找到 docker 命令行工具，请安装 Docker CLI") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerError(f"docker 命令超时（{timeout}s）") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise DockerError(msg or f"docker 命令失败（exit={proc.returncode}）")
    return proc.stdout


def _run_bytes(
    argv: list[str],
    env: dict[str, str] | None = None,
    timeout: int = 120,
    input_bytes: bytes | None = None,
) -> bytes:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout, env=full_env, input=input_bytes)
    except FileNotFoundError as exc:
        raise DockerError("未找到 docker 命令行工具，请安装 Docker CLI") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerError(f"docker 命令超时（{timeout}s）") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        raise DockerError(msg or f"docker 命令失败（exit={proc.returncode}）")
    return proc.stdout


class DockerService:
    def __init__(self, cfg: dict, ssh_exec=None) -> None:
        self.cfg = cfg or {}
        self.ssh_exec = ssh_exec  # callable(cmd: str) -> (stdout, stderr) for remote mode
        mode = (cfg.get("mode") or "local").lower()
        self.mode = mode
        socket_path = cfg.get("socketPath") or "/var/run/docker.sock"
        if mode == "tcp" and cfg.get("host"):
            self.docker_host = f"tcp://{cfg.get('host')}:{int(cfg.get('port') or 2375)}"
        else:
            self.docker_host = f"unix://{socket_path}"

    # ------------------------------------------------------------- internals
    def _exec(self, args: list[str], timeout: int = 60) -> str:
        if self.ssh_exec:
            import shlex

            cmd = "docker " + " ".join(shlex.quote(a) for a in args)
            out, err = self.ssh_exec(cmd)
            if err and not out:
                raise DockerError(err.strip())
            return out
        if not shutil.which("docker") and self.mode != "tcp":
            raise DockerError("未找到 docker 命令行工具")
        return _run(["docker"] + args, env={"DOCKER_HOST": self.docker_host}, timeout=timeout)

    @staticmethod
    def _lines_json(text: str) -> list[dict]:
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # ------------------------------------------------------------------ info
    def test(self) -> dict:
        timer = Timer()
        try:
            version = self._exec(["version", "--format", "{{.Server.Version}}"], timeout=20).strip()
            info = self._exec(["info", "--format", "{{json .}}"], timeout=20).strip()
            parsed: dict[str, Any] = {}
            try:
                parsed = json.loads(info.splitlines()[0]) if info else {}
            except Exception:
                parsed = {}
            return {
                "ok": True,
                "version": version,
                "containers": parsed.get("Containers"),
                "running": parsed.get("ContainersRunning"),
                "images": parsed.get("Images"),
                "os": parsed.get("OperatingSystem"),
                "elapsed": timer.ms(),
            }
        except DockerError as exc:
            raise DockerError(str(exc)) from exc

    def containers(self, all_: bool = True) -> list[dict]:
        args = ["ps", "--format", "{{json .}}"]
        if not all_:
            args = ["ps", "--format", "{{json .}}"]
        else:
            args = ["ps", "-a", "--format", "{{json .}}"]
        rows = self._lines_json(self._exec(args))
        out = []
        for row in rows:
            out.append(
                {
                    "id": row.get("ID") or row.get("Id"),
                    "name": (row.get("Names") or "").split(",")[0],
                    "image": row.get("Image"),
                    "command": row.get("Command"),
                    "createdAt": row.get("CreatedAt"),
                    "state": (row.get("State") or "").lower(),
                    "status": row.get("Status"),
                    "ports": row.get("Ports"),
                    "labels": row.get("Labels"),
                    "networks": row.get("Networks"),
                    "mounts": row.get("Mounts"),
                    "runningFor": row.get("RunningFor"),
                }
            )
        return out

    def images(self) -> list[dict]:
        rows = self._lines_json(self._exec(["images", "--format", "{{json .}}"]))
        return [
            {
                "id": row.get("ID"),
                "repository": row.get("Repository"),
                "tag": row.get("Tag"),
                "size": row.get("Size"),
                "createdAt": row.get("CreatedAt"),
                "createdSince": row.get("CreatedSince"),
                "digest": row.get("Digest"),
            }
            for row in rows
        ]

    def volumes(self) -> list[dict]:
        rows = self._lines_json(self._exec(["volume", "ls", "--format", "{{json .}}"]))
        return [{"name": r.get("Name"), "driver": r.get("Driver"), "mountpoint": r.get("Mountpoint")} for r in rows]

    def networks(self) -> list[dict]:
        rows = self._lines_json(self._exec(["network", "ls", "--format", "{{json .}}"]))
        return [{"id": r.get("ID"), "name": r.get("Name"), "driver": r.get("Driver"), "scope": r.get("Scope")} for r in rows]

    def logs(self, container: str, tail: int = 300) -> str:
        return self._exec(["logs", "--tail", str(int(tail)), "--timestamps", container], timeout=30)

    def inspect(self, ref: str) -> Any:
        text = self._exec(["inspect", ref], timeout=30)
        try:
            return json.loads(text)
        except Exception:
            return text

    def stats(self) -> list[dict]:
        rows = self._lines_json(
            self._exec(["stats", "--no-stream", "--format", "{{json .}}"], timeout=30)
        )
        return [
            {
                "id": r.get("ID") or r.get("Container"),
                "name": r.get("Name"),
                "cpu": r.get("CPUPerc"),
                "mem": r.get("MemUsage"),
                "memPerc": r.get("MemPerc"),
                "net": r.get("NetIO"),
                "block": r.get("BlockIO"),
                "pids": r.get("PIDs"),
            }
            for r in rows
        ]

    def action(self, container: str, action: str, force: bool = False) -> dict:
        mapping = {
            "start": ["start", container],
            "stop": ["stop", container],
            "restart": ["restart", container],
            "pause": ["pause", container],
            "unpause": ["unpause", container],
            "kill": ["kill", container],
            "remove": ["rm", container] + (["-f"] if force else []),
            "rename": None,
        }
        if action not in mapping or mapping[action] is None:
            raise DockerError(f"不支持的操作：{action}")
        self._exec(mapping[action], timeout=60)
        return {"ok": True, "action": action, "container": container}

    def remove_image(self, image: str) -> dict:
        self._exec(["rmi", image], timeout=120)
        return {"ok": True}

    def pull(self, image: str) -> dict:
        self._exec(["pull", image], timeout=600)
        return {"ok": True}

    def compose_projects(self) -> list[dict]:
        try:
            text = self._exec(["compose", "ls", "--format", "json"], timeout=30)
            return json.loads(text) if text.strip() else []
        except DockerError:
            return []


    # ------------------------------------------------------------ lifecycle
    def create(
        self,
        image: str,
        name: str = "",
        command: str = "",
        ports: list[str] | None = None,
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
        restart: str = "",
        network: str = "",
        start: bool = True,
    ) -> dict:
        if not image:
            raise DockerError("镜像不能为空")
        args = ["run" if start else "create", "-d"] if start else ["create"]
        if start:
            args = ["run", "-d"]
        else:
            args = ["create"]
        if name:
            args += ["--name", name]
        if restart:
            args += ["--restart", restart]
        if network:
            args += ["--network", network]
        for port in ports or []:
            if port:
                args += ["-p", port]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        for vol in volumes or []:
            if vol:
                args += ["-v", vol]
        args.append(image)
        if command:
            args += shlex.split(command)
        out = self._exec(args, timeout=600).strip()
        return {"ok": True, "id": out, "started": start, "name": name}

    def rename(self, container: str, new_name: str) -> dict:
        if not new_name or "/" in new_name:
            raise DockerError("容器名无效")
        self._exec(["rename", container, new_name], timeout=30)
        return {"ok": True, "container": container, "name": new_name}

    def top(self, container: str) -> dict:
        try:
            text = self._exec(["top", container, "-eo", "pid,ppid,user,stat,etime,args"], timeout=30)
        except DockerError:
            text = self._exec(["top", container], timeout=30)
        lines = [line for line in text.splitlines() if line.strip()]
        header = lines[0].split() if lines else []
        rows = [line.split(None, len(header) - 1) if header else line.split() for line in lines[1:]]
        return {"header": header, "rows": rows, "raw": text}

    def registry_login(self, registry: str, username: str, password: str) -> dict:
        if self.ssh_exec:
            raise DockerError("远程 Docker 的仓库登录暂未实现（需要把登录态写入远端 ~/.docker）")
        if not username:
            raise DockerError("用户名不能为空")
        args = ["docker", "login"]
        if registry:
            args.append(registry)
        args += ["-u", username, "--password-stdin"]
        _run(args, env={"DOCKER_HOST": self.docker_host}, timeout=60, input_text=password or "")
        return {"ok": True, "registry": registry or "docker.io", "username": username}

    # --------------------------------------------------------------- images
    def build_image(self, tag: str, context_dir: str, dockerfile: str = "Dockerfile", no_cache: bool = False) -> dict:
        if not tag or not context_dir:
            raise DockerError("镜像标签与构建目录不能为空")
        if self.ssh_exec:
            raise DockerError("远程 Docker 的镜像构建暂未实现（需要构建上下文）")
        if not os.path.isdir(context_dir):
            raise DockerError(f"构建目录不存在：{context_dir}")
        dockerfile_path = dockerfile if os.path.isabs(dockerfile) else os.path.join(context_dir, dockerfile)
        args = ["build", "-t", tag, "-f", dockerfile_path]
        if no_cache:
            args.append("--no-cache")
        args.append(context_dir)
        out = self._exec(args, timeout=1800)
        return {"ok": True, "tag": tag, "output": out[-8000:]}

    def image_history(self, image: str) -> list[dict]:
        text = self._exec(["history", image, "--format", "{{json .}}", "--no-trunc"], timeout=60)
        rows = self._lines_json(text)
        return [
            {
                "id": r.get("ID"),
                "created": r.get("CreatedSince"),
                "size": r.get("Size"),
                "comment": r.get("Comment"),
                "tags": r.get("Tags"),
            }
            for r in rows
        ]

    def save_image(self, image: str, path: str) -> int:
        if self.ssh_exec:
            raise DockerError("远程 Docker 的镜像导出暂未实现")
        _run(["docker", "save", "-o", path, image], env={"DOCKER_HOST": self.docker_host}, timeout=900)
        return os.path.getsize(path)

    def load_image(self, path: str) -> dict:
        if self.ssh_exec:
            raise DockerError("远程 Docker 的镜像导入暂未实现")
        out = _run(["docker", "load", "-i", path], env={"DOCKER_HOST": self.docker_host}, timeout=900)
        return {"ok": True, "output": out[-4000:]}

    def prune_images(self, all_: bool = False) -> dict:
        args = ["image", "prune", "-f"] + (["-a"] if all_ else [])
        out = self._exec(args, timeout=300)
        return {"ok": True, "output": out[-4000:]}

    # -------------------------------------------------------------- compose
    @staticmethod
    def _compose_file_name(name: str) -> str:
        base = os.path.basename(name or "docker-compose.yml")
        if base not in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") and not base.endswith((".yml", ".yaml")):
            raise DockerError("compose 文件名必须是 .yml/.yaml")
        return base

    def compose_read(self, directory: str, filename: str = "docker-compose.yml") -> dict:
        path = posixpath.join(directory.rstrip("/") or "/", self._compose_file_name(filename))
        if self.ssh_exec:
            out, err = self.ssh_exec(f"cat {shlex.quote(path)}")
            if not out and err:
                raise DockerError(err.strip())
            return {"path": path, "content": out, "remote": True}
        if not os.path.isfile(path):
            raise DockerError(f"compose 文件不存在：{path}")
        if os.path.getsize(path) > 2 * 1024 * 1024:
            raise DockerError("compose 文件过大")
        with open(path, encoding="utf-8", errors="replace") as fh:
            return {"path": path, "content": fh.read(), "remote": False}

    def compose_write(self, directory: str, content: str, filename: str = "docker-compose.yml") -> dict:
        path = posixpath.join(directory.rstrip("/") or "/", self._compose_file_name(filename))
        if len(content.encode()) > 2 * 1024 * 1024:
            raise DockerError("compose 内容过大")
        if self.ssh_exec:
            payload = base64.b64encode(content.encode()).decode()
            out, err = self.ssh_exec(
                f"mkdir -p {shlex.quote(directory)} && printf %s {payload} | base64 -d > {shlex.quote(path)}"
            )
            if err.strip():
                raise DockerError(err.strip())
            return {"path": path, "size": len(content.encode()), "remote": True}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return {"path": path, "size": len(content.encode()), "remote": False}

    def compose_action(self, directory: str, action: str, filename: str = "docker-compose.yml", services: list[str] | None = None) -> dict:
        path = posixpath.join(directory.rstrip("/") or "/", self._compose_file_name(filename))
        mapping = {
            "up": ["compose", "-f", path, "up", "-d"],
            "down": ["compose", "-f", path, "down"],
            "stop": ["compose", "-f", path, "stop"],
            "restart": ["compose", "-f", path, "restart"],
            "start": ["compose", "-f", path, "start"],
            "ps": ["compose", "-f", path, "ps", "--format", "json"],
        }
        if action not in mapping:
            raise DockerError(f"未知 Compose 操作：{action}")
        args = mapping[action] + [s for s in (services or []) if s]
        out = self._exec(args, timeout=600)
        return {"ok": True, "action": action, "output": out[-8000:]}

    def compose_ps(self, directory: str, filename: str = "docker-compose.yml") -> dict:
        path = posixpath.join(directory.rstrip("/") or "/", self._compose_file_name(filename))
        try:
            text = self._exec(["compose", "-f", path, "ps", "--format", "json"], timeout=60)
        except DockerError:
            text = self._exec(["compose", "-f", path, "ps"], timeout=60)
            return {"services": [], "raw": text}
        services = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                services.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return {"services": services, "raw": text}

    # ------------------------------------------------------- container files
    def _cp_bytes(self, spec: str) -> bytes:
        args = ["cp", spec, "-"]
        if self.ssh_exec:
            cmd = "docker " + " ".join(shlex.quote(a) for a in args) + " | base64 -w0"
            out, err = self.ssh_exec(cmd)
            if err.strip() and not out.strip():
                raise DockerError(err.strip())
            try:
                return base64.b64decode(out)
            except Exception as exc:
                raise DockerError(f"读取容器文件失败：{exc}") from exc
        return _run_bytes(["docker"] + args, env={"DOCKER_HOST": self.docker_host}, timeout=300)

    def _cp_stdin(self, container: str, destination: str, tar_bytes: bytes) -> None:
        args = ["cp", "-", f"{container}:{destination}"]
        if self.ssh_exec:
            payload = base64.b64encode(tar_bytes).decode()
            cmd = (
                f"printf %s {payload} | base64 -d | docker "
                + " ".join(shlex.quote(a) for a in args)
            )
            out, err = self.ssh_exec(cmd)
            if err.strip():
                raise DockerError(err.strip())
            return
        _run_bytes(["docker"] + args, env={"DOCKER_HOST": self.docker_host}, timeout=300, input_bytes=tar_bytes)

    @staticmethod
    def _tar_members(data: bytes):
        try:
            tar = tarfile.open(fileobj=io.BytesIO(data))
        except tarfile.TarError as exc:
            raise DockerError(f"无法解析容器文件归档：{exc}") from exc
        return tar

    _LS_SCRIPT = (
        'cd "$1" 2>/dev/null || exit 3; '
        "for f in .[!.]* *; do [ -e \"$f\" ] || continue; "
        'if [ -h "$f" ]; then l=1; else l=0; fi; '
        'if [ -d "$f" ]; then t=d; else t=f; fi; '
        'sz=$(stat -c %s "$f" 2>/dev/null); [ -n "$sz" ] || sz=0; '
        'mt=$(stat -c %Y "$f" 2>/dev/null); [ -n "$mt" ] || mt=0; '
        'pm=$(stat -c %a "$f" 2>/dev/null); [ -n "$pm" ] || pm=0; '
        'printf "%s|%s|%s|%s|%s|%s\\n" "$t" "$l" "$sz" "$mt" "$pm" "$f"; '
        "done"
    )

    def fs_list(self, container: str, path: str = "/") -> dict:
        clean = posixpath.normpath(path or "/")
        if not clean.startswith("/"):
            clean = "/" + clean
        try:
            output = self._exec(["exec", container, "sh", "-c", self._LS_SCRIPT, "_", clean], timeout=60)
        except DockerError as exc:
            message = str(exc)
            if "is not running" in message or "not found" in message:
                raise DockerError("容器未运行，无法浏览文件；请先启动容器") from exc
            raise
        entries = []
        for line in output.splitlines():
            parts = line.split("|", 5)
            if len(parts) != 6:
                continue
            kind, link, size, mtime, permissions, name = parts
            name = name.strip()
            if not name or name in (".", ".."):
                continue
            try:
                epoch = int(float(mtime))
            except ValueError:
                epoch = 0
            entries.append(
                {
                    "name": name,
                    "path": posixpath.join(clean, name),
                    "isDir": kind == "d",
                    "isLink": link == "1",
                    "size": int(float(size)) if kind != "d" else 0,
                    "mtime": datetime.fromtimestamp(epoch).strftime("%Y-%m-%dT%H:%M:%S") if epoch else "",
                    "permissions": "0" + str(permissions) if permissions and not str(permissions).startswith("0") else str(permissions),
                }
            )
        entries.sort(key=lambda e: (not e["isDir"], e["name"].lower()))
        return {"path": clean, "parent": posixpath.dirname(clean) or "/", "entries": entries}

    def fs_read(self, container: str, path: str, limit: int = 5 * 1024 * 1024) -> dict:
        clean = posixpath.normpath(path)
        data = self._cp_bytes(f"{container}:{clean}")
        tar = self._tar_members(data)
        members = [m for m in tar.getmembers() if m.isfile()]
        if not members:
            raise DockerError(f"不是文件：{clean}")
        member = members[0]
        if member.size > limit:
            raise DockerError(f"文件过大（{member.size} bytes），请下载")
        fh = tar.extractfile(member)
        payload = fh.read() if fh else b""
        try:
            text = payload.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = payload.decode("latin-1")
            encoding = "latin-1"
        return {"path": clean, "content": text, "encoding": encoding, "size": len(payload)}

    def fs_write(self, container: str, path: str, content: str, encoding: str = "utf-8") -> dict:
        clean = posixpath.normpath(path)
        payload = content.encode(encoding or "utf-8", "replace")
        self._write_bytes(container, clean, payload)
        return {"path": clean, "size": len(payload)}

    def _write_bytes(self, container: str, path: str, payload: bytes, mode: int = 0o644) -> None:
        directory = posixpath.dirname(path) or "/"
        name = posixpath.basename(path)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(payload))
        self._cp_stdin(container, directory, buf.getvalue())

    def fs_upload_from(self, container: str, local_path: str, remote_path: str) -> int:
        with open(local_path, "rb") as fh:
            payload = fh.read()
        self._write_bytes(container, posixpath.normpath(remote_path), payload)
        return len(payload)

    def fs_download_to(self, container: str, remote_path: str, local_path: str) -> int:
        data = self._cp_bytes(f"{container}:{posixpath.normpath(remote_path)}")
        tar = self._tar_members(data)
        members = [m for m in tar.getmembers() if m.isfile()]
        if not members:
            raise DockerError(f"不是文件：{remote_path}")
        fh = tar.extractfile(members[0])
        payload = fh.read() if fh else b""
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        with open(local_path, "wb") as out:
            out.write(payload)
        return len(payload)

    def _exec_in(self, container: str, script: str, *args: str) -> dict:
        cmd = ["exec", container, "sh", "-c", script, "_", *args]
        out = self._exec(cmd, timeout=60)
        return {"ok": True, "output": out}

    def fs_delete(self, container: str, path: str) -> dict:
        clean = posixpath.normpath(path)
        if clean in ("/", "", "."):
            raise DockerError("拒绝删除容器根目录")
        return self._exec_in(container, 'rm -rf -- "$1"', clean)

    def fs_mkdir(self, container: str, path: str) -> dict:
        return self._exec_in(container, 'mkdir -p -- "$1"', posixpath.normpath(path))

    def fs_rename(self, container: str, src: str, dst: str) -> dict:
        return self._exec_in(container, 'mv -- "$1" "$2"', posixpath.normpath(src), posixpath.normpath(dst))

    def container_inspect_summary(self, container: str) -> dict:
        data = self.inspect(container)
        if not isinstance(data, list) or not data:
            return {}
        item = data[0]
        return {
            "id": item.get("Id"),
            "name": (item.get("Name") or "").lstrip("/"),
            "image": (item.get("Config") or {}).get("Image"),
            "state": item.get("State"),
            "created": item.get("Created"),
            "mounts": item.get("Mounts"),
            "networkSettings": (item.get("NetworkSettings") or {}).get("Networks"),
            "env": (item.get("Config") or {}).get("Env"),
            "cmd": (item.get("Config") or {}).get("Cmd"),
        }
