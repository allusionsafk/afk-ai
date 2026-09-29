"""Generate the deterministic AFK A-mark icon using only the Python standard library.

The geometry follows Theme.PaintWordmark: an ink disc and a paper A with round
strokes. Run from the repository root. No installed Python package is needed.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
INK = (41, 45, 43)
PAPER = (248, 247, 243)
TARGET = Path(__file__).resolve().parents[1] / "src" / "AFKLocalAI.App" / "Assets" / "afk-ai.ico"


def distance_to_segment(x: float, y: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    position = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - a[0] - position * dx, y - a[1] - position * dy)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def render_png(size: int) -> bytes:
    sample = 4
    pixels = bytearray()
    left, top, extent = 0.0625, 0.0625, 0.875
    apex = (0.5, top + extent * 0.23)
    legs = ((left + extent * 0.28, top + extent * 0.78), apex), (
        apex, (left + extent * 0.72, top + extent * 0.78)
    )
    bar = ((left + extent * 0.38, top + extent * 0.60), (left + extent * 0.62, top + extent * 0.60))
    stroke = max(0.083 * extent, 1.48 / size)
    for row in range(size):
        pixels.append(0)  # PNG filter type: none
        for column in range(size):
            ink_coverage = 0
            paper_coverage = 0
            for sy in range(sample):
                for sx in range(sample):
                    x = (column + (sx + 0.5) / sample) / size
                    y = (row + (sy + 0.5) / sample) / size
                    inside = math.hypot(x - 0.5, y - 0.5) <= extent / 2
                    if inside:
                        ink_coverage += 1
                        if min(distance_to_segment(x, y, *segment) for segment in (*legs, bar)) <= stroke / 2:
                            paper_coverage += 1
            if ink_coverage == 0:
                pixels.extend((0, 0, 0, 0))
            else:
                mix = paper_coverage / ink_coverage
                pixels.extend(round(INK[channel] * (1 - mix) + PAPER[channel] * mix) for channel in range(3))
                pixels.append(round(255 * ink_coverage / (sample * sample)))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", zlib.compress(bytes(pixels), 9)) + png_chunk(b"IEND", b"")


def main() -> None:
    images = [(size, render_png(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + len(images) * 16
    entries = bytearray()
    payload = bytearray()
    for size, png in images:
        entries.extend(struct.pack("<BBBBHHII", 0 if size == 256 else size, 0 if size == 256 else size,
                                   0, 0, 1, 32, len(png), offset))
        payload.extend(png)
        offset += len(png)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(header + entries + payload)
    print(f"Wrote {TARGET} ({len(images)} sizes, {TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
