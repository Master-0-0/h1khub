"""SSH key generation and ssh-copy-id helper."""
from __future__ import annotations

import shlex

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa


class KeyError_(Exception):
    pass


def _serialize_private(key, passphrase: str | None) -> str:
    encryption = (
        serialization.BestAvailableEncryption(passphrase.encode()) if passphrase else serialization.NoEncryption()
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=encryption,
    ).decode()


def _public_line(public_key, comment: str) -> str:
    text = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode()
    return f"{text} {comment}".strip() if comment else text


def generate_keypair(
    key_type: str = "ed25519",
    bits: int = 3072,
    comment: str = "",
    passphrase: str | None = None,
) -> dict:
    kind = (key_type or "ed25519").lower()
    if kind == "rsa":
        size = max(2048, min(int(bits or 3072), 8192))
        key = rsa.generate_private_key(public_exponent=65537, key_size=size)
    elif kind == "ed25519":
        key = ed25519.Ed25519PrivateKey.generate()
    else:
        raise KeyError_(f"不支持的密钥类型：{key_type}")
    private_text = _serialize_private(key, passphrase)
    public_text = _public_line(key.public_key(), comment)
    return {
        "type": kind,
        "privateKey": private_text,
        "publicKey": public_text,
        "fingerprint": _fingerprint(key.public_key()),
    }


def public_from_private(private_text: str, passphrase: str | None = None) -> str:
    import paramiko

    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            key = cls.from_private_key(__import__("io").StringIO(private_text), password=passphrase or None)
            return key.get_name() + " " + key.get_base64()
        except Exception:
            continue
    raise KeyError_("无法解析私钥")


def _fingerprint(public_key) -> str:
    import base64
    import hashlib

    raw = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    digest = hashlib.sha256(base64.b64decode(raw.split(b" ", 1)[1])).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def copy_public_key(transport, public_key: str, timeout: float = 30.0) -> dict:
    key = (public_key or "").strip()
    if not key or " " not in key:
        raise KeyError_("公钥格式无效")
    if len(key) > 8192:
        raise KeyError_("公钥过长")
    quoted = shlex.quote(key)
    script = (
        "umask 077; mkdir -p ~/.ssh && chmod 700 ~/.ssh; "
        "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys; "
        f"if grep -qxF {quoted} ~/.ssh/authorized_keys; then echo H1KHUB_EXISTS; "
        f"else printf '%s\\n' {quoted} >> ~/.ssh/authorized_keys; echo H1KHUB_ADDED; fi"
    )
    chan = transport.open_session()
    try:
        chan.settimeout(timeout)
        chan.exec_command(script)
        out = b""
        err = b""
        while True:
            if chan.recv_ready():
                out += chan.recv(65536)
            if chan.recv_stderr_ready():
                err += chan.recv_stderr(65536)
            if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                break
        code = chan.recv_exit_status()
    finally:
        try:
            chan.close()
        except Exception:
            pass
    text = out.decode("utf-8", "replace")
    error_text = err.decode("utf-8", "replace").strip()
    if code != 0:
        raise KeyError_(error_text or f"ssh-copy-id 失败（exit={code}）")
    return {
        "ok": True,
        "added": "H1KHUB_ADDED" in text,
        "existed": "H1KHUB_EXISTS" in text,
        "output": (text + ("\n" + error_text if error_text else "")).strip(),
    }
