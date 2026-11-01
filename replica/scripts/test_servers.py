#!/usr/bin/env python3
"""Local test fixtures: a minimal FTP server and a Telnet server.

These exist only so the replica's FTP/Telnet clients can be verified end to end
without external services or network access. They are NOT part of the product.

Usage:
  python3 scripts/test_servers.py ftp    --port 2121 --root /tmp/h1khub-ftp
  python3 scripts/test_servers.py telnet --port 2323
"""
from __future__ import annotations

import argparse
import os
import posixpath
import socket
import socketserver
import stat
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Minimal FTP server (passive mode, MLSD + LIST)
# ---------------------------------------------------------------------------
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
TELOPT_ECHO, TELOPT_SGA = 1, 3


class FTPHandler(socketserver.StreamRequestHandler):
    root = "/tmp/h1khub-ftp"
    username = "hexftp"
    password = "hex123"
    pasv_listener: socket.socket | None = None
    cwd = "/"
    authed = False
    rename_from = None
    binary = False

    def reply(self, text: str) -> None:
        self.wfile.write((text + "\r\n").encode("utf-8"))
        self.wfile.flush()

    def abs_path(self, raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return self.cwd
        if not text.startswith("/"):
            text = posixpath.join(self.cwd, text)
        norm = posixpath.normpath(text)
        resolved = os.path.realpath(os.path.join(self.root, norm.lstrip("/")))
        if not (resolved == self.root or resolved.startswith(self.root + os.sep)):
            raise PermissionError("outside root")
        return "/" + os.path.relpath(resolved, self.root).replace(os.sep, "/") if resolved != self.root else "/"

    def fs_path(self, virtual: str) -> str:
        return os.path.join(self.root, virtual.lstrip("/"))

    def open_data(self) -> socket.socket:
        if not self.pasv_listener:
            raise RuntimeError("use PASV first")
        conn, _ = self.pasv_listener.accept()
        self.pasv_listener.close()
        self.pasv_listener = None
        conn.settimeout(15)
        return conn

    def list_lines(self, virtual: str, mlsd: bool) -> list[str]:
        target = self.fs_path(virtual)
        if os.path.isfile(target):
            names = [os.path.basename(target)]
            base = posixpath.dirname(virtual) or "/"
        else:
            base = virtual
            names = sorted(os.listdir(target))
        out = []
        for name in names:
            full = os.path.join(target, name) if os.path.isdir(target) else target
            st = os.stat(full)
            mode = stat.filemode(st.st_mode)
            if mlsd:
                typ = "dir" if stat.S_ISDIR(st.st_mode) else "file"
                modify = time.strftime("%Y%m%d%H%M%S", time.localtime(st.st_mtime))
                perm = "el" if typ == "dir" else "r"
                out.append(f"type={typ};size={st.st_size};modify={modify};perm={perm}; {name}")
            else:
                mtime = time.strftime("%b %d %H:%M", time.localtime(st.st_mtime))
                out.append(f"{mode} 1 ftp ftp {st.st_size:>12} {mtime} {name}")
        return out

    def handle(self) -> None:
        self.reply("220 H1KHub test FTP ready")
        while True:
            try:
                raw = self.rfile.readline()
            except (ConnectionResetError, socket.timeout):
                return
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                continue
            cmd, _, arg = line.partition(" ")
            cmd = cmd.upper()
            try:
                if cmd == "USER":
                    self.reply("331 password required")
                elif cmd == "PASS":
                    if arg == self.password:
                        self.authed = True
                        self.reply("230 logged in")
                    else:
                        self.reply("530 login incorrect")
                elif cmd == "SYST":
                    self.reply("215 UNIX Type: L8")
                elif cmd == "FEAT":
                    self.reply("211-Features:\r\n MLSD\r\n MLST type*;size*;modify*;perm*;\r\n SIZE\r\n UTF8\r\n211 End")
                elif cmd == "OPTS":
                    self.reply("200 ok")
                elif cmd == "PWD":
                    self.reply(f'257 "{self.cwd}"')
                elif cmd == "CWD":
                    try:
                        target = self.abs_path(arg)
                    except PermissionError:
                        self.reply("550 permission denied")
                        continue
                    if os.path.isdir(self.fs_path(target)):
                        self.cwd = target
                        self.reply("250 ok")
                    else:
                        self.reply("550 no such directory")
                elif cmd == "CDUP":
                    self.cwd = posixpath.dirname(self.cwd.rstrip("/")) or "/"
                    self.reply("250 ok")
                elif cmd == "TYPE":
                    self.binary = arg.upper().startswith("I")
                    self.reply("200 type set")
                elif cmd in ("MODE", "STRU"):
                    self.reply("200 ok")
                elif cmd == "PASV":
                    self.pasv_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.pasv_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    self.pasv_listener.bind(("127.0.0.1", 0))
                    self.pasv_listener.listen(1)
                    port = self.pasv_listener.getsockname()[1]
                    self.reply(f"227 Entering Passive Mode (127,0,0,1,{port >> 8},{port & 255})")
                elif cmd == "EPSV":
                    self.pasv_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.pasv_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    self.pasv_listener.bind(("127.0.0.1", 0))
                    self.pasv_listener.listen(1)
                    port = self.pasv_listener.getsockname()[1]
                    self.reply(f"229 Entering Extended Passive Mode (|||{port}|)")
                elif cmd == "LIST":
                    virtual = self.cwd if not arg or arg.startswith("-") else self.abs_path(arg)
                    lines = self.list_lines(virtual, mlsd=False)
                    self.reply("150 opening data connection")
                    conn = self.open_data()
                    with conn:
                        conn.sendall(("\r\n".join(lines) + ("\r\n" if lines else "")).encode())
                    self.reply("226 transfer complete")
                elif cmd == "NLST":
                    virtual = self.cwd if not arg else self.abs_path(arg)
                    target = self.fs_path(virtual)
                    names = [os.path.basename(target)] if os.path.isfile(target) else sorted(os.listdir(target))
                    self.reply("150 opening data connection")
                    conn = self.open_data()
                    with conn:
                        conn.sendall(("\r\n".join(names) + ("\r\n" if names else "")).encode())
                    self.reply("226 transfer complete")
                elif cmd == "MLSD":
                    virtual = self.cwd if not arg else self.abs_path(arg)
                    lines = self.list_lines(virtual, mlsd=True)
                    self.reply("150 opening data connection")
                    conn = self.open_data()
                    with conn:
                        conn.sendall(("\r\n".join(lines) + ("\r\n" if lines else "")).encode())
                    self.reply("226 transfer complete")
                elif cmd == "SIZE":
                    target = self.fs_path(self.abs_path(arg))
                    if os.path.isfile(target):
                        self.reply(f"213 {os.path.getsize(target)}")
                    else:
                        self.reply("550 not a file")
                elif cmd == "MDTM":
                    target = self.fs_path(self.abs_path(arg))
                    if os.path.exists(target):
                        self.reply("213 " + time.strftime("%Y%m%d%H%M%S", time.localtime(os.path.getmtime(target))))
                    else:
                        self.reply("550 no such file")
                elif cmd == "RETR":
                    target = self.fs_path(self.abs_path(arg))
                    if not os.path.isfile(target):
                        self.reply("550 no such file")
                        continue
                    self.reply("150 opening data connection")
                    conn = self.open_data()
                    with conn, open(target, "rb") as fh:
                        conn.sendall(fh.read())
                    self.reply("226 transfer complete")
                elif cmd == "STOR":
                    target = self.fs_path(self.abs_path(arg))
                    os.makedirs(os.path.dirname(target) or self.root, exist_ok=True)
                    self.reply("150 ok to send data")
                    conn = self.open_data()
                    with conn, open(target, "wb") as fh:
                        while True:
                            chunk = conn.recv(65536)
                            if not chunk:
                                break
                            fh.write(chunk)
                    self.reply("226 transfer complete")
                elif cmd == "DELE":
                    target = self.fs_path(self.abs_path(arg))
                    if os.path.isfile(target):
                        os.remove(target)
                        self.reply("250 deleted")
                    else:
                        self.reply("550 no such file")
                elif cmd == "MKD":
                    target = self.fs_path(self.abs_path(arg))
                    try:
                        os.makedirs(target, exist_ok=False)
                        self.reply(f'257 "{self.abs_path(arg)}" created')
                    except FileExistsError:
                        self.reply("550 already exists")
                elif cmd == "RMD":
                    target = self.fs_path(self.abs_path(arg))
                    try:
                        os.rmdir(target)
                        self.reply("250 removed")
                    except OSError:
                        self.reply("550 cannot remove")
                elif cmd == "RNFR":
                    self.rename_from = self.abs_path(arg)
                    self.reply("350 ready")
                elif cmd == "RNTO":
                    if not self.rename_from:
                        self.reply("503 RNFR required")
                    else:
                        try:
                            os.replace(self.fs_path(self.rename_from), self.fs_path(self.abs_path(arg)))
                            self.reply("250 renamed")
                        except OSError:
                            self.reply("550 rename failed")
                        self.rename_from = None
                elif cmd == "NOOP":
                    self.reply("200 ok")
                elif cmd == "QUIT":
                    self.reply("221 bye")
                    return
                else:
                    self.reply("502 command not implemented")
            except Exception as exc:  # pragma: no cover - test fixture
                try:
                    self.reply(f"550 {exc}")
                except Exception:
                    return


class FTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_ftp(port: int, root: str, user: str, password: str) -> None:
    os.makedirs(root, exist_ok=True)
    FTPHandler.root = os.path.realpath(root)
    FTPHandler.username = user
    FTPHandler.password = password
    server = FTPServer(("127.0.0.1", port), FTPHandler)
    print(f"ftp listening on 127.0.0.1:{port} root={FTPHandler.root}", flush=True)
    server.serve_forever()


# ---------------------------------------------------------------------------
# Minimal Telnet server (IAC negotiation + tiny line shell)
# ---------------------------------------------------------------------------
def handle_telnet(conn: socket.socket) -> None:
    conn.settimeout(120)
    conn.sendall(bytes([IAC, WILL, TELOPT_ECHO, IAC, WILL, TELOPT_SGA, IAC, DO, TELOPT_SGA]))
    conn.sendall(b"H1KHub test telnet\r\nlogin: ")

    buf = b""
    state = "login"
    user = ""
    while True:
        try:
            data = conn.recv(4096)
        except (socket.timeout, ConnectionResetError):
            return
        if not data:
            return
        out = bytearray()
        i = 0
        while i < len(data):
            byte = data[i]
            if byte == IAC and i + 1 < len(data):
                cmd = data[i + 1]
                if cmd == IAC:
                    buf += bytes([IAC])
                    i += 2
                    continue
                if cmd in (WILL, WONT, DO, DONT):
                    i += 3
                    continue
                if cmd == SB:
                    j = data.find(bytes([IAC, SE]), i)
                    i = len(data) if j < 0 else j + 2
                    continue
                i += 2
                continue
            if byte in (0, 4):
                conn.sendall(b"\r\nbye\r\n")
                return
            if byte in (10, 13):
                if buf and not buf.endswith(b"\n"):
                    buf += b"\n"
                i += 1
                continue
            if byte == 8 or byte == 127:
                if buf:
                    buf = buf[:-1]
                    out += b"\b \b"
                i += 1
                continue
            buf += bytes([byte])
            out += bytes([byte])
            i += 1
        if out:
            conn.sendall(bytes(out))
        # process completed lines
        while b"\n" in buf or b"\r" in buf:
            line, _sep, rest = buf.partition(b"\n" if b"\n" in buf else b"\r")
            buf = rest
            text = line.decode("utf-8", "replace").strip()
            if not text:
                conn.sendall(b"\r\nlogin: " if state == "login" else b"\r\ntelnet> ")
                continue
            if state == "login":
                user = text
                state = "shell"
                conn.sendall(f"\r\nwelcome {user}\r\ntelnet> ".encode())
                continue
            if text in ("exit", "quit", "logout"):
                conn.sendall(b"\r\nbye\r\n")
                return
            if text == "help":
                reply = "commands: help echo whoami uname date exit"
            elif text.startswith("echo "):
                reply = text[5:]
            elif text == "whoami":
                reply = user
            elif text == "uname":
                reply = "Linux h1khub-telnet-test"
            elif text == "date":
                reply = time.strftime("%Y-%m-%d %H:%M:%S")
            else:
                reply = f"unknown: {text}"
            conn.sendall(b"\r\n" + reply.encode() + b"\r\ntelnet> ")


def run_telnet(port: int) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(16)
    print(f"telnet listening on 127.0.0.1:{port}", flush=True)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle_telnet, args=(conn,), daemon=True).start()


# ---------------------------------------------------------------------------
# Minimal HTTP CONNECT / SOCKS5 test proxies (forward to 127.0.0.1 only)
# ---------------------------------------------------------------------------
def _pipe(a: socket.socket, b: socket.socket) -> None:
    import select

    try:
        while True:
            readable, _, _ = select.select([a, b], [], [], 1.0)
            if a in readable:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
            if b in readable:
                data = b.recv(65536)
                if not data:
                    break
                a.sendall(data)
    except Exception:
        pass
    finally:
        for sock in (a, b):
            try:
                sock.close()
            except Exception:
                pass


def _connect_target(host: str, port: int) -> socket.socket:
    if host not in ("127.0.0.1", "localhost") and not host.startswith("127."):
        raise PermissionError("test proxy only forwards to loopback")
    return socket.create_connection((host, port), timeout=10)


def handle_http_proxy(conn: socket.socket) -> None:
    conn.settimeout(15)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            conn.close()
            return
        buf += chunk
    line = buf.split(b"\r\n", 1)[0].decode("latin1")
    parts = line.split()
    if len(parts) < 2 or parts[0].upper() != "CONNECT" or ":" not in parts[1]:
        conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
        conn.close()
        return
    host, _, port = parts[1].rpartition(":")
    try:
        upstream = _connect_target(host, int(port))
    except Exception as exc:
        conn.sendall(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{exc}".encode())
        conn.close()
        return
    conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    _pipe(conn, upstream)


def handle_socks5(conn: socket.socket) -> None:
    conn.settimeout(15)
    try:
        head = conn.recv(2)
        if not head or head[0] != 5:
            conn.close()
            return
        conn.recv(head[1])
        conn.sendall(b"\x05\x00")
        req = conn.recv(4)
        if len(req) < 4 or req[1] != 1:
            conn.close()
            return
        atyp = req[3]
        if atyp == 1:
            host = socket.inet_ntoa(conn.recv(4))
        elif atyp == 3:
            length = conn.recv(1)[0]
            host = conn.recv(length).decode()
        else:
            conn.close()
            return
        port = int.from_bytes(conn.recv(2), "big")
        try:
            upstream = _connect_target(host, port)
        except Exception:
            conn.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
            conn.close()
            return
        conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        _pipe(conn, upstream)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def run_proxy(port: int, kind: str) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(16)
    print(f"{kind} proxy listening on 127.0.0.1:{port}", flush=True)
    handler = handle_http_proxy if kind == "http" else handle_socks5
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handler, args=(conn,), daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["ftp", "telnet", "http-proxy", "socks5-proxy"])
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--root", default="/tmp/h1khub-ftp")
    parser.add_argument("--user", default="hexftp")
    parser.add_argument("--password", default="hex123")
    args = parser.parse_args()
    if args.kind == "ftp":
        run_ftp(args.port or 2121, args.root, args.user, args.password)
    elif args.kind in ("http-proxy", "socks5-proxy"):
        run_proxy(args.port or (8080 if args.kind == "http-proxy" else 1080), "http" if args.kind == "http-proxy" else "socks5")
    else:
        run_telnet(args.port or 2323)
    return 0


if __name__ == "__main__":
    sys.exit(main())
