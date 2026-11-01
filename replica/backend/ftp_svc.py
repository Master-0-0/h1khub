"""Plain FTP / FTPS client used by the replica file browser."""
from __future__ import annotations

import ftplib
import io
import os
import posixpath
import re
import tempfile
import time
from typing import Any

from util import human_bytes, now_iso


class FTPError(Exception):
    pass


def _mode_string(perm: str) -> str:
    """Translate MLSD 'perm' facts or LIST mode text into a friendly mode string."""
    mapping = {
        "a": "a", "c": "c", "d": "d", "e": "x", "f": "-", "l": "l", "m": "m",
        "p": "p", "r": "r", "w": "w",
    }
    if not perm:
        return ""
    # MLSD perm looks like "adfr", "flr", "el" ... not a POSIX string; keep as-is
    if re.fullmatch(r"[acdeflmprw]+", perm):
        return perm
    return perm


def _iso_from_modify(value: str) -> str:
    # MLSD modify: YYYYMMDDHHMMSS(.sss)
    text = str(value or "").strip()
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", text)
    if not m:
        return ""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.strptime("".join(m.groups()), "%Y%m%d%H%M%S"))
    except ValueError:
        return ""


_LIST_RE = re.compile(
    r"^(?P<perm>[-dlbcps][rwxstST-]{9})\S*\s+\d+\s+\S+\s+\S+\s+(?P<size>\d+)\s+"
    r"(?P<mon>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+(?P<time>\d{1,2}:\d{2}|\d{4})\s+(?P<name>.+)$"
)
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
)}


def _parse_list_line(line: str) -> dict | None:
    m = _LIST_RE.match(line.strip())
    if not m:
        return None
    perm = m.group("perm")
    name = m.group("name")
    # strip symlink target "name -> target"
    is_link = perm.startswith("l")
    if " -> " in name:
        name = name.split(" -> ", 1)[0]
    size = int(m.group("size"))
    mon = _MONTHS.get(m.group("mon").capitalize(), 1)
    day = int(m.group("day"))
    token = m.group("time")
    year = time.localtime().tm_year
    hour = minute = 0
    if ":" in token:
        hour, minute = (int(x) for x in token.split(":"))
    else:
        year = int(token)
    try:
        stamp = time.mktime((year, mon, day, hour, minute, 0, 0, 0, -1))
        mtime = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stamp))
    except (OverflowError, ValueError):
        mtime = ""
    return {
        "name": name,
        "isDir": perm.startswith("d"),
        "isLink": is_link,
        "size": size,
        "mtime": mtime,
        "permissions": perm,
    }


class FTPClient:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg or {}
        self.host = self.cfg.get("host") or "127.0.0.1"
        self.port = int(self.cfg.get("port") or 21)
        protocol = (self.cfg.get("protocol") or "ftp").lower()
        self.tls = protocol in ("ftps", "ftp+tls", "ftpes") or bool(self.cfg.get("tls"))
        self.passive = self.cfg.get("passive", True) is not False
        self.timeout = int(self.cfg.get("connectTimeout") or 15)
        self.conn: ftplib.FTP | None = None
        self.home_path = "/"

    # ---------------------------------------------------------------- lifecycle
    def connect(self) -> None:
        cls = ftplib.FTP_TLS if self.tls else ftplib.FTP
        conn = cls()
        conn.encoding = "utf-8"
        try:
            conn.connect(self.host, self.port, timeout=self.timeout)
            conn.login(
                self.cfg.get("username") or "anonymous",
                self.cfg.get("password") or "anonymous@",
            )
            if self.tls:
                conn.prot_p()
            conn.set_pasv(self.passive)
        except (ftplib.all_errors + (OSError,)) as exc:  # type: ignore[operator]
            try:
                conn.close()
            except Exception:
                pass
            raise FTPError(f"FTP 连接失败：{exc}") from exc
        self.conn = conn
        try:
            self.home_path = conn.pwd() or "/"
        except ftplib.all_errors:
            self.home_path = "/"

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.quit()
            except Exception:
                try:
                    self.conn.close()
                except Exception:
                    pass
            self.conn = None

    def _c(self) -> ftplib.FTP:
        if self.conn is None:
            self.connect()
        assert self.conn is not None
        return self.conn

    def _abs(self, path: str) -> str:
        text = str(path or "").strip()
        if text in ("", "~", "."):
            return self.home_path or "/"
        if text == "..":
            return posixpath.dirname((self.home_path or "/").rstrip("/")) or "/"
        if not text.startswith("/"):
            text = posixpath.join(self.home_path or "/", text)
        return posixpath.normpath(text)

    # --------------------------------------------------------------------- read
    def test(self) -> dict:
        timer = time.time()
        self.connect()
        try:
            version = self._c().getwelcome() or ""
            files = self.list_dir(self.home_path or "/")
            return {
                "ok": True,
                "protocol": "ftps" if self.tls else "ftp",
                "home": self.home_path,
                "entries": len(files["entries"]),
                "welcome": version.strip().splitlines()[0][:200] if version.strip() else "",
                "elapsed": int((time.time() - timer) * 1000),
            }
        finally:
            self.close()

    def list_dir(self, path: str = "~", show_hidden: bool = True) -> dict:
        conn = self._c()
        target = self._abs(path)
        try:
            conn.cwd(target)
        except ftplib.error_perm as exc:
            raise FTPError(f"无法进入目录 {target}：{exc}") from exc
        cwd = conn.pwd() or target
        entries: list[dict] = []
        used_mlsd = False
        try:
            for name, facts in conn.mlsd():
                if name in (".", ".."):
                    continue
                typ = (facts.get("type") or "").lower()
                is_dir = typ in ("dir", "cdir", "pdir")
                size = int(facts.get("size") or 0)
                entries.append(
                    {
                        "name": name,
                        "path": posixpath.join(cwd, name),
                        "isDir": is_dir,
                        "isLink": typ == "os.unix=slink" or typ == "slink",
                        "size": size,
                        "mtime": _iso_from_modify(facts.get("modify") or ""),
                        "permissions": _mode_string(facts.get("perm") or ""),
                        "hidden": name.startswith("."),
                    }
                )
            used_mlsd = True
        except (ftplib.error_perm, ftplib.error_proto, AttributeError, TypeError):
            used_mlsd = False
        if not used_mlsd:
            lines: list[str] = []
            try:
                conn.retrlines("LIST", lines.append)
            except ftplib.error_perm as exc:
                raise FTPError(f"列目录失败：{exc}") from exc
            for line in lines:
                parsed = _parse_list_line(line)
                if not parsed:
                    continue
                entries.append(
                    {
                        "name": parsed["name"],
                        "path": posixpath.join(cwd, parsed["name"]),
                        "isDir": parsed["isDir"],
                        "isLink": parsed["isLink"],
                        "size": parsed["size"],
                        "mtime": parsed["mtime"],
                        "permissions": parsed["permissions"],
                        "hidden": parsed["name"].startswith("."),
                    }
                )
        if not show_hidden:
            entries = [e for e in entries if not e["hidden"]]
        entries.sort(key=lambda e: (not e["isDir"], e["name"].lower()))
        parent = posixpath.dirname(cwd.rstrip("/")) or "/"
        return {
            "path": cwd,
            "home": self.home_path or "/",
            "parent": parent,
            "entries": entries,
            "protocol": "ftps" if self.tls else "ftp",
        }

    def read_text(self, path: str, limit: int = 5 * 1024 * 1024) -> dict:
        conn = self._c()
        target = self._abs(path)
        try:
            size = conn.size(target)
        except ftplib.all_errors:
            size = None
        if size and size > limit:
            raise FTPError(f"文件过大（{human_bytes(size)}），请下载后编辑")
        buf = io.BytesIO()

        def sink(chunk: bytes) -> None:
            buf.write(chunk)
            if buf.tell() > limit:
                raise FTPError(f"文件超过 {human_bytes(limit)}，请下载后编辑")

        try:
            conn.retrbinary(f"RETR {target}", sink, blocksize=65536)
        except FTPError:
            raise
        except ftplib.error_perm as exc:
            raise FTPError(f"读取失败：{exc}") from exc
        raw = buf.getvalue()
        try:
            text = raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
            encoding = "latin-1"
        return {"path": target, "content": text, "encoding": encoding, "size": len(raw), "mtime": now_iso()}

    def download_to(self, path: str, local_path: str) -> int:
        conn = self._c()
        target = self._abs(path)
        total = 0
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        try:
            with open(local_path, "wb") as fh:
                def sink(chunk: bytes) -> None:
                    nonlocal total
                    fh.write(chunk)
                    total += len(chunk)

                conn.retrbinary(f"RETR {target}", sink, blocksize=262144)
        except ftplib.error_perm as exc:
            raise FTPError(f"下载失败：{exc}") from exc
        return total

    # -------------------------------------------------------------------- write
    def mkdir(self, path: str) -> None:
        target = self._abs(path)
        try:
            self._c().mkd(target)
        except ftplib.error_perm as exc:
            raise FTPError(f"创建目录失败：{exc}") from exc

    def rename(self, src: str, dst: str) -> None:
        try:
            self._c().rename(self._abs(src), self._abs(dst))
        except ftplib.error_perm as exc:
            raise FTPError(f"重命名失败：{exc}") from exc

    def delete(self, path: str) -> None:
        target = self._abs(path)
        conn = self._c()
        if target in ("/", "", self.home_path):
            raise FTPError("拒绝删除根目录或主目录")
        listing = None
        try:
            listing = self.list_dir(target)
        except FTPError:
            listing = None
        if listing is not None:
            try:
                conn.cwd("/")
            except ftplib.all_errors:
                pass
            for entry in listing["entries"]:
                self.delete(entry["path"])
            try:
                conn.rmd(target)
            except ftplib.error_perm as exc:
                raise FTPError(f"删除目录失败：{exc}") from exc
            return
        try:
            conn.delete(target)
        except ftplib.error_perm as exc:
            raise FTPError(f"删除文件失败：{exc}") from exc

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> dict:
        data = content.encode(encoding or "utf-8", "replace")
        target = self._abs(path)
        try:
            self._c().storbinary(f"STOR {target}", io.BytesIO(data))
        except ftplib.error_perm as exc:
            raise FTPError(f"写入失败：{exc}") from exc
        return {"path": target, "size": len(data)}

    def upload_from(self, local_path: str, remote_path: str) -> int:
        target = self._abs(remote_path)
        try:
            with open(local_path, "rb") as fh:
                self._c().storbinary(f"STOR {target}", fh, blocksize=262144)
        except ftplib.error_perm as exc:
            raise FTPError(f"上传失败：{exc}") from exc
        return os.path.getsize(local_path)
