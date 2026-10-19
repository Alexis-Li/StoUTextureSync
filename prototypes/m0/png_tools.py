"""Minimal dependency-free PNG writer for publication probes."""

from __future__ import annotations

import struct
import zlib


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def solid_png(width: int, height: int, rgba: tuple[int, int, int, int], noise_seed: int = 0) -> bytes:
    """An RGBA8 PNG. A non-zero seed adds a deterministic pattern so files differ in size."""
    row = bytes(rgba) * width
    rows = []
    for y in range(height):
        if noise_seed:
            value = (y * 31 + noise_seed * 17) & 0xFF
            line = bytearray(row)
            line[0:4] = bytes((value, rgba[1], rgba[2], 255))
            rows.append(b"\x00" + bytes(line))
        else:
            rows.append(b"\x00" + row)
    raw = b"".join(rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b""))
