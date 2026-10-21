"""虚拟手柄命令 + 图像中线自动提取 + 像素探针。"""
from __future__ import annotations

from pathlib import Path

# 对齐逐飞助手虚拟手柄：发送文本 + 换行
JOYSTICK_KEYS = {
    "W": "W",
    "A": "A",
    "S": "S",
    "D": "D",
    "Up": "Up",
    "Down": "Down",
    "Left": "Left",
    "Right": "Right",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}


def normalize_joystick_key(text: str) -> str | None:
    """返回标准化键名，不是手柄键则 None。"""
    t = text.strip()
    if t in JOYSTICK_KEYS:
        return JOYSTICK_KEYS[t]
    t2 = t.splitlines()[0].strip() if t else ""
    return JOYSTICK_KEYS.get(t2)


# ---- 中线提取 ----

def extract_track(
    rgb: bytes,
    width: int,
    height: int,
    threshold: int = 100,
) -> dict:
    """逐行找亮度突变得到左右边线，再算中线。"""
    rows: list[dict] = []
    left_edge: list[list[int]] = []
    right_edge: list[list[int]] = []
    midline: list[list[int]] = []

    for y in range(height):
        base = y * width * 3
        bright = [rgb[base + x * 3] for x in range(width)]

        left = -1
        for x in range(width):
            if bright[x] >= threshold:
                left = x
                break
        right = -1
        for x in range(width - 1, -1, -1):
            if bright[x] >= threshold:
                right = x
                break

        if left >= 0 and right > left:
            mid = (left + right) // 2
            rows.append({"y": y, "left": left, "right": right, "mid": mid})
            left_edge.append([left, y])
            right_edge.append([right, y])
            midline.append([mid, y])

    confidence = len(rows) / height if height else 0.0
    mean_width = (
        sum(r["right"] - r["left"] for r in rows) / len(rows) if rows else 0.0
    )
    return {
        "rows": rows,
        "midline": midline,
        "leftEdge": left_edge,
        "rightEdge": right_edge,
        "confidence": round(confidence, 3),
        "meanWidth": round(mean_width, 2),
        "threshold": threshold,
        "width": width,
        "height": height,
    }


def annotate_track(rgb: bytearray, width: int, height: int, track: dict) -> None:
    """把提取出的边线/中线画到 RGB 上（就地）。"""
    from .image import draw_polyline

    if track.get("leftEdge"):
        draw_polyline(rgb, width, height, [tuple(p) for p in track["leftEdge"]], (255, 60, 60))
    if track.get("rightEdge"):
        draw_polyline(rgb, width, height, [tuple(p) for p in track["rightEdge"]], (60, 100, 255))
    if track.get("midline"):
        draw_polyline(rgb, width, height, [tuple(p) for p in track["midline"]], (255, 220, 40))


def load_png_rgb(path: str | Path) -> tuple[bytes, int, int]:
    """纯标准库解 PNG（8bit RGB/RGBA/Gray，filter 0-4）。"""
    import struct
    import zlib

    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    width = height = 0
    color_type = 0
    bit_depth = 8
    idat = bytearray()
    while pos < len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, bit_depth, color_type, _c, _f, _i = struct.unpack(
                ">IIBBBBB", chunk
            )
            if bit_depth != 8:
                raise ValueError("only 8-bit PNG supported")
        elif tag == b"IDAT":
            idat.extend(chunk)
        elif tag == b"IEND":
            break

    raw = zlib.decompress(bytes(idat))
    if color_type == 2:
        bpp = 3
    elif color_type == 6:
        bpp = 4
    elif color_type == 0:
        bpp = 1
    elif color_type == 4:
        bpp = 2
    else:
        raise ValueError(f"unsupported color_type {color_type}")

    stride = width * bpp
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        if pos >= len(raw):
            break
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + left) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) // 2)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        else:
            raise ValueError(f"bad filter {filter_type}")
        out[y * stride:(y + 1) * stride] = line
        prev = line

    rgb = bytearray(width * height * 3)
    if bpp == 3:
        rgb[:] = out
    elif bpp == 4:
        for i in range(width * height):
            rgb[i * 3:i * 3 + 3] = out[i * 4:i * 4 + 3]
    elif bpp == 1:
        for i in range(width * height):
            v = out[i]
            rgb[i * 3] = rgb[i * 3 + 1] = rgb[i * 3 + 2] = v
    elif bpp == 2:
        for i in range(width * height):
            v = out[i * 2]
            rgb[i * 3] = rgb[i * 3 + 1] = rgb[i * 3 + 2] = v
    return bytes(rgb), width, height


# ---- 像素探针（视觉 bug 排查）----

def rgb_to_gray(rgb: bytes, width: int, height: int) -> bytes:
    """RGB888 转灰度。"""
    out = bytearray(width * height)
    for i in range(width * height):
        r = rgb[i * 3]
        g = rgb[i * 3 + 1]
        b = rgb[i * 3 + 2]
        out[i] = (r * 299 + g * 587 + b * 114) // 1000
    return bytes(out)


def pixel_at(gray: bytes, width: int, height: int, x: int, y: int) -> int:
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(f"out of range ({x},{y}) size {width}x{height}")
    return gray[y * width + x]


def pixel_grid(
    gray: bytes, width: int, height: int,
    cx: int, cy: int, radius: int = 4,
) -> dict:
    """以 (cx,cy) 为中心取 (2r+1)x(2r+1) 灰度网格。"""
    cells = []
    for y in range(cy - radius, cy + radius + 1):
        row = []
        for x in range(cx - radius, cx + radius + 1):
            if 0 <= x < width and 0 <= y < height:
                row.append(gray[y * width + x])
            else:
                row.append(None)
        cells.append(row)
    vals = [v for row in cells for v in row if v is not None]
    return {
        "center": [cx, cy],
        "radius": radius,
        "grid": cells,
        "min": min(vals) if vals else None,
        "max": max(vals) if vals else None,
        "mean": round(sum(vals) / len(vals), 2) if vals else None,
    }


def row_profile(gray: bytes, width: int, height: int, y: int) -> list[int]:
    if not (0 <= y < height):
        raise ValueError(f"row {y} out of range")
    return [gray[y * width + x] for x in range(width)]


def col_profile(gray: bytes, width: int, height: int, x: int) -> list[int]:
    if not (0 <= x < width):
        raise ValueError(f"col {x} out of range")
    return [gray[y * width + x] for y in range(height)]


def histogram(gray: bytes, bins: int = 16) -> dict:
    """灰度直方图。bins=16 → 每桶 16 个灰度。"""
    counts = [0] * bins
    for v in gray:
        counts[min(bins - 1, v * bins // 256)] += 1
    total = len(gray) or 1
    return {
        "bins": bins,
        "counts": counts,
        "ratios": [round(c / total, 4) for c in counts],
        "min": min(gray) if gray else None,
        "max": max(gray) if gray else None,
    }


def format_grid_ascii(grid: list[list[int | None]], width: int = 3) -> str:
    """灰度网格文本表（每格 3 字符宽，None 用 ' - '）。"""
    lines = []
    for row in grid:
        cells = [" - " if v is None else f"{v:3d}" for v in row]
        lines.append("|".join(cells))
    return "\n".join(lines)


def false_color_rgb(gray: bytes, width: int, height: int) -> bytes:
    """伪彩色：0→蓝 128→绿 255→红，便于肉眼分辨灰度。"""
    out = bytearray(width * height * 3)
    for i, v in enumerate(gray):
        if v < 128:
            t = v / 127.0
            r, g, b = 0, int(t * 255), int((1 - t) * 255)
        else:
            t = (v - 128) / 127.0
            r, g, b = int(t * 255), int((1 - t) * 255), 0
        out[i * 3] = r
        out[i * 3 + 1] = g
        out[i * 3 + 2] = b
    return bytes(out)


def otsu_threshold(gray: bytes) -> int:
    """大津法最佳阈值（取方差最大区间中点，避免 0/255 图退化）。"""
    if not gray:
        return 128
    hist = [0] * 256
    for v in gray:
        hist[v] += 1
    total = len(gray)
    sum_total = sum(i * hist[i] for i in range(256))
    sum_b = 0
    w_b = 0
    vars_ = [0.0] * 256
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        vars_[t] = w_b * w_f * (m_b - m_f) ** 2
    max_var = max(vars_)
    if max_var <= 0:
        return 128
    # 方差达到最大值的 t 区间 → 取中点
    best = [t for t in range(256) if vars_[t] == max_var]
    return best[len(best) // 2]