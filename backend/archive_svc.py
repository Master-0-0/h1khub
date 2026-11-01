"""Archive creation/extraction for the local and remote file panes.

Remote work runs the target host's own ``tar``/``zip``/``7z`` binaries over an
existing SSH transport (no upload round-trip); local work uses the Python
standard library and delegates ``7z``/``rar`` to a local binary when present.

Safety rules shared by both backends:

* every shell argument is quoted with :func:`shlex.quote`;
* only direct children of one directory can be packed, so a selection can
  never silently widen to a parent path;
* archive member names are listed and rejected when they are absolute or
  contain a ``..`` component, before anything is written below the
  destination directory.

Member scanning is bounded (entries and bytes); when the bound is reached the
result is marked ``partial`` and the host tool's own traversal protection is
the remaining backstop.
"""
from __future__ import annotations

import bz2
import gzip
import lzma
import os
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import zipfile

from util import human_bytes

MAX_MEMBERS = 200_000
MAX_SAMPLE = 8
MAX_RAW_BYTES = 16 * 1024 * 1024


class ArchiveError(Exception):
    """User-facing archive failure (mapped to HTTP 400)."""


# --------------------------------------------------------------------- formats
#: Formats offered for creation.  ``tools`` names the host binaries that must
#: exist; extraction may fall back to 7z when the native tool is absent.
CREATE_FORMATS: dict[str, dict] = {
    "tar.gz": {"label": "tar.gz (gzip)", "ext": ".tar.gz", "tools": ("tar",)},
    "zip": {"label": "zip", "ext": ".zip", "tools": ("zip",)},
    "tar.bz2": {"label": "tar.bz2 (bzip2)", "ext": ".tar.bz2", "tools": ("tar",)},
    "tar.xz": {"label": "tar.xz (xz)", "ext": ".tar.xz", "tools": ("tar",)},
    "tar": {"label": "tar", "ext": ".tar", "tools": ("tar",)},
    "7z": {"label": "7z", "ext": ".7z", "tools": ("7z",)},
}

CREATE_ORDER = ("tar.gz", "zip", "tar.bz2", "tar.xz", "tar", "7z")

#: Suffix -> extraction format.  Longest/most specific suffixes come first so
#: ``.tar.gz`` is not classified as ``.gz``.
EXTRACT_SUFFIXES: tuple[tuple[str, str], ...] = (
    (".tar.gz", "tar.gz"),
    (".tgz", "tar.gz"),
    (".tar.bz2", "tar.bz2"),
    (".tbz2", "tar.bz2"),
    (".tbz", "tar.bz2"),
    (".tar.xz", "tar.xz"),
    (".txz", "tar.xz"),
    (".tar.zst", "tar.zst"),
    (".tzst", "tar.zst"),
    (".tar", "tar"),
    (".zip", "zip"),
    (".7z", "7z"),
    (".rar", "rar"),
    (".gz", "gz"),
    (".bz2", "bz2"),
    (".xz", "xz"),
)

PROBE_TOOLS = ("tar", "gzip", "bzip2", "xz", "zstd", "zip", "unzip", "7z", "7za", "7zr", "unrar")
SEVEN_ZIP_NAMES = ("7z", "7za", "7zr")


# ------------------------------------------------------------------- helpers
def _q(value: str) -> str:
    return shlex.quote(str(value))


def _clamp_timeout(value, default: int = 600, maximum: int = 3600) -> int:
    try:
        timeout = int(value or default)
    except (TypeError, ValueError):
        timeout = default
    return max(10, min(timeout, maximum))


def _short(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def detect_format(name: str) -> str:
    """Return the extraction format for an archive file name ("" if unknown)."""
    lower = (name or "").lower()
    for suffix, fmt in EXTRACT_SUFFIXES:
        if lower.endswith(suffix):
            return fmt
    return ""


def is_archive_name(name: str) -> bool:
    return bool(detect_format(name))


def archive_stem(name: str) -> str:
    """Archive file name without its (known) archive suffix."""
    lower = (name or "").lower()
    for suffix, fmt in EXTRACT_SUFFIXES:
        if lower.endswith(suffix) and fmt == detect_format(name):
            stem = name[: len(name) - len(suffix)]
            return stem or "extracted"
    return name or "extracted"


def normalize_archive_name(name: str, fmt: str) -> str:
    """Validate an archive base name and append the format's extension."""
    info = CREATE_FORMATS.get(fmt)
    if not info:
        raise ArchiveError(f"不支持的压缩格式：{fmt}")
    clean = (name or "").strip()
    if not clean:
        clean = "archive"
    if "\x00" in clean or "/" in clean or "\\" in clean:
        raise ArchiveError("归档名称不能包含路径分隔符")
    if clean in (".", ".."):
        raise ArchiveError("归档名称无效")
    # Replace another archive suffix so picking zip on "backup.tar.gz" does not
    # silently produce "backup.tar.gz.zip".
    lower = clean.lower()
    for suffix, _ in EXTRACT_SUFFIXES:
        if lower.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    if not clean:
        clean = "archive"
    return clean + info["ext"]


def member_is_unsafe(name: str) -> bool:
    """True when an archive member could escape the destination directory."""
    raw = str(name or "").strip()
    if not raw:
        return False
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    if re.match(r"^[A-Za-z]:", normalized):
        return True
    return any(part == ".." for part in normalized.split("/") if part not in ("", "."))


def _finish_scan(names: list[str], truncated: bool) -> dict:
    names = [n for n in names if n]
    if len(names) > MAX_MEMBERS:
        names = names[:MAX_MEMBERS]
        truncated = True
    unsafe = [n for n in names if member_is_unsafe(n)]
    return {
        "members": len(names),
        "sample": names[:MAX_SAMPLE],
        "partial": bool(truncated),
        "unsafe": bool(unsafe),
        "unsafeSample": unsafe[:3],
    }


def _seven_zip(tools) -> str:
    for name in SEVEN_ZIP_NAMES:
        if name in tools:
            return name
    return ""


def seven_zip_name(tools) -> str:
    """Public helper for API responses (``""`` when 7z is unavailable)."""
    return _seven_zip(tools)


def _local_seven_zip() -> str:
    for name in SEVEN_ZIP_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return ""


def formats_for(tools) -> list[dict]:
    """Creation formats and whether the target host can build each one."""
    seven = _seven_zip(tools)
    items: list[dict] = []
    for fmt in CREATE_ORDER:
        info = CREATE_FORMATS[fmt]
        if fmt == "7z":
            missing = [] if seven else ["7z"]
        else:
            missing = [tool for tool in info["tools"] if tool not in tools]
        items.append(
            {
                "id": fmt,
                "label": info["label"],
                "ext": info["ext"],
                "available": not missing,
                "missingTools": missing,
            }
        )
    return items


def local_formats() -> list[dict]:
    seven = _local_seven_zip()
    items: list[dict] = []
    for fmt in CREATE_ORDER:
        info = CREATE_FORMATS[fmt]
        if fmt == "7z":
            missing = [] if seven else ["7z"]
        else:
            missing = []
        items.append(
            {
                "id": fmt,
                "label": info["label"],
                "ext": info["ext"],
                "available": not missing,
                "missingTools": missing,
            }
        )
    return items


# ------------------------------------------------------------- remote: probing
def probe_tools(exec_fn) -> set[str]:
    """List the archive tools installed on the target host.

    Uses ``which`` instead of a POSIX ``for`` loop so the probe also works when
    the login shell is fish/csh; any error output is ignored and only paths
    whose basename is a known tool are accepted.
    """
    script = "; ".join(f"which {name}" for name in PROBE_TOOLS)
    try:
        res = exec_fn(script, 20)
    except Exception as exc:
        raise ArchiveError(f"无法探测远端工具：{exc}") from exc
    tools: set[str] = set()
    for line in (res.get("stdout") or "").splitlines():
        for token in line.split():
            base = posixpath.basename(token.strip())
            if base in PROBE_TOOLS:
                tools.add(base)
    return tools


def _run(exec_fn, script: str, timeout: int) -> dict:
    try:
        res = exec_fn(script, timeout)
    except TimeoutError as exc:
        raise ArchiveError(f"命令执行超时（{timeout}s）") from exc
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError(f"远端命令执行失败：{exc}") from exc
    code = int(res.get("exitCode") or 0)
    if code != 0:
        detail = _short(res.get("stderr") or res.get("stdout") or "")
        raise ArchiveError(f"远端命令失败（退出码 {code}）：{detail or '无输出'}")
    return res


def _remote_norm(sftp, path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        raise ArchiveError("路径为空")
    if "\x00" in raw:
        raise ArchiveError("路径包含非法字符")
    try:
        resolved = sftp.normalize(raw)
    except IOError:
        if raw.startswith("~"):
            try:
                home = sftp.normalize(".")
            except IOError:
                home = "/"
            resolved = home + raw[1:]
        elif raw.startswith("/"):
            resolved = raw
        else:
            try:
                resolved = sftp.normalize(".") + "/" + raw
            except IOError:
                resolved = "/" + raw
    resolved = posixpath.normpath(resolved)
    return resolved or "/"


def _entry_names_remote(sftp, paths) -> tuple[str, list[str]]:
    cleaned: list[str] = []
    for raw in paths or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        full = _remote_norm(sftp, raw)
        if full == "/":
            raise ArchiveError("不能压缩根目录")
        cleaned.append(full)
    if not cleaned:
        raise ArchiveError("请先选择要压缩的文件或文件夹")
    parents = {posixpath.dirname(path) for path in cleaned}
    if len(parents) != 1:
        raise ArchiveError("只能压缩同一目录下的文件或文件夹")
    parent = parents.pop()
    names = [posixpath.basename(path) for path in cleaned]
    if len(set(names)) != len(names):
        raise ArchiveError("选择中包含重复项目")
    if any(name in ("", ".", "..") for name in names):
        raise ArchiveError("选择中包含无效文件名")
    return parent, names


def _remote_is_dir(sftp, path: str) -> bool:
    try:
        return stat.S_ISDIR(sftp.stat(path).st_mode or 0)
    except IOError:
        return False


def _remote_exists(sftp, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except IOError:
        return False


def _remote_remove(sftp, path: str) -> None:
    try:
        sftp.remove(path)
    except IOError:
        pass


def _join_remote(directory: str, name: str) -> str:
    return "/" + name if directory == "/" else posixpath.join(directory, name)


# ----------------------------------------------------------- remote: create
def remote_compress(
    sftp,
    exec_fn,
    paths,
    target_dir: str,
    name: str,
    fmt: str,
    overwrite: bool = False,
    timeout=600,
) -> dict:
    """Create an archive on the remote host from selected entries."""
    started = time.time()
    timeout = _clamp_timeout(timeout)
    if fmt not in CREATE_FORMATS:
        raise ArchiveError(f"不支持的压缩格式：{fmt}")
    parent, names = _entry_names_remote(sftp, paths)
    target_dir = _remote_norm(sftp, target_dir or parent)
    if not _remote_is_dir(sftp, target_dir):
        raise ArchiveError(f"目标目录不存在：{target_dir}")
    archive_name = normalize_archive_name(name, fmt)
    target = _join_remote(target_dir, archive_name)
    sources = {_join_remote(parent, entry) for entry in names}
    if target in sources:
        raise ArchiveError("归档文件不能与选中的源文件同名")
    for source in sources:
        if target.startswith(source.rstrip("/") + "/"):
            raise ArchiveError("归档不能保存在被压缩的目录内，请改选其他目录")
    if _remote_is_dir(sftp, target):
        raise ArchiveError(f"目标路径是目录：{target}")
    if _remote_exists(sftp, target) and not overwrite:
        raise ArchiveError(f"目标归档已存在：{target}")

    tools = probe_tools(exec_fn)
    seven = _seven_zip(tools)
    if fmt == "7z":
        if not seven:
            raise ArchiveError("远端未安装 7z，无法创建 7z 归档")
        command = (
            f"cd {_q(parent)} && {_q(seven)} a -bso0 -bsp0 -t7z {_q(target)} -- "
            + " ".join(_q(entry) for entry in names)
        )
        tool = seven
    elif fmt == "zip":
        if "zip" not in tools:
            raise ArchiveError("远端未安装 zip，无法创建 zip 归档")
        command = (
            f"cd {_q(parent)} && zip -r -q {_q(target)} -- "
            + " ".join(_q(entry) for entry in names)
        )
        tool = "zip"
    else:
        if "tar" not in tools:
            raise ArchiveError("远端未安装 tar，无法创建 tar 归档")
        flag = {"tar.gz": "z", "tar.bz2": "j", "tar.xz": "J", "tar": ""}[fmt]
        command = (
            f"tar -c{flag}f {_q(target)} -C {_q(parent)} -- "
            + " ".join(_q(entry) for entry in names)
        )
        tool = "tar"

    try:
        _run(exec_fn, command, timeout)
    except ArchiveError:
        if overwrite or not _remote_exists(sftp, target):
            _remote_remove(sftp, target)
        raise
    try:
        attrs = sftp.stat(target)
    except IOError as exc:
        raise ArchiveError("压缩命令已执行，但目标归档不可见（检查远端磁盘空间与权限）") from exc
    size = int(attrs.st_size or 0)
    return {
        "path": target,
        "name": archive_name,
        "format": fmt,
        "tool": tool,
        "source": names,
        "count": len(names),
        "size": size,
        "sizeText": human_bytes(size),
        "overwrite": bool(overwrite),
        "elapsedMs": int((time.time() - started) * 1000),
    }


# ------------------------------------------------- remote: inspect/extract
def _extract_tool(fmt: str, tools, seven: str) -> tuple[bool, str]:
    if fmt in ("tar.gz", "tar.bz2", "tar.xz", "tar"):
        if "tar" in tools:
            return True, "tar"
        if seven:
            return True, seven
        return False, ""
    if fmt == "tar.zst":
        if "tar" in tools and "zstd" in tools:
            return True, "tar"
        if seven:
            return True, seven
        return False, ""
    if fmt == "zip":
        if "unzip" in tools:
            return True, "unzip"
        if seven:
            return True, seven
        return False, ""
    if fmt == "7z":
        return (True, seven) if seven else (False, "")
    if fmt == "rar":
        if "unrar" in tools:
            return True, "unrar"
        if seven:
            return True, seven
        return False, ""
    if fmt in ("gz", "bz2", "xz"):
        native = {"gz": "gzip", "bz2": "bzip2", "xz": "xz"}[fmt]
        if native in tools:
            return True, native
        if seven:
            return True, seven
        return False, ""
    return False, ""


def _scan_remote(exec_fn, archive: str, fmt: str, tool: str, timeout: int) -> dict:
    if fmt in ("gz", "bz2", "xz"):
        return _finish_scan([archive_stem(posixpath.basename(archive))], False)
    if tool == "tar":
        command = f"env LC_ALL=C tar -tf {_q(archive)}"
    elif tool == "unzip":
        command = f"env LC_ALL=C unzip -Z1 -- {_q(archive)}"
    elif tool == "unrar":
        command = f"env LC_ALL=C unrar lb -- {_q(archive)}"
    else:
        command = f"env LC_ALL=C {_q(tool)} l -slt -ba -- {_q(archive)}"
    res = _run(exec_fn, command, timeout)
    output = res.get("stdout") or ""
    truncated = bool(res.get("truncated"))
    if tool in SEVEN_ZIP_NAMES:
        names = [line[7:] for line in output.splitlines() if line.startswith("Path = ")]
    else:
        names = output.splitlines()
    return _finish_scan(names, truncated)


def remote_inspect(sftp, exec_fn, path: str, timeout=60) -> dict:
    """Describe an archive without extracting it (member count + safety)."""
    timeout = _clamp_timeout(timeout, default=120)
    archive = _remote_norm(sftp, path)
    try:
        attrs = sftp.stat(archive)
    except IOError as exc:
        raise ArchiveError(f"归档不存在：{archive}") from exc
    if stat.S_ISDIR(attrs.st_mode or 0):
        raise ArchiveError("请选择一个归档文件")
    name = posixpath.basename(archive)
    fmt = detect_format(name)
    if not fmt:
        raise ArchiveError(f"无法识别归档格式：{name}")
    tools = probe_tools(exec_fn)
    seven = _seven_zip(tools)
    supported, tool = _extract_tool(fmt, tools, seven)
    if not supported:
        raise ArchiveError(f"远端缺少解压 {fmt} 所需的工具")
    size = int(attrs.st_size or 0)
    scan = _scan_remote(exec_fn, archive, fmt, tool, timeout)
    return {
        "path": archive,
        "name": name,
        "format": fmt,
        "tool": tool,
        "size": size,
        "sizeText": human_bytes(size),
        **scan,
    }


def _extract_command(fmt: str, tool: str, archive: str, dest: str, overwrite: bool) -> str:
    if tool == "tar":
        if fmt == "tar.zst":
            command = f"tar --zstd -xf {_q(archive)} -C {_q(dest)}"
        else:
            flag = {"tar.gz": "z", "tar.bz2": "j", "tar.xz": "J", "tar": ""}[fmt]
            command = f"tar -x{flag}f {_q(archive)} -C {_q(dest)}"
        command += " --no-same-owner"
        if not overwrite:
            command += " --skip-old-files"
        return command
    if tool == "unzip":
        return f"unzip -q {'-o' if overwrite else '-n'} {_q(archive)} -d {_q(dest)}"
    if tool in SEVEN_ZIP_NAMES:
        mode = "-y" if overwrite else "-aos"
        return f"{_q(tool)} x {mode} -bso0 -bsp0 -o{_q(dest)} -- {_q(archive)}"
    if tool == "unrar":
        mode = "-o+" if overwrite else "-o-"
        return f"unrar x {mode} -idq {_q(archive)} {_q(dest)}/"
    raise ArchiveError(f"不支持的解压工具：{tool}")


def remote_extract(
    sftp,
    exec_fn,
    path: str,
    target_dir: str = "",
    create: bool = True,
    overwrite: bool = True,
    timeout=600,
) -> dict:
    """Extract an archive on the remote host into ``target_dir``."""
    started = time.time()
    timeout = _clamp_timeout(timeout)
    archive = _remote_norm(sftp, path)
    try:
        attrs = sftp.stat(archive)
    except IOError as exc:
        raise ArchiveError(f"归档不存在：{archive}") from exc
    if stat.S_ISDIR(attrs.st_mode or 0):
        raise ArchiveError("请选择一个归档文件而不是目录")
    name = posixpath.basename(archive)
    fmt = detect_format(name)
    if not fmt:
        raise ArchiveError(f"无法识别归档格式：{name}")
    dest = _remote_norm(
        sftp,
        target_dir or posixpath.join(posixpath.dirname(archive), archive_stem(name)),
    )
    dest_exists = _remote_is_dir(sftp, dest)
    if not dest_exists and not create:
        raise ArchiveError(f"目标目录不存在：{dest}")
    tools = probe_tools(exec_fn)
    seven = _seven_zip(tools)
    supported, tool = _extract_tool(fmt, tools, seven)
    if not supported:
        raise ArchiveError(f"远端缺少解压 {fmt} 所需的工具")
    scan = _scan_remote(exec_fn, archive, fmt, tool, timeout)
    if scan["unsafe"]:
        raise ArchiveError(
            "归档包含越界路径，已拒绝解压：" + ", ".join(scan["unsafeSample"])
        )
    if not dest_exists:
        _run(exec_fn, f"mkdir -p {_q(dest)}", timeout)
    command = _extract_command(fmt, tool, archive, dest, overwrite)
    _run(exec_fn, command, timeout)
    return {
        "path": archive,
        "targetDir": dest,
        "format": fmt,
        "tool": tool,
        "members": scan["members"],
        "memberCheck": "partial" if scan["partial"] else "full",
        "overwrite": bool(overwrite),
        "elapsedMs": int((time.time() - started) * 1000),
    }


# --------------------------------------------------------------- local paths
def _local_norm(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        raise ArchiveError("路径为空")
    if "\x00" in raw:
        raise ArchiveError("路径包含非法字符")
    return os.path.abspath(os.path.expanduser(raw))


def _entry_names_local(paths) -> tuple[str, list[str]]:
    cleaned: list[str] = []
    for raw in paths or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        full = _local_norm(raw)
        if full == os.path.sep:
            raise ArchiveError("不能压缩根目录")
        cleaned.append(full)
    if not cleaned:
        raise ArchiveError("请先选择要压缩的文件或文件夹")
    parents = {os.path.dirname(path) for path in cleaned}
    if len(parents) != 1:
        raise ArchiveError("只能压缩同一目录下的文件或文件夹")
    parent = parents.pop()
    names = [os.path.basename(path) for path in cleaned]
    if len(set(names)) != len(names):
        raise ArchiveError("选择中包含重复项目")
    if any(name in ("", ".", "..") for name in names):
        raise ArchiveError("选择中包含无效文件名")
    return parent, names


def _zip_add(zf: zipfile.ZipFile, source: str, arcname: str) -> None:
    if os.path.islink(source) and os.path.isdir(source):
        info = zipfile.ZipInfo(arcname.rstrip("/") + "/")
        info.external_attr = (0o40755 << 16) | 0x10
        zf.writestr(info, b"")
        return
    if os.path.isdir(source):
        zf.write(source, arcname.rstrip("/") + "/")
        for entry in sorted(os.listdir(source)):
            _zip_add(zf, os.path.join(source, entry), arcname.rstrip("/") + "/" + entry)
        return
    zf.write(source, arcname)


def local_compress(
    paths,
    target_dir: str,
    name: str,
    fmt: str,
    overwrite: bool = False,
) -> dict:
    """Create an archive on the local disk from selected entries."""
    started = time.time()
    if fmt not in CREATE_FORMATS:
        raise ArchiveError(f"不支持的压缩格式：{fmt}")
    parent, names = _entry_names_local(paths)
    target_dir = _local_norm(target_dir or parent)
    if not os.path.isdir(target_dir):
        raise ArchiveError(f"目标目录不存在：{target_dir}")
    archive_name = normalize_archive_name(name, fmt)
    target = os.path.join(target_dir, archive_name)
    sources = {os.path.join(parent, entry) for entry in names}
    target_real = os.path.realpath(target)
    for source in sources:
        source_real = os.path.realpath(source)
        if target_real == source_real:
            raise ArchiveError("归档文件不能与选中的源文件同名")
        if target_real.startswith(source_real.rstrip(os.sep) + os.sep):
            raise ArchiveError("归档不能保存在被压缩的目录内，请改选其他目录")
    if os.path.isdir(target):
        raise ArchiveError(f"目标路径是目录：{target}")
    if os.path.exists(target) and not overwrite:
        raise ArchiveError(f"目标归档已存在：{target}")
    tool = fmt
    try:
        if fmt in ("tar.gz", "tar.bz2", "tar.xz", "tar"):
            mode = {"tar.gz": "w:gz", "tar.bz2": "w:bz2", "tar.xz": "w:xz", "tar": "w"}[fmt]
            with tarfile.open(target, mode, format=tarfile.PAX_FORMAT) as archive:
                for entry in names:
                    archive.add(os.path.join(parent, entry), arcname=entry, recursive=True)
            tool = "python-tar"
        elif fmt == "zip":
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for entry in names:
                    _zip_add(archive, os.path.join(parent, entry), entry)
            tool = "python-zip"
        else:
            seven = _local_seven_zip()
            if not seven:
                raise ArchiveError("本机未安装 7z，无法创建 7z 归档")
            _run_local(
                [seven, "a", "-bso0", "-bsp0", "-t7z", target, "--", *names],
                cwd=parent,
            )
            tool = seven
    except ArchiveError:
        _remove_quiet(target)
        raise
    except Exception as exc:
        _remove_quiet(target)
        raise ArchiveError(f"压缩失败：{exc}") from exc
    size = os.path.getsize(target)
    return {
        "path": target,
        "name": archive_name,
        "format": fmt,
        "tool": tool,
        "source": names,
        "count": len(names),
        "size": size,
        "sizeText": human_bytes(size),
        "overwrite": bool(overwrite),
        "elapsedMs": int((time.time() - started) * 1000),
    }


def _remove_quiet(path: str) -> None:
    try:
        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
    except OSError:
        pass


def _run_local(argv: list[str], cwd: str | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ArchiveError(f"未找到命令：{argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ArchiveError(f"命令执行超时（{timeout}s）") from exc
    if result.returncode != 0:
        detail = _short((result.stderr or result.stdout or b"").decode("utf-8", "replace"))
        raise ArchiveError(f"命令失败（退出码 {result.returncode}）：{detail or '无输出'}")
    return result


def _scan_local(archive: str, fmt: str) -> dict:
    if fmt in ("gz", "bz2", "xz"):
        return _finish_scan([archive_stem(os.path.basename(archive))], False)
    if fmt in ("tar.gz", "tar.bz2", "tar.xz", "tar"):
        try:
            with tarfile.open(archive, "r:*") as handle:
                return _finish_scan(handle.getnames(), False)
        except tarfile.TarError as exc:
            raise ArchiveError(f"读取归档失败：{exc}") from exc
    if fmt == "zip":
        try:
            with zipfile.ZipFile(archive) as handle:
                return _finish_scan([info.filename for info in handle.infolist()], False)
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"读取归档失败：{exc}") from exc
    seven = _local_seven_zip()
    if not seven:
        raise ArchiveError(f"本机未安装 7z，无法处理 {fmt} 归档")
    result = _run_local([seven, "l", "-slt", "-ba", "--", archive], timeout=120)
    output = (result.stdout or b"").decode("utf-8", "replace")
    names = [line[7:] for line in output.splitlines() if line.startswith("Path = ")]
    return _finish_scan(names, False)


def local_inspect(path: str) -> dict:
    archive = _local_norm(path)
    if not os.path.exists(archive):
        raise ArchiveError(f"归档不存在：{archive}")
    if os.path.isdir(archive):
        raise ArchiveError("请选择一个归档文件")
    name = os.path.basename(archive)
    fmt = detect_format(name)
    if not fmt:
        raise ArchiveError(f"无法识别归档格式：{name}")
    size = os.path.getsize(archive)
    scan = _scan_local(archive, fmt)
    return {
        "path": archive,
        "name": name,
        "format": fmt,
        "size": size,
        "sizeText": human_bytes(size),
        **scan,
    }


def _within(root: str, candidate: str) -> bool:
    root_abs = os.path.abspath(root)
    candidate_abs = os.path.abspath(candidate)
    try:
        return os.path.commonpath([root_abs, candidate_abs]) == root_abs
    except ValueError:
        return False


def _local_tar_extract(archive: str, dest: str, overwrite: bool) -> None:
    with tarfile.open(archive, "r:*") as handle:
        members = handle.getmembers()
        for member in members:
            target = os.path.join(dest, member.name)
            if not _within(dest, target):
                raise ArchiveError(f"归档包含越界路径：{member.name}")
            if member.issym():
                # Symlink targets are relative to the directory holding the link.
                link = os.path.normpath(
                    os.path.join(dest, os.path.dirname(member.name), member.linkname)
                )
                if os.path.isabs(member.linkname) or not _within(dest, link):
                    raise ArchiveError(f"归档包含越界链接：{member.linkname}")
            elif member.islnk():
                # Hard links are archive-root relative.
                if os.path.isabs(member.linkname) or not _within(
                    dest, os.path.join(dest, member.linkname)
                ):
                    raise ArchiveError(f"归档包含越界链接：{member.linkname}")
        if overwrite:
            if sys.version_info >= (3, 12):
                handle.extractall(dest, filter="data")
            else:  # pragma: no cover - the shipped runtime is 3.13
                handle.extractall(dest)
            return
        for member in members:
            target = os.path.join(dest, member.name)
            if not member.isdir() and os.path.exists(target):
                continue
            if sys.version_info >= (3, 12):
                handle.extract(member, dest, filter="data")
            else:  # pragma: no cover
                handle.extract(member, dest)


def _local_zip_extract(archive: str, dest: str, overwrite: bool) -> None:
    with zipfile.ZipFile(archive) as handle:
        infos = handle.infolist()
        for info in infos:
            if not _within(dest, os.path.join(dest, info.filename)):
                raise ArchiveError(f"归档包含越界路径：{info.filename}")
        if overwrite:
            handle.extractall(dest)
            return
        for info in infos:
            target = os.path.join(dest, info.filename)
            if os.path.isdir(target) or os.path.exists(target):
                continue
            handle.extract(info, dest)


def _local_7z_extract(archive: str, dest: str, overwrite: bool) -> None:
    seven = _local_seven_zip()
    if not seven:
        raise ArchiveError("本机未安装 7z，无法解压该归档")
    mode = "-y" if overwrite else "-aos"
    _run_local([seven, "x", mode, "-bso0", "-bsp0", f"-o{dest}", "--", archive])


def local_extract(
    path: str,
    target_dir: str = "",
    create: bool = True,
    overwrite: bool = True,
) -> dict:
    started = time.time()
    archive = _local_norm(path)
    if not os.path.exists(archive):
        raise ArchiveError(f"归档不存在：{archive}")
    if os.path.isdir(archive):
        raise ArchiveError("请选择一个归档文件而不是目录")
    name = os.path.basename(archive)
    fmt = detect_format(name)
    if not fmt:
        raise ArchiveError(f"无法识别归档格式：{name}")
    dest = _local_norm(target_dir) if target_dir else os.path.join(
        os.path.dirname(archive), archive_stem(name)
    )
    if os.path.exists(dest) and not os.path.isdir(dest):
        raise ArchiveError(f"目标路径不是目录：{dest}")
    dest_exists = os.path.isdir(dest)
    if not dest_exists and not create:
        raise ArchiveError(f"目标目录不存在：{dest}")
    scan = _scan_local(archive, fmt)
    if scan["unsafe"]:
        raise ArchiveError(
            "归档包含越界路径，已拒绝解压：" + ", ".join(scan["unsafeSample"])
        )
    if not dest_exists:
        os.makedirs(dest, exist_ok=True)
    try:
        if fmt in ("tar.gz", "tar.bz2", "tar.xz", "tar"):
            _local_tar_extract(archive, dest, overwrite)
        elif fmt == "zip":
            _local_zip_extract(archive, dest, overwrite)
        elif fmt in ("gz", "bz2", "xz"):
            opener = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open}[fmt]
            output = os.path.join(dest, archive_stem(name))
            if os.path.exists(output) and not overwrite:
                raise ArchiveError(f"目标文件已存在：{output}")
            with opener(archive, "rb") as src, open(output, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
        else:
            _local_7z_extract(archive, dest, overwrite)
    except ArchiveError:
        raise
    except Exception as exc:
        raise ArchiveError(f"解压失败：{exc}") from exc
    return {
        "path": archive,
        "targetDir": dest,
        "format": fmt,
        "tool": "python" if fmt in ("tar.gz", "tar.bz2", "tar.xz", "tar", "zip", "gz", "bz2", "xz") else _local_seven_zip(),
        "members": scan["members"],
        "memberCheck": "partial" if scan["partial"] else "full",
        "overwrite": bool(overwrite),
        "elapsedMs": int((time.time() - started) * 1000),
    }
