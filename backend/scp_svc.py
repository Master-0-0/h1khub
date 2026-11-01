"""SCP upload/download over an existing paramiko Transport (remote `scp` binary).

Implements the classic SCP wire protocol (``scp -t`` sink / ``scp -f`` source) so
transfers work even when the remote server only offers SSH exec, not SFTP.
"""
from __future__ import annotations

import os
import tempfile

from util import now_iso


class SCPError(Exception):
    pass


def _quote(path: str) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


class SCPClient:
    def __init__(self, transport) -> None:
        self.transport = transport

    # ------------------------------------------------------------- internals
    def _channel(self, command: str):
        try:
            chan = self.transport.open_session()
            chan.settimeout(30)
            chan.exec_command(command)
        except Exception as exc:
            raise SCPError(f"无法启动远端 scp：{exc}") from exc
        chan.settimeout(600)
        return chan

    @staticmethod
    def _read_ack(chan) -> None:
        code = chan.recv(1)
        if not code:
            raise SCPError("远端连接已关闭（可能未安装 scp 命令）")
        if code == b"\x00":
            return
        message = b""
        while True:
            chunk = chan.recv(4096)
            if not chunk:
                break
            message += chunk
            if b"\n" in message:
                break
        text = message.decode("utf-8", "replace").strip()
        raise SCPError(text or f"SCP 返回错误码 {code!r}")

    def _read_header(self, chan) -> dict:
        """Read C/D/E/T protocol lines until a C record is found."""
        buf = b""
        while True:
            while b"\n" not in buf:
                chunk = chan.recv(1)
                if not chunk:
                    raise SCPError("远端未返回文件头")
                buf += chunk
            line, _, buf = buf.partition(b"\n")
            text = line.decode("utf-8", "replace")
            if text.startswith("T"):
                chan.sendall(b"\x00")
                continue
            if text.startswith("E"):
                chan.sendall(b"\x00")
                raise SCPError("远端路径是目录，请指定文件")
            if not text.startswith("C"):
                raise SCPError(f"无法识别的 SCP 文件头：{text[:120]}")
            parts = text[1:].split(" ", 2)
            if len(parts) != 3:
                raise SCPError(f"SCP 文件头格式错误：{text[:120]}")
            mode_text, size_text, name = parts
            try:
                return {"mode": int(mode_text, 8), "size": int(size_text), "name": name}
            except ValueError as exc:
                raise SCPError(f"SCP 文件头数值错误：{text[:120]}") from exc

    # ---------------------------------------------------------------- upload
    def upload(self, local_path: str, remote_path: str, mode: int | None = None) -> dict:
        if not os.path.isfile(local_path):
            raise SCPError(f"本地文件不存在：{local_path}")
        size = os.path.getsize(local_path)
        remote = str(remote_path)
        directory = os.path.dirname(remote.rstrip("/")) or "."
        name = os.path.basename(remote.rstrip("/")) or os.path.basename(local_path)
        if name in ("", ".", ".."):
            raise SCPError("远端文件名无效")
        chan = self._channel(f"scp -t {_quote(directory)}")
        try:
            self._read_ack(chan)
            permissions = mode if mode is not None else (os.stat(local_path).st_mode & 0o777)
            chan.sendall(f"C{permissions:04o} {size} {name}\n".encode("utf-8"))
            self._read_ack(chan)
            sent = 0
            with open(local_path, "rb") as fh:
                while True:
                    chunk = fh.read(262144)
                    if not chunk:
                        break
                    chan.sendall(chunk)
                    sent += len(chunk)
            chan.sendall(b"\x00")
            self._read_ack(chan)
            # The sink treats channel EOF as "no more files"; sending an extra
            # NUL makes OpenSSH's scp exit with status 1.
            chan.shutdown_write()
            status = chan.recv_exit_status()
            if status not in (0, None):
                raise SCPError(f"远端 scp 退出码 {status}")
            return {"path": remote, "size": sent, "mode": permissions}
        finally:
            try:
                chan.close()
            except Exception:
                pass

    def upload_bytes(self, payload: bytes, remote_path: str, mode: int = 0o644) -> dict:
        tmp = tempfile.NamedTemporaryFile(prefix="h1khub-scp-", delete=False)
        try:
            tmp.write(payload)
            tmp.close()
            return self.upload(tmp.name, remote_path, mode)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    # -------------------------------------------------------------- download
    def download(self, remote_path: str, local_path: str) -> dict:
        remote = str(remote_path)
        chan = self._channel(f"scp -f {_quote(remote)}")
        try:
            chan.sendall(b"\x00")
            header = self._read_header(chan)
            chan.sendall(b"\x00")
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            remaining = header["size"]
            received = 0
            with open(local_path, "wb") as fh:
                while remaining > 0:
                    chunk = chan.recv(min(262144, remaining))
                    if not chunk:
                        raise SCPError("传输中断：远端提前关闭连接")
                    fh.write(chunk)
                    received += len(chunk)
                    remaining -= len(chunk)
            ack = chan.recv(1)
            if ack not in (b"\x00", b""):
                raise SCPError("远端文件传输确认失败")
            chan.sendall(b"\x00")
            chan.shutdown_write()
            status = chan.recv_exit_status()
            if status not in (0, None):
                raise SCPError(f"远端 scp 退出码 {status}")
            try:
                os.chmod(local_path, header["mode"] & 0o777)
            except OSError:
                pass
            return {"path": remote, "size": received, "mode": header["mode"] & 0o777, "name": header["name"]}
        finally:
            try:
                chan.close()
            except Exception:
                pass

    def download_bytes(self, remote_path: str) -> bytes:
        tmp = tempfile.NamedTemporaryFile(prefix="h1khub-scp-dl-", delete=False)
        tmp.close()
        try:
            self.download(remote_path, tmp.name)
            with open(tmp.name, "rb") as fh:
                return fh.read()
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
