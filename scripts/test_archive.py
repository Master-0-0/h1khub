#!/usr/bin/env python3
"""End-to-end tests for archive compression/extraction.

Covers the SFTP file pane (SSH session and saved SSH asset targets) and the
local file pane, including format round-trips, overwrite/skip semantics,
selection validation and archive-slip rejection.

Usage: python3 scripts/test_archive.py [--base http://127.0.0.1:38110]
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import random
import shlex
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE = "http://127.0.0.1:38110"
TOKEN = ""
PASS = 0
FAIL = 0
FAILURES: list[str] = []

SSH_ASSET = ""
SSH_ASSET_NAME = "SSH hexlab"
SESSION_ID = ""
REMOTE_ROOT = ""
LOCAL_ROOT = ""


def load_token() -> str:
    home = os.path.expanduser(os.environ.get("H1KHUB_HOME") or "~/.h1khub")
    with open(os.path.join(home, "runtime.json"), encoding="utf-8") as fh:
        return json.load(fh)["token"]


def req(path: str, body=None, method="POST", raw=False, query=None, timeout=180):
    url = BASE + path + (("?" + urllib.parse.urlencode(query)) if query else "")
    if raw:
        data = body
        headers = {"X-H1KHub-Token": TOKEN, "Content-Type": "application/octet-stream"}
    else:
        data = None if body is None else json.dumps(body).encode()
        headers = {"X-H1KHub-Token": TOKEN, "Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = resp.read()
            return payload if raw else json.loads(payload)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            detail = json.loads(text)
        except Exception:
            detail = text
        return {"__http_error": exc.code, "detail": detail}


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def data_of(resp):
    if isinstance(resp, dict) and resp.get("ok") and "data" in resp:
        return resp["data"]
    return None


def is_ok(resp) -> bool:
    return isinstance(resp, dict) and resp.get("ok") is True and "__http_error" not in resp


def error_of(resp) -> str:
    if not isinstance(resp, dict):
        return str(resp)
    if resp.get("__http_error"):
        detail = resp.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("error") or detail)
        return str(detail)
    return json.dumps(resp, ensure_ascii=False)[:300]


def is_http_error(resp, code: int = 400) -> bool:
    return isinstance(resp, dict) and resp.get("__http_error") == code


def q(value: str) -> str:
    return shlex.quote(str(value))


def find_assets() -> dict:
    data = data_of(req("/api/assets", method="GET"))
    return {asset["name"]: asset for asset in (data or {}).get("assets", [])}


def sh(command: str, timeout=90):
    resp = req("/api/batch/exec", {"assetIds": [SSH_ASSET], "command": command}, timeout=timeout)
    data = data_of(resp)
    if not data:
        raise RuntimeError(f"batch exec failed: {error_of(resp)}")
    result = data["results"][0]
    if not result.get("ok"):
        raise RuntimeError(f"remote command failed: {result.get('stderr') or result.get('error')}")
    return result


def remote_read(path: str) -> bytes:
    return sh(f"cat {q(path)}")["stdout"].encode()


def remote_exists(path: str) -> bool:
    result = sh(f"test -e {q(path)} && echo YES || echo NO")
    return "YES" in (result.get("stdout") or "")


def sftp(path, body, method="POST", timeout=180):
    return req(path, body, method=method, timeout=timeout)


def upload(path: str, payload: bytes, query: dict) -> dict:
    url = BASE + path + "?" + urllib.parse.urlencode(query)
    headers = {"X-H1KHub-Token": TOKEN, "Content-Type": "application/octet-stream"}
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"__http_error": exc.code, "detail": exc.read().decode(errors="replace")}


# --------------------------------------------------------------------- setup
def start_session() -> str:
    resp = req("/api/sessions/start", {"assetId": SSH_ASSET, "cols": 90, "rows": 24})
    data = data_of(resp)
    if not data:
        raise RuntimeError(f"cannot start SSH session: {error_of(resp)}")
    return data["session"]["id"]


def make_remote_fixture() -> None:
    sub = f"{REMOTE_ROOT}/proj/sub dir"
    other = f"{REMOTE_ROOT}/other"
    script = "\n".join(
        [
            f"rm -rf {q(REMOTE_ROOT)}",
            f"mkdir -p {q(sub)} {q(other)}",
            f"printf 'one' > {q(REMOTE_ROOT + '/proj/a.txt')}",
            f"printf 'two' > {q(sub + '/b.txt')}",
            f"printf 'uni' > {q(REMOTE_ROOT + '/proj/文件 名.txt')}",
            f"printf 'keep-new' > {q(REMOTE_ROOT + '/proj/keep.txt')}",
            f"printf 'elsewhere' > {q(other + '/c.txt')}",
        ]
    )
    sh(script)


def make_local_fixture() -> None:
    proj = os.path.join(LOCAL_ROOT, "proj")
    os.makedirs(os.path.join(proj, "sub dir"), exist_ok=True)
    os.makedirs(os.path.join(LOCAL_ROOT, "other"), exist_ok=True)
    with open(os.path.join(proj, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("one")
    with open(os.path.join(proj, "sub dir", "b.txt"), "w", encoding="utf-8") as fh:
        fh.write("two")
    with open(os.path.join(proj, "文件 名.txt"), "w", encoding="utf-8") as fh:
        fh.write("uni")
    with open(os.path.join(proj, "keep.txt"), "w", encoding="utf-8") as fh:
        fh.write("keep-new")
    with open(os.path.join(LOCAL_ROOT, "other", "c.txt"), "w", encoding="utf-8") as fh:
        fh.write("elsewhere")


def local_read(path: str) -> str:
    resp = req("/api/fs/read", {"path": path})
    return (data_of(resp) or {}).get("content", "")


# -------------------------------------------------------------- remote tests
def test_remote_formats() -> set[str]:
    print("\n[remote: formats]")
    data = data_of(sftp("/api/sftp/archive/formats", {"sessionId": SESSION_ID}))
    check("sftp: formats endpoint", bool(data), error_of(data))
    formats = {item["id"]: item for item in (data or {}).get("formats", [])}
    check("sftp: tar.gz offered", formats.get("tar.gz", {}).get("available") is True)
    check("sftp: zip offered", formats.get("zip", {}).get("available") is True)
    check("sftp: 7z offered", formats.get("7z", {}).get("available") is True, str(formats.get("7z")))
    check("sftp: tar tool reported", "tar" in (data or {}).get("tools", []))
    return {fid for fid, item in formats.items() if item.get("available")}


def remote_compress(session: bool = True, **overrides):
    body = {
        "sessionId": SESSION_ID,
        "paths": [f"{REMOTE_ROOT}/proj/a.txt", f"{REMOTE_ROOT}/proj/sub dir"],
        "targetDir": f"{REMOTE_ROOT}/proj",
        "name": "bundle",
        "format": "tar.gz",
        "overwrite": False,
    }
    body.update(overrides)
    if not session:
        body.pop("sessionId", None)
        body["assetId"] = SSH_ASSET
    return sftp("/api/sftp/archive/compress", body)


def remote_extract(archive: str, target: str, create=True, overwrite=True, session: bool = True):
    body = {
        "sessionId": SESSION_ID,
        "path": archive,
        "targetDir": target,
        "create": create,
        "overwrite": overwrite,
    }
    if not session:
        body.pop("sessionId", None)
        body["assetId"] = SSH_ASSET
    return sftp("/api/sftp/archive/extract", body)


def remote_inspect(archive: str, session: bool = True):
    body = {"path": archive}
    if session:
        body["sessionId"] = SESSION_ID
    else:
        body["assetId"] = SSH_ASSET
    return sftp("/api/sftp/archive/inspect", body)


def test_remote_roundtrips(formats: set[str]) -> None:
    print("\n[remote: tar.gz]")
    resp = remote_compress()
    data = data_of(resp)
    check("sftp: compress tar.gz", is_ok(resp), error_of(resp))
    check("sftp: tar.gz name", (data or {}).get("name") == "bundle.tar.gz", str(data))
    check("sftp: tar.gz count", (data or {}).get("count") == 2, str(data))
    check("sftp: tar.gz non-empty", (data or {}).get("size", 0) > 0, str(data))

    listing = data_of(sftp("/api/sftp/list", {"sessionId": SESSION_ID, "path": f"{REMOTE_ROOT}/proj"}))
    names = [entry["name"] for entry in (listing or {}).get("entries", [])]
    check("sftp: archive visible in listing", "bundle.tar.gz" in names, str(names))

    inspect = data_of(remote_inspect(f"{REMOTE_ROOT}/proj/bundle.tar.gz"))
    check("sftp: inspect tar.gz", (inspect or {}).get("format") == "tar.gz", str(inspect))
    check("sftp: inspect member count", (inspect or {}).get("members", 0) >= 3, str(inspect))
    check("sftp: inspect safe", (inspect or {}).get("unsafe") is False, str(inspect))

    out = f"{REMOTE_ROOT}/out-targz"
    extracted = data_of(remote_extract(f"{REMOTE_ROOT}/proj/bundle.tar.gz", out))
    check("sftp: extract tar.gz", extracted is not None, error_of(extracted))
    check("sftp: tar.gz content", remote_read(f"{out}/a.txt") == b"one")
    check("sftp: tar.gz nested content", remote_read(f"{out}/sub dir/b.txt") == b"two")

    print("\n[remote: zip]")
    resp = remote_compress(
        paths=[
            f"{REMOTE_ROOT}/proj/a.txt",
            f"{REMOTE_ROOT}/proj/sub dir",
            f"{REMOTE_ROOT}/proj/文件 名.txt",
        ],
        name="pack",
        format="zip",
    )
    check("sftp: compress zip", is_ok(resp), error_of(resp))
    check("sftp: zip extension appended", (data_of(resp) or {}).get("name") == "pack.zip")
    inspect = data_of(remote_inspect(f"{REMOTE_ROOT}/proj/pack.zip"))
    check("sftp: inspect zip", (inspect or {}).get("format") == "zip", str(inspect))
    out = f"{REMOTE_ROOT}/out-zip"
    check("sftp: extract zip", data_of(remote_extract(f"{REMOTE_ROOT}/proj/pack.zip", out)) is not None)
    check("sftp: zip unicode content", remote_read(f"{out}/文件 名.txt") == b"uni")

    print("\n[remote: 7z]")
    if "7z" in formats:
        resp = remote_compress(
            paths=[
                f"{REMOTE_ROOT}/proj/a.txt",
                f"{REMOTE_ROOT}/proj/sub dir",
                f"{REMOTE_ROOT}/proj/文件 名.txt",
            ],
            name="seven",
            format="7z",
        )
        check("sftp: compress 7z", is_ok(resp), error_of(resp))
        out = f"{REMOTE_ROOT}/out-7z"
        check("sftp: extract 7z", data_of(remote_extract(f"{REMOTE_ROOT}/proj/seven.7z", out)) is not None)
        check("sftp: 7z content", remote_read(f"{out}/sub dir/b.txt") == b"two")
        check("sftp: 7z unicode", remote_read(f"{out}/文件 名.txt") == b"uni")
    else:
        check("sftp: 7z offered", False, "7z not advertised by formats")

    print("\n[remote: bzip2/xz/tar]")
    for fmt, name in (("tar.bz2", "bz"), ("tar.xz", "xz"), ("tar", "plain")):
        resp = remote_compress(name=name, format=fmt)
        check(f"sftp: compress {fmt}", is_ok(resp), error_of(resp))
        archive = f"{REMOTE_ROOT}/proj/{name}.{fmt}"
        out = f"{REMOTE_ROOT}/out-{name}"
        extracted = data_of(remote_extract(archive, out))
        check(f"sftp: extract {fmt}", extracted is not None, error_of(extracted))
        check(f"sftp: {fmt} content", remote_read(f"{out}/a.txt") == b"one")

    print("\n[remote: single file]")
    resp = remote_compress(
        paths=[f"{REMOTE_ROOT}/proj/keep.txt"],
        name="single",
        format="tar.gz",
        targetDir=f"{REMOTE_ROOT}/proj",
    )
    check("sftp: compress single file", is_ok(resp), error_of(resp))
    inspect = data_of(remote_inspect(f"{REMOTE_ROOT}/proj/single.tar.gz"))
    check("sftp: single file member count", (inspect or {}).get("members") == 1, str(inspect))
    out = f"{REMOTE_ROOT}/out-single"
    check("sftp: extract single file", data_of(remote_extract(f"{REMOTE_ROOT}/proj/single.tar.gz", out)) is not None)
    check("sftp: single file content", remote_read(f"{out}/keep.txt") == b"keep-new")


def test_remote_validation() -> None:
    print("\n[remote: validation]")
    existing = remote_compress(name="pack", format="zip", overwrite=False)
    check(
        "sftp: refuses existing archive",
        is_http_error(existing) and "已存在" in error_of(existing),
        error_of(existing),
    )
    overwrite = remote_compress(name="pack", format="zip", overwrite=True)
    check("sftp: overwrite accepted", is_ok(overwrite), error_of(overwrite))

    mixed = remote_compress(
        paths=[f"{REMOTE_ROOT}/proj/a.txt", f"{REMOTE_ROOT}/other/c.txt"],
        name="mixed",
        format="tar.gz",
    )
    check("sftp: refuses mixed directories", is_http_error(mixed), error_of(mixed))

    slash = remote_compress(name="bad/name", format="tar.gz")
    check("sftp: refuses path in archive name", is_http_error(slash), error_of(slash))

    missing_dir = remote_compress(name="lost", format="tar.gz", targetDir=f"{REMOTE_ROOT}/no-such")
    check("sftp: refuses missing target dir", is_http_error(missing_dir), error_of(missing_dir))

    bad_format = remote_compress(name="bad", format="rar")
    check("sftp: refuses unknown format", is_http_error(bad_format), error_of(bad_format))

    root = remote_compress(paths=["/"], name="root", format="tar.gz")
    check("sftp: refuses root selection", is_http_error(root), error_of(root))

    self_target = remote_compress(
        paths=[f"{REMOTE_ROOT}/proj/pack.zip"],
        name="pack",
        format="zip",
        targetDir=f"{REMOTE_ROOT}/proj",
        overwrite=True,
    )
    check("sftp: refuses archiving the archive itself", is_http_error(self_target), error_of(self_target))

    inside = remote_compress(
        paths=[f"{REMOTE_ROOT}/proj/sub dir"],
        name="inside",
        format="tar.gz",
        targetDir=f"{REMOTE_ROOT}/proj/sub dir",
        overwrite=True,
    )
    check("sftp: refuses archive inside source dir", is_http_error(inside), error_of(inside))

    unknown = remote_inspect(f"{REMOTE_ROOT}/proj/a.txt")
    check("sftp: inspect rejects non-archive", is_http_error(unknown), error_of(unknown))

    absent = remote_extract(f"{REMOTE_ROOT}/proj/no-such.tar.gz", f"{REMOTE_ROOT}/x")
    check("sftp: extract refuses missing archive", is_http_error(absent), error_of(absent))

    no_create = remote_extract(f"{REMOTE_ROOT}/proj/bundle.tar.gz", f"{REMOTE_ROOT}/no-such-dest", create=False)
    check("sftp: extract refuses missing dest without create", is_http_error(no_create), error_of(no_create))

    closed = req("/api/sftp/archive/compress", {"sessionId": "s_missing", "paths": ["/x"], "format": "tar.gz"})
    check("sftp: unknown session rejected", is_http_error(closed), error_of(closed))


def test_remote_unsafe() -> None:
    print("\n[remote: archive slip]")
    payloads = {}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    payloads["evil.tar.gz"] = buffer.getvalue()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape2.txt", "x")
        archive.writestr("/abs3.txt", "x")
    payloads["evil.zip"] = buffer.getvalue()

    for name, payload in payloads.items():
        target = f"{REMOTE_ROOT}/{name}"
        up = upload("/api/scp/upload", payload, {"assetId": SSH_ASSET, "path": target})
        check(f"sftp: upload {name}", is_ok(up), error_of(up))
        inspect = data_of(remote_inspect(target))
        check(f"sftp: {name} flagged unsafe", (inspect or {}).get("unsafe") is True, str(inspect))
        check(f"sftp: {name} unsafe sample", bool((inspect or {}).get("unsafeSample")), str(inspect))
        extracted = remote_extract(target, f"{REMOTE_ROOT}/evil")
        check(f"sftp: {name} extraction refused", is_http_error(extracted), error_of(extracted))
        check(f"sftp: {name} created no dest dir", remote_exists(f"{REMOTE_ROOT}/evil") is False)
        check(f"sftp: {name} wrote nothing outside", remote_exists(f"{REMOTE_ROOT}/escape.txt") is False)


def test_remote_skip_existing() -> None:
    print("\n[remote: overwrite=false]")
    out = f"{REMOTE_ROOT}/out-skip"
    sh(f"mkdir -p {q(out)}")
    for fmt, name in (("tar.gz", "skip-tar"), ("zip", "skip-zip"), ("7z", "skip-7z")):
        resp = remote_compress(
            paths=[f"{REMOTE_ROOT}/proj/keep.txt"],
            name=name,
            format=fmt,
            targetDir=f"{REMOTE_ROOT}/proj",
            overwrite=True,
        )
        if not is_ok(resp):
            check(f"sftp: prepare {fmt} skip fixture", False, error_of(resp))
            continue
        sh(f"printf 'old' > {q(out + '/keep.txt')}")
        archive = f"{REMOTE_ROOT}/proj/{name}.{fmt}"
        skipped = data_of(remote_extract(archive, out, overwrite=False))
        check(f"sftp: {fmt} skip existing", skipped is not None, error_of(skipped))
        check(f"sftp: {fmt} kept old content", remote_read(f"{out}/keep.txt") == b"old")
        forced = data_of(remote_extract(archive, out, overwrite=True))
        check(f"sftp: {fmt} overwrite existing", forced is not None, error_of(forced))
        check(f"sftp: {fmt} replaced content", remote_read(f"{out}/keep.txt") == b"keep-new")


def test_remote_asset_target() -> None:
    print("\n[remote: asset-only target]")
    resp = remote_compress(session=False, name="assetonly", format="tar.gz", overwrite=True)
    check("sftp: compress via assetId", is_ok(resp), error_of(resp))
    inspect = data_of(remote_inspect(f"{REMOTE_ROOT}/proj/assetonly.tar.gz", session=False))
    check("sftp: inspect via assetId", (inspect or {}).get("members", 0) >= 3, str(inspect))
    out = f"{REMOTE_ROOT}/out-asset"
    extracted = data_of(remote_extract(f"{REMOTE_ROOT}/proj/assetonly.tar.gz", out, session=False))
    check("sftp: extract via assetId", extracted is not None, error_of(extracted))
    check("sftp: assetId content", remote_read(f"{out}/a.txt") == b"one")


# --------------------------------------------------------------- local tests
def local_compress(paths, target_dir, name, fmt, overwrite=False):
    return req(
        "/api/fs/archive/compress",
        {
            "paths": paths,
            "targetDir": target_dir,
            "name": name,
            "format": fmt,
            "overwrite": overwrite,
        },
    )


def local_extract(archive, target, create=True, overwrite=True):
    return req(
        "/api/fs/archive/extract",
        {"path": archive, "targetDir": target, "create": create, "overwrite": overwrite},
    )


def local_inspect(archive):
    return req("/api/fs/archive/inspect", {"path": archive})


def test_local() -> None:
    print("\n[local: roundtrips]")
    proj = os.path.join(LOCAL_ROOT, "proj")
    formats_data = data_of(req("/api/fs/archive/formats", {}))
    formats = {item["id"] for item in (formats_data or {}).get("formats", []) if item.get("available")}
    check("local: formats endpoint", bool(formats_data) and "tar.gz" in formats, str(formats_data))
    for fmt, name in (("tar.gz", "lt"), ("zip", "lz"), ("tar.bz2", "lbz"), ("tar.xz", "lxz"), ("7z", "l7")):
        if fmt not in formats:
            check(f"local: {fmt} available", False, "not advertised")
            continue
        resp = local_compress(
            [os.path.join(proj, "a.txt"), os.path.join(proj, "sub dir"), os.path.join(proj, "文件 名.txt")],
            proj,
            name,
            fmt,
        )
        check(f"local: compress {fmt}", is_ok(resp), error_of(resp))
        if not is_ok(resp):
            continue
        archive = data_of(resp)["path"]
        inspect = data_of(local_inspect(archive))
        check(f"local: inspect {fmt}", (inspect or {}).get("members", 0) >= 3, str(inspect))
        out = os.path.join(LOCAL_ROOT, f"out-{name}")
        extracted = data_of(local_extract(archive, out))
        check(f"local: extract {fmt}", extracted is not None, error_of(extracted))
        check(f"local: {fmt} content", local_read(os.path.join(out, "a.txt")) == "one")
        check(f"local: {fmt} unicode", local_read(os.path.join(out, "文件 名.txt")) == "uni")

    print("\n[local: gzip single file]")
    gz_path = os.path.join(proj, "raw.log.gz")
    with gzip.open(gz_path, "wb") as fh:
        fh.write(b"raw bytes\n")
    inspect = data_of(local_inspect(gz_path))
    check("local: inspect gz", (inspect or {}).get("format") == "gz" and (inspect or {}).get("members") == 1, str(inspect))
    out = os.path.join(LOCAL_ROOT, "out-gz")
    check("local: extract gz", data_of(local_extract(gz_path, out)) is not None)
    check("local: gz content", local_read(os.path.join(out, "raw.log")).strip() == "raw bytes")

    print("\n[local: validation]")
    existing = local_compress([os.path.join(proj, "a.txt")], proj, "lt", "tar.gz", overwrite=False)
    check("local: refuses existing archive", is_http_error(existing), error_of(existing))
    mixed = local_compress(
        [os.path.join(proj, "a.txt"), os.path.join(LOCAL_ROOT, "other", "c.txt")],
        proj,
        "mixed",
        "tar.gz",
    )
    check("local: refuses mixed directories", is_http_error(mixed), error_of(mixed))
    check(
        "local: refuses missing target dir",
        is_http_error(local_compress([os.path.join(proj, "a.txt")], os.path.join(LOCAL_ROOT, "no-such"), "x", "zip")),
    )
    check(
        "local: refuses archive inside source dir",
        is_http_error(
            local_compress(
                [os.path.join(proj, "sub dir")],
                os.path.join(proj, "sub dir"),
                "inside",
                "tar.gz",
            )
        ),
    )
    check(
        "local: refuses missing dest without create",
        is_http_error(local_extract(os.path.join(proj, "lt.tar.gz"), os.path.join(LOCAL_ROOT, "no-such"), create=False)),
    )
    check(
        "local: inspect rejects non-archive",
        is_http_error(local_inspect(os.path.join(proj, "a.txt"))),
    )

    print("\n[local: overwrite=false]")
    out_skip = os.path.join(LOCAL_ROOT, "out-skip-local")
    os.makedirs(out_skip, exist_ok=True)
    for fmt, name in (("tar.gz", "lskip-tar"), ("zip", "lskip-zip"), ("7z", "lskip-7z")):
        if fmt not in formats:
            check(f"local: {fmt} available", False, "not advertised")
            continue
        resp = local_compress([os.path.join(proj, "keep.txt")], proj, name, fmt, overwrite=True)
        if not is_ok(resp):
            check(f"local: prepare {fmt} skip fixture", False, error_of(resp))
            continue
        archive = data_of(resp)["path"]
        with open(os.path.join(out_skip, "keep.txt"), "w", encoding="utf-8") as fh:
            fh.write("old")
        check(
            f"local: {fmt} skip existing",
            data_of(local_extract(archive, out_skip, overwrite=False)) is not None,
        )
        check(f"local: {fmt} kept old content", local_read(os.path.join(out_skip, "keep.txt")) == "old")
        check(
            f"local: {fmt} overwrite existing",
            data_of(local_extract(archive, out_skip, overwrite=True)) is not None,
        )
        check(f"local: {fmt} replaced content", local_read(os.path.join(out_skip, "keep.txt")) == "keep-new")

    print("\n[local: archive slip]")
    evil_tar = os.path.join(LOCAL_ROOT, "evil.tar.gz")
    with tarfile.open(evil_tar, "w:gz") as archive:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    evil_zip = os.path.join(LOCAL_ROOT, "evil.zip")
    with zipfile.ZipFile(evil_zip, "w") as archive:
        archive.writestr("../escape2.txt", "x")
        archive.writestr("/abs3.txt", "x")
    for archive in (evil_tar, evil_zip):
        inspect = data_of(local_inspect(archive))
        check(f"local: {os.path.basename(archive)} flagged unsafe", (inspect or {}).get("unsafe") is True, str(inspect))
        out = os.path.join(LOCAL_ROOT, "evil-out")
        check(
            f"local: {os.path.basename(archive)} extraction refused",
            is_http_error(local_extract(archive, out)),
        )
        check(f"local: {os.path.basename(archive)} wrote nothing outside", not os.path.exists(os.path.join(LOCAL_ROOT, "escape.txt")))
        check(f"local: {os.path.basename(archive)} no dest dir", not os.path.isdir(out))


def test_history() -> None:
    print("\n[history]")
    resp = req("/api/history?limit=30", method="GET")
    data = data_of(resp)
    kinds = [item.get("kind") for item in (data or {}).get("history", [])]
    check("history: archive entries recorded", "archive" in kinds, str(kinds[:8]))


# ---------------------------------------------------------------------- main
def main() -> int:
    global BASE, TOKEN, SSH_ASSET, SESSION_ID, REMOTE_ROOT, LOCAL_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    BASE = args.base.rstrip("/")
    TOKEN = args.token or load_token()
    suffix = "%04x" % random.randint(0, 0xFFFF)
    REMOTE_ROOT = f"/tmp/h1khub-archive-{suffix}"
    LOCAL_ROOT = tempfile.mkdtemp(prefix=f"h1khub-archive-local-{suffix}-")

    assets = find_assets()
    asset = assets.get(SSH_ASSET_NAME)
    if not asset:
        print(f"missing SSH asset: {SSH_ASSET_NAME}")
        return 1
    SSH_ASSET = asset["id"]

    make_remote_fixture()
    make_local_fixture()
    try:
        SESSION_ID = start_session()
        formats = test_remote_formats()
        test_remote_roundtrips(formats)
        test_remote_validation()
        test_remote_unsafe()
        test_remote_skip_existing()
        test_remote_asset_target()
        test_local()
        test_history()
    finally:
        if SESSION_ID:
            try:
                req(f"/api/sessions/{SESSION_ID}/close")
            except Exception:
                pass
        try:
            sh(f"rm -rf {q(REMOTE_ROOT)}")
        except Exception:
            pass
        shutil.rmtree(LOCAL_ROOT, ignore_errors=True)

    print(f"\nPASS={PASS} FAIL={FAIL}")
    if FAILURES:
        print("FAILED:", ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
