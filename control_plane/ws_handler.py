"""WebSocket handler — RFC 6455 §5 upgrade + frame encode/decode.

Minimal implementation: text frames only (opcode 0x1), close/ping/pong control
frames. No extensions, no compression. Zero external dependencies.

Used by server.py to upgrade GET /ws/terminal to a bidirectional WebSocket that
proxies between xterm.js (browser) and the PTY (claude process).
"""

from __future__ import annotations

import base64
import hashlib
import struct
from typing import Optional, Tuple

# RFC 6455 §4.2.2 — magic GUID for the accept key.
_WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Frame opcodes
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

# Max frame payload we accept from the client (1 MiB).
_MAX_PAYLOAD = 1_048_576


def compute_accept_key(key: str) -> str:
    """RFC 6455 §4.2.2 step 4: base64(sha1(key + magic))."""
    sha = hashlib.sha1(key.encode() + _WS_GUID).digest()
    return base64.b64encode(sha).decode()


def read_frame(data: bytes) -> Tuple[Optional[int], Optional[bytes]]:
    """Read a single WebSocket frame from `data`. Returns (opcode, payload).

    Returns (opcode, None) for close frames (no payload needed).
    Returns (None, None) if more data is needed (incomplete frame).
    """
    if len(data) < 2:
        return None, None

    byte0 = data[0]
    byte1 = data[1]
    opcode = byte0 & 0x0F
    masked = (byte1 & 0x80) != 0
    length = byte1 & 0x7F

    offset = 2
    if length == 126:
        if len(data) < 4:
            return None, None
        length = struct.unpack("!H", data[2:4])[0]
        offset = 4
    elif length == 127:
        if len(data) < 10:
            return None, None
        length = struct.unpack("!Q", data[2:10])[0]
        offset = 10

    if masked:
        if len(data) < offset + 4:
            return None, None
        mask_key = data[offset : offset + 4]
        offset += 4

    if length > _MAX_PAYLOAD:
        # Too large — close the connection.
        return _OP_CLOSE, None

    if len(data) < offset + length:
        return None, None

    payload = data[offset : offset + length]

    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

    if opcode == _OP_CLOSE:
        return _OP_CLOSE, None

    if opcode == _OP_PING:
        return _OP_PING, payload

    if opcode == _OP_PONG:
        return _OP_PONG, payload

    if opcode == _OP_TEXT:
        return _OP_TEXT, payload

    # Unknown opcode — ignore
    return None, None


def encode_frame(opcode: int, payload: bytes = b"") -> bytes:
    """Encode a WebSocket frame. Server → client frames are never masked."""
    header = bytes([0x80 | opcode])  # FIN + opcode
    plen = len(payload)
    if plen < 126:
        header += bytes([plen])
    elif plen < 65536:
        header += bytes([126]) + struct.pack("!H", plen)
    else:
        header += bytes([127]) + struct.pack("!Q", plen)
    return header + payload


def encode_text(payload: str) -> bytes:
    """Encode a text frame (UTF-8)."""
    return encode_frame(_OP_TEXT, payload.encode("utf-8"))


def encode_close(code: int = 1000) -> bytes:
    """Encode a close frame with a status code."""
    return encode_frame(_OP_CLOSE, struct.pack("!H", code))


def encode_pong(payload: bytes = b"") -> bytes:
    return encode_frame(_OP_PONG, payload)
