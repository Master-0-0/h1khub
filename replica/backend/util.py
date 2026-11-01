"""Shared helpers for the H1KHub replica backend."""
from __future__ import annotations

import base64
import datetime as _dt
import decimal
import hashlib
import io
import json
import os
import re
import time
import unicodedata
import uuid
from typing import Any

APP_NAME = "H1KHub"
APP_VERSION = "5.1.9"
APP_BUILD = 519


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex
    return f"{prefix}{raw[:12]}" if prefix else raw


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def human_bytes(n: int | float | None) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def clamp_text(value: Any, limit: int = 200_000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n… [truncated, {len(value) - limit} more chars]"


def b64encode_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode_str(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-_]")


def strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)


def safe_filename(name: str, fallback: str = "download") -> str:
    name = unicodedata.normalize("NFKC", str(name or "")).strip()
    name = name.replace("\x00", "")
    name = re.sub(r"[\\/]+", "_", name)
    if name in ("", ".", ".."):
        return fallback
    return name[:200]


def md5_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    return hashlib.md5(data).hexdigest()


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def jsonable(value: Any) -> Any:
    """Convert driver values into JSON-safe primitives with typed wrappers."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "__type": "blob",
            "length": len(raw),
            "preview": raw[:64].hex(),
            "base64": base64.b64encode(raw).decode("ascii") if len(raw) <= 262144 else None,
        }
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat(sep=" ")
    if isinstance(value, _dt.timedelta):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return str(value)


def truncate_rows(columns: list[str], rows: list[list[Any]], limit: int = 500) -> dict:
    total = len(rows)
    sliced = rows[:limit]
    return {
        "columns": columns,
        "rows": sliced,
        "rowCount": total,
        "truncated": total > limit,
    }


class Timer:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)


def jobj(data: bytes | str) -> dict:
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    return json.loads(data or "{}")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def file_etag(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{st.st_mtime_ns}-{st.st_size}"
    except OSError:
        return ""


def guess_mime(path: str) -> str:
    import mimetypes

    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def iter_chunks(buf: io.BufferedReader, size: int = 256 * 1024):
    while True:
        chunk = buf.read(size)
        if not chunk:
            break
        yield chunk


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
