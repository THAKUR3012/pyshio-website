"""Draw the app icons (white heart on the Pyshio blue) as PNGs without any image library.

    python features/pwa/make_icons.py      # writes features/pwa/static/icon-192.png, icon-512.png, icon-maskable-512.png
"""
from pathlib import Path
import struct
import zlib

OUT = Path(__file__).resolve().parent / "static"
BLUE = (2, 132, 199)
WHITE = (255, 255, 255)
SAMPLES = 3  # anti-aliasing: 3x3 samples per pixel


def heart(x: float, y: float) -> bool:
    """Classic implicit heart, x/y roughly in [-1.3, 1.3], pointing down."""
    y = -y
    return (x * x + y * y - 1) ** 3 - x * x * y ** 3 <= 0


def rounded(x: float, y: float, size: float, radius: float) -> bool:
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def draw(size: int, maskable: bool) -> bytes:
    heart_scale = 0.26 if maskable else 0.33  # maskable icons keep the art inside the central safe zone
    radius = 0 if maskable else size * 0.22
    rows = []
    for py in range(size):
        row = bytearray([0])  # filter type 0
        for px in range(size):
            bg = fg = 0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    x = px + (sx + 0.5) / SAMPLES
                    y = py + (sy + 0.5) / SAMPLES
                    if maskable or rounded(x, y, size, radius):
                        bg += 1
                        hx = (x - size / 2) / (size * heart_scale)
                        hy = (y - size * 0.52) / (size * heart_scale)
                        fg += heart(hx * 1.15, hy * 1.15)
            n = SAMPLES * SAMPLES
            alpha = round(255 * bg / n)
            t = fg / bg if bg else 0
            rgb = [round(BLUE[i] * (1 - t) + WHITE[i] * t) for i in range(3)]
            row += bytes(rgb + [alpha])
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, size, maskable in (("icon-192.png", 192, False), ("icon-512.png", 512, False),
                                 ("icon-maskable-512.png", 512, True)):
        (OUT / name).write_bytes(draw(size, maskable))
        print("wrote", OUT / name)
