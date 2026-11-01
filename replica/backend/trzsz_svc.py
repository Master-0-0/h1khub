"""trzsz file transfer client (trz/tsz compatible), driven by the backend.

The browser cannot speak the trzsz terminal protocol directly (it relies on a
fingerprint / file-picker gesture in iTerm2), so the replica implements the
*client* half of the protocol in the backend:

1. run the remote ``trz``/``tsz`` on a dedicated SSH exec channel;
2. read the ``::TRZSZ:TRANSFER:...`` magic line it prints;
3. open a ``direct-tcpip`` channel to the remote tunnel port it announced;
4. exchange the ``::TRZSZ::CLIENT::HELLO`` handshake and the base64/zlib
   framed protocol (ACT/CFG/NUM/NAME/SIZE/DATA/MD5/SUCC/EXIT).

This mirrors what the iTerm2 plugin does, without a terminal or a GUI gesture.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import threading
import time
import zlib

MAGIC_RE = re.compile(rb"::TRZSZ:TRANSFER:([RSD]):([^:\r\n]+):(\d+):(\d+)")
PROTOCOL_VERSION = 1
CLIENT_VERSION = "1.1.7"
CHUNK = 1024 * 1024


class TrzszError(Exception):
    pass


class _Wire:
    """Line/byte framing over a paramiko Channel (base64 + zlib payloads)."""

    def __init__(self, chan, newline: str = "\n") -> None:
        self.chan = chan
        self.newline = newline
        self.buf = b""

    def _fill(self) -> bool:
        try:
            data = self.chan.recv(65536)
        except Exception as exc:  # timeout or closed channel
            raise TrzszError(f"trzsz 通道读取失败：{exc}") from exc
        if not data:
            return False
        self.buf += data
        return True

    def read_line(self) -> str:
        while b"\n" not in self.buf:
            if not self._fill():
                if not self.buf:
                    raise TrzszError("trzsz 通道已关闭")
                break
        idx = self.buf.find(b"\n")
        if idx < 0:
            line, self.buf = self.buf, b""
        else:
            line, self.buf = self.buf[:idx], self.buf[idx + 1 :]
        return line.decode("latin1").rstrip("\r")

    def read_exact(self, size: int) -> bytes:
        while len(self.buf) < size:
            if not self._fill():
                raise TrzszError("trzsz 通道提前关闭")
        data, self.buf = self.buf[:size], self.buf[size:]
        return data

    def write(self, data: bytes) -> None:
        self.chan.sendall(data)

    # ------------------------------------------------------------- protocol
    def send_line(self, typ: str, text: str) -> None:
        self.write(f"#{typ}:{text}{self.newline}".encode("utf-8"))

    def send_payload(self, typ: str, payload: bytes) -> None:
        self.send_line(typ, base64.b64encode(zlib.compress(payload)).decode("ascii"))

    def send_int(self, typ: str, value: int) -> None:
        self.send_line(typ, str(int(value)))

    def send_string(self, typ: str, text: str) -> None:
        self.send_payload(typ, text.encode("utf-8"))

    def send_json(self, typ: str, obj: dict) -> None:
        self.send_string(typ, json.dumps(obj, separators=(",", ":")))

    def recv_check(self, expect: str) -> bytes:
        line = self.read_line().strip("\x00")
        idx = line.find(":")
        if idx < 0:
            raise TrzszError(f"trzsz 协议错误：{line[:120]}")
        typ, payload = line[1:idx], line[idx + 1 :]
        if typ in ("FAIL", "fail"):
            raise TrzszError("trzsz 远端错误：" + _decode_text(payload))
        if typ != expect:
            raise TrzszError(f"trzsz 协议错误：期望 {expect}，收到 {typ}")
        return payload.encode("latin1")

    def recv_int(self, expect: str) -> int:
        return int(self.recv_check(expect))

    def recv_payload(self, expect: str) -> bytes:
        return _decode_payload(self.recv_check(expect))

    def recv_string(self, expect: str) -> str:
        return self.recv_payload(expect).decode("utf-8")

    def recv_json(self, expect: str) -> dict:
        return json.loads(self.recv_string(expect))


def _decode_payload(text: str) -> bytes:
    try:
        return zlib.decompress(base64.b64decode(text))
    except Exception as exc:
        raise TrzszError(f"trzsz 载荷解码失败：{exc}") from exc


def _decode_text(text: str) -> str:
    try:
        return _decode_payload(text).decode("utf-8", "replace")
    except Exception:
        return text[:200]


def _read_magic(chan, timeout: float = 15.0) -> tuple[str, str, str, int]:
    """Read the remote transfer magic line from the exec channel stdout."""
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        try:
            if chan.recv_ready():
                chunk = chan.recv(4096)
                if not chunk:
                    break
                buf += chunk
                match = MAGIC_RE.search(buf)
                if match and b"\n" in buf[match.end() :]:
                    return (
                        match.group(1).decode(),
                        match.group(2).decode(),
                        match.group(3).decode(),
                        int(match.group(4)),
                    )
            else:
                time.sleep(0.03)
        except Exception as exc:
            raise TrzszError(f"读取 trzsz 握手信息失败：{exc}") from exc
    raise TrzszError(
        "远端未启动 trzsz 传输（请确认远端已安装 trz/tsz，且 SSH 允许 direct-tcpip）"
        + (f"：{buf[-160:]!r}" if buf else "")
    )


def _open_tunnel(transport, port: int):
    src = transport.get_transport() if hasattr(transport, "get_transport") else transport
    try:
        chan = src.open_channel(
            "direct-tcpip", ("127.0.0.1", int(port)), ("127.0.0.1", 0), timeout=15
        )
    except Exception as exc:
        raise TrzszError(f"打开 trzsz 隧道失败：{exc}") from exc
    chan.settimeout(120)
    return chan


def _collect(channel, timeout: float = 30.0) -> str:
    """Drain the exec channel stdout after the transfer finished."""
    deadline = time.time() + timeout
    data = b""
    while time.time() < deadline:
        try:
            if channel.recv_ready():
                chunk = channel.recv(65536)
                if not chunk:
                    break
                data += chunk
            elif channel.exit_status_ready():
                try:
                    data += channel.recv(65536) or b""
                except Exception:
                    pass
                break
            else:
                time.sleep(0.05)
        except Exception:
            break
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[78]", "", data.decode("utf-8", "replace")).strip()


def _run_remote(transport, command: str):
    chan = transport.open_session(timeout=15)
    chan.settimeout(15)
    chan.exec_command(command)
    return chan


class TrzszClient:
    def __init__(self, transport, progress=None) -> None:
        # Accept either a paramiko Transport or an SSHSession-like wrapper.
        self.transport = transport
        self.progress = progress or (lambda event: None)

    # ------------------------------------------------------------------ up
    def upload(self, dest_dir: str, files: list[tuple[str, bytes]]) -> dict:
        if not files:
            raise TrzszError("没有要上传的文件")
        dest = shlex.quote(dest_dir or ".")
        chan = _run_remote(self.transport, f"cd {dest} && /usr/local/bin/trz -q . || trz -q .")
        try:
            mode, version, uid, port = _read_magic(chan)
            if mode not in ("R", "D"):
                raise TrzszError(f"远端模式异常：{mode}")
            tunnel = _open_tunnel(self.transport, port)
            try:
                wire = _handshake(tunnel, uid, port)
                _send_all(wire, files, self.progress)
                wire.send_string("EXIT", "")
                message = _collect(chan)
            finally:
                tunnel.close()
        finally:
            try:
                chan.close()
            except Exception:
                pass
        return {"ok": True, "direction": "upload", "files": len(files), "message": message}

    # ---------------------------------------------------------------- down
    def download(self, remote_paths: list[str]) -> dict:
        if not remote_paths:
            raise TrzszError("没有要下载的文件")
        quoted = " ".join(shlex.quote(p) for p in remote_paths)
        chan = _run_remote(self.transport, f"/usr/local/bin/tsz -q {quoted} || tsz -q {quoted}")
        try:
            mode, version, uid, port = _read_magic(chan)
            if mode not in ("R", "S"):
                raise TrzszError(f"远端模式异常：{mode}")
            tunnel = _open_tunnel(self.transport, port)
            try:
                wire = _handshake(tunnel, uid, port)
                received = _recv_all(wire, self.progress)
                wire.send_string("EXIT", "")
                message = _collect(chan)
            finally:
                tunnel.close()
        finally:
            try:
                chan.close()
            except Exception:
                pass
        return {"ok": True, "direction": "download", "files": received, "message": message}


def _handshake(tunnel, uid13: str, port: int) -> _Wire:
    """Perform the tunnel hello. The server trims the last 2 digits of the uid."""
    unique_id = uid13[:-2] if len(uid13) > 2 else uid13
    hello = f"::TRZSZ::CLIENT::HELLO::{unique_id}:{port}".encode("ascii")
    tunnel.sendall(hello)
    expect_len = len(hello)
    data = b""
    while len(data) < expect_len:
        chunk = tunnel.recv(expect_len - len(data))
        if not chunk:
            raise TrzszError("trzsz 隧道握手被远端关闭")
        data += chunk
    expected = f"::TRZSZ::SERVER::HELLO::{unique_id}:{port}".encode("ascii")
    if data != expected:
        raise TrzszError(f"trzsz 隧道握手失败：{data[:80]!r}")
    return _Wire(tunnel)


def _send_all(wire: _Wire, files: list[tuple[str, bytes]], progress) -> None:
    # Client action tells the remote trz how to behave.
    wire.send_json(
        "ACT",
        {
            "lang": "py",
            "confirm": True,
            "version": CLIENT_VERSION,
            "support_dir": True,
            "protocol": PROTOCOL_VERSION,
        },
    )
    cfg = wire.recv_json("CFG")
    if cfg.get("directory"):
        raise TrzszError("暂不支持整目录上传，请逐个文件上传")
    wire.send_int("NUM", len(files))
    if wire.recv_int("SUCC") != len(files):
        raise TrzszError("trzsz 文件数校验失败")
    total = sum(len(data) for _, data in files)
    done = 0
    transferred = []
    for name, data in files:
        wire.send_string("NAME", name)
        remote_name = wire.recv_string("SUCC")
        wire.send_int("SIZE", len(data))
        if wire.recv_int("SUCC") != len(data):
            raise TrzszError("trzsz 文件大小校验失败")
        md5 = hashlib.md5()
        for offset in range(0, len(data), CHUNK):
            chunk = data[offset : offset + CHUNK]
            md5.update(chunk)
            wire.send_payload("DATA", chunk)
            if wire.recv_int("SUCC") != len(chunk):
                raise TrzszError("trzsz 数据块校验失败")
            done += len(chunk)
            progress({"file": name, "done": done, "total": total or 1})
        wire.send_payload("MD5", md5.digest())
        if wire.recv_payload("SUCC") != md5.digest():
            raise TrzszError("trzsz MD5 校验失败")
        transferred.append({"name": remote_name or name, "size": len(data)})
    progress({"done": total, "total": total or 1, "finished": True, "files": transferred})


def _recv_all(wire: _Wire, progress) -> list[dict]:
    wire.send_json(
        "ACT",
        {
            "lang": "py",
            "confirm": True,
            "version": CLIENT_VERSION,
            "support_dir": True,
            "protocol": PROTOCOL_VERSION,
        },
    )
    cfg = wire.recv_json("CFG")
    if cfg.get("directory"):
        raise TrzszError("暂不支持整目录下载，请指定具体文件")
    num = wire.recv_int("NUM")
    wire.send_int("SUCC", num)
    out = []
    for _ in range(num):
        name = os.path.basename(wire.recv_string("NAME").replace("\\", "/")) or "download.bin"
        wire.send_string("SUCC", name)
        size = wire.recv_int("SIZE")
        wire.send_int("SUCC", size)
        md5 = hashlib.md5()
        chunks = []
        remaining = size
        while remaining > 0:
            payload = wire.recv_payload("DATA")
            if not payload:
                raise TrzszError("trzsz 收到空数据块")
            chunks.append(payload)
            md5.update(payload)
            remaining -= len(payload)
            wire.send_int("SUCC", len(payload))
            progress({"file": name, "done": size - remaining, "total": size or 1})
        expect = wire.recv_payload("MD5")
        if expect != md5.digest():
            raise TrzszError("trzsz MD5 校验失败")
        wire.send_payload("SUCC", md5.digest())
        out.append({"name": name, "size": size, "data": b"".join(chunks)})
        progress({"file": name, "done": size, "total": size or 1, "finished": True})
    return out


def download_to_dir(transport, remote_paths: list[str], dest_dir: str, progress=None) -> dict:
    """Download remote paths into a local directory (temp dir owned by caller)."""
    client = TrzszClient(transport, progress)
    result = client.download(remote_paths)
    os.makedirs(dest_dir, exist_ok=True)
    saved = []
    for item in result["files"]:
        path = os.path.join(dest_dir, os.path.basename(item["name"]))
        with open(path, "wb") as fh:
            fh.write(item["data"])
        saved.append({"name": os.path.basename(path), "path": path, "size": item["size"]})
    result["saved"] = saved
    result.pop("files", None)
    return result
