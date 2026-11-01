"""Binary RPC framing and protobuf payloads, compatible with HexHub 5.1.9.

The original client (assets/rpc-*.js) speaks a XOR-obfuscated length-prefixed
Packet frame over WebSocket:

    uint16 methodLen | uint32 dataLen | uint32 id | method | data

every byte XOR'd with (index & 0xff). Channels are carried as
ChannelOpen / ChannelSend / ChannelClose packets, and specific replies use the
protobuf schemas served from /proto/*.proto (recovered from the original app).

This module implements the framing plus a small protobuf encoder for
TableDataResponse (docs/proto-recovered/table_data.proto).
"""
from __future__ import annotations

import json
import struct
from typing import Any

XOR = True


def _xor(buf: bytes) -> bytes:
    return bytes(b ^ (i & 0xFF) for i, b in enumerate(buf))


def encode_packet(method: str, msg_id: int, data: bytes = b"", xor: bool = XOR) -> bytes:
    name = method.encode("utf-8")
    frame = (
        struct.pack(">HII", len(name) & 0xFFFF, len(data) & 0xFFFFFFFF, msg_id & 0xFFFFFFFF)
        + name
        + data
    )
    return _xor(frame) if xor else frame


def decode_packet(buf: bytes, xor: bool = XOR) -> dict[str, Any]:
    raw = _xor(buf) if xor else bytes(buf)
    if len(raw) < 10:
        raise ValueError("packet too short")
    method_len, data_len, msg_id = struct.unpack(">HII", raw[:10])
    end = 10 + method_len
    if len(raw) < end:
        raise ValueError("truncated method")
    method = raw[10:end].decode("utf-8", "replace")
    data = raw[end : end + data_len]
    return {"method": method, "id": msg_id, "data": data}


# ----------------------------------------------------------------- protobuf
def _varint(value: int) -> bytes:
    n = int(value)
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        chunk = n & 0x7F
        n >>= 7
        if n:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _len_field(field: int, payload: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _varint_field(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _string_field(field: int, value: str) -> bytes:
    return _len_field(field, str(value).encode("utf-8"))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def encode_table_data(
    columns: list[str],
    column_types: list[str],
    rows: list[list[Any]],
    offset: int = 0,
    total: int = 0,
    page_size: int = 0,
) -> bytes:
    """Encode proto_model.TableDataResponse from the recovered .proto."""
    cols = b""
    for idx, name in enumerate(columns):
        col_type = column_types[idx] if idx < len(column_types) else ""
        col = _string_field(1, name) + _string_field(2, col_type)
        cols += _len_field(1, col)

    row_payload = b""
    for row in rows:
        nulls = b"".join(_varint(1 if cell is None else 0) for cell in row)
        msg = _len_field(1, nulls)
        for cell in row:
            msg += _string_field(2, _cell(cell))
        row_payload += _len_field(2, msg)

    wrapper = cols + row_payload
    out = _len_field(1, wrapper)
    out += _varint_field(2, int(offset))
    out += _varint_field(3, int(total))
    pages = (int(total) + int(page_size) - 1) // int(page_size) if page_size else 0
    out += _varint_field(4, pages)
    return out
