"""Local OCI registry lifecycle for the "pull from a local network registry" feature.

Docker Hub is unreachable in this lab, so the replica can publish an existing
local image into a small read-only OCI distribution registry (served by
``tools/oci_registry.py``) and pull it back through Docker.  This module owns
the registry process and the OCI layout under ``~/.h1khub/registry``.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "oci_registry.py")


class RegistryError(Exception):
    pass


class LocalRegistry:
    def __init__(self, home: str) -> None:
        self.home = os.path.expanduser(home)
        self.layout = os.path.join(self.home, "registry")
        self.state_path = os.path.join(self.home, "registry-state.json")

    # ------------------------------------------------------------ state
    def _load_state(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        os.makedirs(self.home, mode=0o700, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_path)

    def _alive(self, pid: int) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def status(self) -> dict:
        state = self._load_state()
        pid = int(state.get("pid") or 0)
        port = int(state.get("port") or 5050)
        running = self._alive(pid)
        if not running:
            port = int(state.get("port") or 5050)
        images = []
        try:
            with open(os.path.join(self.layout, "registry.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            images = list((data.get("images") or {}).values())
        except Exception:
            images = []
        return {
            "running": running,
            "port": port,
            "url": f"http://127.0.0.1:{port}" if running else "",
            "layout": self.layout,
            "images": images,
        }

    # ---------------------------------------------------------- lifecycle
    def start(self, port: int = 5050) -> dict:
        state = self._load_state()
        pid = int(state.get("pid") or 0)
        if self._alive(pid):
            return self.status()
        os.makedirs(self.home, mode=0o700, exist_ok=True)
        os.makedirs(self.layout, mode=0o700, exist_ok=True)
        log_path = os.path.join(self.home, "registry.log")
        log = open(log_path, "ab")
        proc = subprocess.Popen(
            [sys.executable, TOOL, "serve", "--layout", self.layout, "--port", str(int(port))],
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
        self._save_state({"pid": proc.pid, "port": int(port), "startedAt": time.time()})
        deadline = time.time() + 10
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/v2/", timeout=1) as resp:
                    if resp.status == 200:
                        return self.status()
            except Exception:
                time.sleep(0.2)
        self.stop()
        raise RegistryError("本地 registry 启动失败，请查看 ~/.h1khub/registry.log")

    def stop(self) -> dict:
        state = self._load_state()
        pid = int(state.get("pid") or 0)
        if self._alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
            for _ in range(30):
                if not self._alive(pid):
                    break
                time.sleep(0.1)
        self._save_state({**state, "pid": 0})
        return self.status()

    # ------------------------------------------------------------ publish
    def publish(self, image: str, repo: str, tag: str = "latest") -> dict:
        image = (image or "").strip()
        repo = (repo or "").strip().strip("/")
        tag = (tag or "latest").strip() or "latest"
        if not image:
            raise RegistryError("镜像不能为空")
        if not repo or ":" in repo:
            raise RegistryError("仓库名无效（例如 h1khub/ui-copy）")
        os.makedirs(self.layout, mode=0o700, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, TOOL, "pack", "--image", image, "--repo", repo, "--tag", tag, "--layout", self.layout],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if proc.returncode != 0:
            raise RegistryError(f"打包镜像失败：{(proc.stderr or proc.stdout).strip()[-500:]}")
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            return payload.get("entry") or payload
        except Exception as exc:
            raise RegistryError(f"解析打包结果失败：{exc}") from exc

    # --------------------------------------------------------------- pull
    def image_ref(self, repo: str, tag: str = "latest") -> str:
        state = self._load_state()
        port = int(state.get("port") or 5050)
        return f"127.0.0.1:{port}/{repo.strip('/')}:{tag or 'latest'}"
