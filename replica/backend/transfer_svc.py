"""Cross-server file transfer over SFTP (server A -> server B)."""
from __future__ import annotations

import os
import posixpath
import stat


class TransferError(Exception):
    pass


def _is_dir(sftp, path: str) -> bool:
    try:
        return stat.S_ISDIR(sftp.stat(path).st_mode)
    except IOError:
        return False


def _ensure_dir(sftp, path: str) -> None:
    parts = [p for p in path.strip("/").split("/") if p]
    current = "/" if path.startswith("/") else ""
    for part in parts:
        current = posixpath.join(current, part) if current else part
        try:
            sftp.stat(current)
        except IOError:
            try:
                sftp.mkdir(current)
            except IOError as exc:
                raise TransferError(f"无法创建目标目录 {current}：{exc}") from exc


def transfer(
    src_sftp,
    dst_sftp,
    src_path: str,
    dst_path: str,
    overwrite: bool = True,
    max_files: int = 5000,
) -> dict:
    stats = {"files": 0, "dirs": 0, "bytes": 0, "skipped": []}

    def copy_one(source: str, target: str) -> None:
        if stats["files"] >= max_files:
            raise TransferError(f"文件数超过上限 {max_files}")
        try:
            st = src_sftp.stat(source)
        except IOError as exc:
            raise TransferError(f"源文件不存在：{source}") from exc
        if not overwrite:
            try:
                dst_sftp.stat(target)
                stats["skipped"].append(target)
                return
            except IOError:
                pass
        _ensure_dir(dst_sftp, posixpath.dirname(target))
        with src_sftp.open(source, "rb") as src, dst_sftp.open(target, "wb") as dst:
            try:
                src.set_pipelined(True)
            except Exception:
                pass
            try:
                dst.set_pipelined(True)
            except Exception:
                pass
            while True:
                chunk = src.read(262144)
                if not chunk:
                    break
                dst.write(chunk)
                stats["bytes"] += len(chunk)
        try:
            dst_sftp.chmod(target, stat.S_IMODE(st.st_mode) or 0o644)
        except IOError:
            pass
        stats["files"] += 1

    def walk(source: str, target: str) -> None:
        st = src_sftp.stat(source)
        if stat.S_ISDIR(st.st_mode):
            _ensure_dir(dst_sftp, target)
            stats["dirs"] += 1
            for entry in src_sftp.listdir_attr(source):
                if entry.filename in (".", ".."):
                    continue
                walk(posixpath.join(source, entry.filename), posixpath.join(target, entry.filename))
        else:
            copy_one(source, target)

    walk(src_path, dst_path)
    return stats
