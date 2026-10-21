"""图传帧 → PNG，供 AI 直接读图分析。

纯标准库实现 PNG 编码（不强制依赖 Pillow）。
逐飞 V2 摄像头帧类型：
  0x01 二值   W*H/8 字节，每 bit 一个像素
  0x02 灰度   W*H 字节
  0x03 RGB565 W*H*2 字节，小端 RGB565
"""
from __future__ import annotations

import struct
import time
import zlib
import os
from pathlib import Path


CAMERA_BINARY = 1
CAMERA_GRAY = 2
CAMERA_RGB565 = 3


def decode_camera_payload(camera_type: int, width: int, height: int, payload: bytes) -> bytes:
    """返回 RGB888 像素（width*height*3 字节）。"""
    if width <= 0 or height <= 0:
        raise ValueError("bad size")
    if camera_type == CAMERA_BINARY:
        expected = (width * height + 7) // 8
        if len(payload) < expected:
            raise ValueError(f"binary payload short: {len(payload)} < {expected}")
        out = bytearray(width * height * 3)
        for y in range(height):
            for x in range(width):
                idx = y * width + x
                byte = payload[idx // 8]
                bit = 7 - (idx % 8)
                v = 255 if (byte >> bit) & 1 else 0
                out[idx * 3] = out[idx * 3 + 1] = out[idx * 3 + 2] = v
        return bytes(out)
    if camera_type == CAMERA_GRAY:
        expected = width * height
        if len(payload) < expected:
            raise ValueError(f"gray payload short: {len(payload)} < {expected}")
        out = bytearray(width * height * 3)
        for i in range(width * height):
            v = payload[i]
            out[i * 3] = out[i * 3 + 1] = out[i * 3 + 2] = v
        return bytes(out)
    if camera_type == CAMERA_RGB565:
        expected = width * height * 2
        if len(payload) < expected:
            raise ValueError(f"rgb565 payload short: {len(payload)} < {expected}")
        out = bytearray(width * height * 3)
        for i in range(width * height):
            pix = payload[i * 2] | (payload[i * 2 + 1] << 8)
            r = ((pix >> 11) & 0x1F) << 3
            g = ((pix >> 5) & 0x3F) << 2
            b = (pix & 0x1F) << 3
            out[i * 3] = r
            out[i * 3 + 1] = g
            out[i * 3 + 2] = b
        return bytes(out)
    raise ValueError(f"unknown camera_type {camera_type}")


def write_png(path: str | Path, width: int, height: int, rgb: bytes) -> None:
    """写 8bit RGB PNG（纯 zlib）。"""
    expected = width * height * 3
    if len(rgb) != expected:
        raise ValueError(f"rgb length {len(rgb)} != {expected}")
    raw = bytearray()
    row = width * 3
    for y in range(height):
        raw.append(0)  # filter: None
        raw.extend(rgb[y * row:(y + 1) * row])
    compressed = zlib.compress(bytes(raw), level=6)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    target = Path(path)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_bytes(png)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class FrameSink:
    """latest/历史/内存均保留原始像素，算法标注只用于独立预览。"""

    def __init__(self, out_dir: str | Path, save_every: int = 1, max_keep: int = 30) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.save_every = max(1, save_every)
        self.max_keep = max_keep
        # 预览用的内存快照：UI 直接读这里，不落盘、无竞争
        self.last_rgb: bytes = b""
        self.last_annotated_rgb: bytes = b""
        self.save_annotated = False
        # 叠加开关：边线（左紫/右青）与中线（路径）可分别显示
        self.show_edges = False
        self.show_center = False
        # 一次赋值发布不可变快照，供UI/标定读取同帧尺寸、像素和模式。
        self.last_frame: tuple[int, int, int, bytes, bytes] | None = None
        self.last_size: tuple[int, int] = (0, 0)
        self._count = 0
        self.last_path: Path | None = None
        self.last_meta: dict | None = None
        # 标注：与下一帧图像同组，画完即清（联测反馈 #2）
        self._boundaries: list[dict] = []
        self._rects: list[dict] = []
        # 帧时序统计
        self._last_ts: float = 0.0
        self._max_gap: float = 0.0
        self._gaps: list[float] = []
        self.camera_type_last = 0

    def add_boundary(self, points: list[tuple[int, int]], color: tuple[int, int, int] = (255, 0, 0)) -> None:
        """与下一帧图像同组；空点列忽略。"""
        if not points:
            return
        self._boundaries.append({"points": points, "color": color})
        if len(self._boundaries) > 8:
            self._boundaries = self._boundaries[-8:]

    def add_rect(self, x: int, y: int, w: int, h: int, color: tuple[int, int, int] = (0, 255, 0)) -> None:
        if w <= 0 or h <= 0:
            return
        self._rects.append({"x": x, "y": y, "w": w, "h": h, "color": color})
        if len(self._rects) > 8:
            self._rects = self._rects[-8:]

    def clear_overlays(self) -> None:
        self._boundaries.clear()
        self._rects.clear()

    def stats(self) -> dict:
        gaps = self._gaps[-50:] if self._gaps else []
        avg_gap = sum(gaps) / len(gaps) if gaps else 0.0
        return {
            "frames": self._count,
            "lastCameraType": self.camera_type_last,
            "maxGapSeconds": round(self._max_gap, 3),
            "avgGapSeconds": round(avg_gap, 3),
            "estFps": round(1.0 / avg_gap, 2) if avg_gap > 0 else 0.0,
        }

    def on_camera(self, camera_type: int, width: int, height: int, payload: bytes) -> dict:
        self._count += 1
        self.camera_type_last = camera_type
        now = time.time()
        if self._last_ts > 0:
            gap = now - self._last_ts
            self._gaps.append(gap)
            if gap > self._max_gap:
                self._max_gap = gap
        self._last_ts = now

        rgb_bytes = decode_camera_payload(camera_type, width, height, payload)
        n_b, n_r = len(self._boundaries), len(self._rects)
        annotated = bytearray(rgb_bytes)
        self._draw_overlays(annotated, width, height)
        self.clear_overlays()
        annotated_bytes = bytes(annotated)

        # 预览快照留在内存里，UI 直接取用，避免文件读写竞争导致的闪烁
        self.last_rgb = rgb_bytes
        self.last_annotated_rgb = annotated_bytes
        self.last_size = (width, height)
        self.last_frame = (camera_type, width, height, rgb_bytes, annotated_bytes)

        latest = self.out_dir / "latest.png"
        raw_saved = False
        try:
            write_png(latest, width, height, rgb_bytes)
            raw_saved = True
        except OSError:
            pass

        annotated_path = None
        if self.save_annotated:
            try:
                annotated_path = self.out_dir / "latest_annotated.png"
                write_png(annotated_path, width, height, annotated_bytes)
            except OSError:
                annotated_path = None

        # 归档帧按 save_every 抽样，避免每帧两次 PNG 压缩
        path = self.last_path
        if self._count % self.save_every == 0:
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = self.out_dir / f"frame_{ts}_{self._count:05d}_{width}x{height}.png"
            try:
                write_png(path, width, height, rgb_bytes)
                self.last_path = path
            except OSError:
                path = self.last_path
        self.last_meta = {
            "event": "frame",
            "path": str(path),
            "latestPath": str(latest),
            "raw": True,
            "rawSaved": raw_saved,
            "annotatedPath": str(annotated_path) if annotated_path else None,
            "width": width,
            "height": height,
            "cameraType": camera_type,
            "seq": self._count,
            "overlays": {"boundaries": n_b, "rects": n_r},
            "overlaysAppliedToRaw": False,
        } | self.stats()
        self._prune()
        return self.last_meta

    def _draw_overlays(self, rgb: bytearray, width: int, height: int) -> None:
        for rect in self._rects:
            draw_rect(rgb, width, height, rect["x"], rect["y"], rect["w"], rect["h"], rect["color"])
        for b in self._boundaries:
            if b["color"] in EDGE_COLORS:
                if not self.show_edges:
                    continue
            elif not self.show_center:
                continue
            draw_polyline(rgb, width, height, b["points"], b["color"])

    def _prune(self) -> None:
        frames = sorted(self.out_dir.glob("frame_*.png"))
        while len(frames) > self.max_keep:
            try:
                frames[0].unlink()
            except OSError:
                break
            frames = frames[1:]


# ---- 绘制原语（RGB888 就地修改）----

def rgb565_to_rgb888(color: int) -> tuple[int, int, int]:
    r = ((color >> 11) & 0x1F) << 3
    g = ((color >> 5) & 0x3F) << 2
    b = (color & 0x1F) << 3
    return r, g, b


def _plot(rgb: bytearray, width: int, height: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= x < width and 0 <= y < height:
        i = (y * width + x) * 3
        rgb[i] = color[0]
        rgb[i + 1] = color[1]
        rgb[i + 2] = color[2]


def draw_line(rgb: bytearray, width: int, height: int,
              x0: int, y0: int, x1: int, y1: int,
              color: tuple[int, int, int]) -> None:
    """Bresenham 直线。"""
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        _plot(rgb, width, height, x0, y0, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def draw_polyline(rgb: bytearray, width: int, height: int,
                  points: list[tuple[int, int]],
                  color: tuple[int, int, int],
                  closed: bool = False) -> None:
    if not points:
        return
    pts = [(int(x), int(y)) for x, y in points]
    for i in range(len(pts) - 1):
        draw_line(rgb, width, height, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], color)
    if closed and len(pts) > 2:
        draw_line(rgb, width, height, pts[-1][0], pts[-1][1], pts[0][0], pts[0][1], color)
    # 端点加粗为 2x2
    for x, y in (pts[0], pts[-1]):
        for dx in (0, 1):
            for dy in (0, 1):
                _plot(rgb, width, height, x + dx, y + dy, color)


def draw_rect(rgb: bytearray, width: int, height: int,
              x: int, y: int, w: int, h: int,
              color: tuple[int, int, int]) -> None:
    x, y, w, h = int(x), int(y), int(w), int(h)
    draw_line(rgb, width, height, x, y, x + w, y, color)
    draw_line(rgb, width, height, x + w, y, x + w, y + h, color)
    draw_line(rgb, width, height, x + w, y + h, x, y + h, color)
    draw_line(rgb, width, height, x, y + h, x, y, color)


def save_test_pattern(path: str | Path, width: int = 64, height: int = 48) -> Path:
    """生成测试图（棋盘 + 渐变），用于离线自检。"""
    rgb = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 3
            if (x // 8 + y // 8) % 2 == 0:
                rgb[i] = rgb[i + 1] = rgb[i + 2] = 220
            else:
                rgb[i] = x * 255 // max(1, width - 1)
                rgb[i + 1] = y * 255 // max(1, height - 1)
                rgb[i + 2] = 80
    write_png(path, width, height, bytes(rgb))
    return Path(path)


# app.py 在 add_boundary 前已把 RGB565 转 RGB888，这里按转换后的值判定边线
EDGE_COLORS = (rgb565_to_rgb888(0xF81F), rgb565_to_rgb888(0x07FF))
