"""File browser operations for local disk and SFTP."""
from __future__ import annotations

import datetime as dt
import errno
import os
import posixpath
import shutil
import stat
from typing import Any

import paramiko

from util import human_bytes, now_iso


class FileOpError(Exception):
    pass


def _mode_string(mode: int) -> str:
    return stat.filemode(mode)


def _entry_from_stat(name: str, path: str, st: Any, is_link: bool = False) -> dict:
    is_dir = stat.S_ISDIR(st.st_mode)
    mtime = dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")
    return {
        "name": name,
        "path": path,
        "isDir": is_dir,
        "isLink": is_link,
        "size": 0 if is_dir else int(st.st_size),
        "sizeText": "-" if is_dir else human_bytes(st.st_size),
        "mtime": mtime,
        "mode": _mode_string(st.st_mode),
        "permissions": oct(stat.S_IMODE(st.st_mode)),
        "uid": getattr(st, "st_uid", None),
        "gid": getattr(st, "st_gid", None),
    }


def _normalize_remote(path: str) -> str:
    path = (path or "~").strip()
    if path in ("", "."):
        path = "."
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return path


# --------------------------------------------------------------------- local
def local_list(path: str, show_hidden: bool = True) -> dict:
    path = os.path.expanduser(path or "~")
    if not os.path.isdir(path):
        raise FileOpError(f"目录不存在：{path}")
    entries = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if not show_hidden and entry.name.startswith("."):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                    is_link = entry.is_symlink()
                    if is_link:
                        try:
                            st = os.stat(entry.path)
                        except OSError:
                            st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                entries.append(_entry_from_stat(entry.name, entry.path, st, is_link))
    except PermissionError as exc:
        raise FileOpError(f"权限不足：{path}") from exc
    entries.sort(key=lambda e: (not e["isDir"], e["name"].lower()))
    parent = os.path.dirname(path.rstrip("/")) or "/"
    st = os.stat(path)
    return {
        "path": path,
        "parent": parent,
        "entries": entries,
        "isRoot": path == "/",
        "writable": os.access(path, os.W_OK),
        "stat": {
            "mode": _mode_string(st.st_mode),
            "uid": st.st_uid,
            "gid": st.st_gid,
            "mtime": dt.datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"),
        },
    }


def local_home() -> dict:
    home = os.path.expanduser("~")
    return {"home": home, "roots": [home, "/", "/tmp", "/var/log", "/opt"]}


def local_mkdir(path: str) -> None:
    os.makedirs(os.path.expanduser(path), exist_ok=False)


def local_rename(src: str, dst: str) -> None:
    os.rename(os.path.expanduser(src), os.path.expanduser(dst))


def local_delete(path: str) -> None:
    path = os.path.expanduser(path)
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def local_chmod(path: str, mode: str) -> None:
    os.chmod(os.path.expanduser(path), int(mode, 8))


def local_read_text(path: str, limit: int = 5 * 1024 * 1024) -> dict:
    path = os.path.expanduser(path)
    size = os.path.getsize(path)
    if size > limit:
        raise FileOpError(f"文件过大（{human_bytes(size)}），请下载后编辑")
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        encoding = "latin-1"
    return {"path": path, "content": text, "encoding": encoding, "size": size, "mtime": now_iso()}


def local_write_text(path: str, content: str, encoding: str = "utf-8") -> dict:
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content.encode(encoding or "utf-8", "replace"))
    return {"path": path, "size": os.path.getsize(path), "mtime": now_iso()}


# ---------------------------------------------------------------------- sftp
def sftp_home(sftp: paramiko.SFTPClient) -> str:
    try:
        return sftp.normalize(".")
    except Exception:
        return "/"


def sftp_list(sftp: paramiko.SFTPClient, path: str, show_hidden: bool = True) -> dict:
    path = _normalize_remote(path)
    if path == "~":
        path = sftp_home(sftp)
    entries = []
    try:
        attrs = sftp.listdir_attr(path)
    except IOError as exc:
        msg = exc.strerror or str(exc)
        raise FileOpError(f"读取目录失败（{path}）：{msg}") from exc
    for attr in attrs:
        name = attr.filename
        if not show_hidden and name.startswith("."):
            continue
        full = posixpath.join(path, name) if path != "/" else "/" + name
        is_link = stat.S_ISLNK(attr.st_mode or 0)
        entries.append(
            {
                "name": name,
                "path": full,
                "isDir": stat.S_ISDIR(attr.st_mode or 0),
                "isLink": is_link,
                "size": int(attr.st_size or 0),
                "sizeText": human_bytes(attr.st_size or 0),
                "mtime": dt.datetime.fromtimestamp(attr.st_mtime or 0)
                .astimezone()
                .isoformat(timespec="seconds"),
                "mode": _mode_string(attr.st_mode or 0),
                "permissions": oct(stat.S_IMODE(attr.st_mode or 0)),
                "uid": attr.st_uid,
                "gid": attr.st_gid,
            }
        )
    entries.sort(key=lambda e: (not e["isDir"], e["name"].lower()))
    parent = posixpath.dirname(path.rstrip("/")) or "/"
    try:
        st = sftp.stat(path)
        info = {
            "mode": _mode_string(st.st_mode or 0),
            "uid": st.st_uid,
            "gid": st.st_gid,
            "mtime": dt.datetime.fromtimestamp(st.st_mtime or 0)
            .astimezone()
            .isoformat(timespec="seconds"),
        }
    except IOError:
        info = {}
    return {
        "path": path,
        "parent": parent,
        "entries": entries,
        "isRoot": path == "/",
        "home": sftp_home(sftp),
        "stat": info,
    }


def sftp_mkdir(sftp: paramiko.SFTPClient, path: str) -> None:
    sftp.mkdir(path)


def sftp_rename(sftp: paramiko.SFTPClient, src: str, dst: str) -> None:
    sftp.rename(src, dst)


def _rmtree(sftp: paramiko.SFTPClient, path: str) -> None:
    for attr in sftp.listdir_attr(path):
        child = posixpath.join(path, attr.filename)
        if stat.S_ISDIR(attr.st_mode or 0):
            _rmtree(sftp, child)
        else:
            sftp.remove(child)
    sftp.rmdir(path)


def sftp_delete(sftp: paramiko.SFTPClient, path: str) -> None:
    try:
        st = sftp.stat(path)
    except IOError as exc:
        raise FileOpError(f"路径不存在：{path}") from exc
    if stat.S_ISDIR(st.st_mode or 0):
        _rmtree(sftp, path)
    else:
        sftp.remove(path)


def sftp_chmod(sftp: paramiko.SFTPClient, path: str, mode: str) -> None:
    sftp.chmod(path, int(mode, 8))


def sftp_read_text(sftp: paramiko.SFTPClient, path: str, limit: int = 5 * 1024 * 1024) -> dict:
    try:
        st = sftp.stat(path)
    except IOError as exc:
        raise FileOpError(f"文件不存在：{path}") from exc
    if st.st_size and st.st_size > limit:
        raise FileOpError(f"文件过大（{human_bytes(st.st_size)}），请下载后编辑")
    with sftp.open(path, "rb") as fh:
        fh.set_pipelined(True)
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        encoding = "latin-1"
    return {"path": path, "content": text, "encoding": encoding, "size": len(raw), "mtime": now_iso()}


def sftp_write_text(sftp: paramiko.SFTPClient, path: str, content: str, encoding: str = "utf-8") -> dict:
    with sftp.open(path, "wb") as fh:
        fh.set_pipelined(True)
        fh.write(content.encode(encoding or "utf-8", "replace"))
    return {"path": path, "size": len(content.encode(encoding or "utf-8", "replace"))}


def sftp_upload(sftp: paramiko.SFTPClient, local_path: str, remote_path: str, progress=None) -> None:
    total = os.path.getsize(local_path)
    with open(local_path, "rb") as src, sftp.open(remote_path, "wb") as dst:
        dst.set_pipelined(True)
        sent = 0
        while True:
            chunk = src.read(262144)
            if not chunk:
                break
            dst.write(chunk)
            sent += len(chunk)
            if progress:
                progress(sent, total)
    try:
        sftp.chmod(remote_path, os.stat(local_path).st_mode & 0o777)
    except IOError:
        pass


def sftp_download(sftp: paramiko.SFTPClient, remote_path: str, local_path: str, progress=None) -> None:
    st = sftp.stat(remote_path)
    total = st.st_size or 0
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    with sftp.open(remote_path, "rb") as src, open(local_path, "wb") as dst:
        src.set_pipelined(True)
        received = 0
        while True:
            chunk = src.read(262144)
            if not chunk:
                break
            dst.write(chunk)
            received += len(chunk)
            if progress:
                progress(received, total)
    try:
        os.chmod(local_path, stat.S_IMODE(st.st_mode) or 0o644)
    except OSError:
        pass
