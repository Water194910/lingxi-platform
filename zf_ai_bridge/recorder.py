"""把预览帧录成未压缩 RGB 的 AVI（纯标准库，无 ffmpeg 依赖）。

容器结构：RIFF('AVI ') → LIST('hdrl') → LIST('movi') → idx1。
像素按 BI_RGB 存 BGR、行 4 字节对齐、自下而上，兼容 VLC / Windows 播放器。
"""
from __future__ import annotations

import struct
from pathlib import Path


class AviWriter:
    """逐帧写入；stop() 回填三处长度与总帧数。"""

    def __init__(self, path: str | Path, width: int, height: int, fps: int = 15) -> None:
        self.path = Path(path)
        self.width = int(width)
        self.height = int(height)
        self.fps = max(1, int(fps))
        self.frames = 0
        self.bytes_written = 0
        self._index: list[tuple[int, int]] = []
        self._f = self.path.open("wb")
        self._write_headers()

    def _write_headers(self) -> None:
        stride = self.width * 3
        stride += (-stride) % 4
        self._frame_size = stride * self.height
        avih = struct.pack(
            "<IIIIIIIIII4I",
            int(1_000_000 / self.fps),
            self._frame_size * self.fps,
            0, 0x10, 0, 0, 1, self._frame_size, self.width, self.height,
            0, 0, 0, 0,
        )
        strh = struct.pack(
            "<4s4sIHHIIIIIIIIhhhh",
            b"vids", b"DIB ", 0, 0, 0, 0, 1, self.fps, 0, 0,
            self._frame_size, 0xFFFFFFFF, 0, 0, 0, self.width, self.height,
        )
        strf = struct.pack(
            "<IiiHHIIiiII",
            40, self.width, self.height, 1, 24, 0, self._frame_size, 0, 0, 0, 0,
        )
        strl = b"strl" + self._chunk(b"strh", strh) + self._chunk(b"strf", strf)
        hdrl = b"hdrl" + self._chunk(b"avih", avih) + self._chunk(b"LIST", strl)

        f = self._f
        f.write(b"RIFF" + struct.pack("<I", 0) + b"AVI ")
        self._riff_size_pos = 4
        f.write(b"LIST" + struct.pack("<I", 0) + hdrl)
        self._hdrl_size_pos = 16  # 12(RIFF头) + 4('LIST')
        self._hdrl_end = f.tell()
        self._avih_frames_pos = 12 + 8 + 4 + 8 + 16  # RIFF + LIST头 + 'hdrl' + avih头 + dwTotalFrames
        f.write(b"LIST" + struct.pack("<I", 0) + b"movi")
        self._movi_size_pos = f.tell() - 8  # 指向 movi LIST 的 size 字段
        self._movi_data_start = f.tell()

    @staticmethod
    def _chunk(tag: bytes, payload: bytes) -> bytes:
        pad = b"\x00" if len(payload) % 2 else b""
        return tag + struct.pack("<I", len(payload)) + payload + pad

    def add_rgb(self, rgb: bytes) -> None:
        """rgb 为 width*height*3 的 RGB888。"""
        if len(rgb) < self.width * self.height * 3:
            return
        stride = self.width * 3
        pad = (-stride) % 4
        rows = []
        for y in range(self.height - 1, -1, -1):
            src = rgb[y * stride:(y + 1) * stride]
            line = bytearray(stride)
            for i in range(0, stride, 3):
                line[i] = src[i + 2]
                line[i + 1] = src[i + 1]
                line[i + 2] = src[i]
            rows.append(bytes(line) + b"\x00" * pad)
        data = b"".join(rows)
        offset = self._f.tell() - self._movi_data_start
        self._f.write(b"00db" + struct.pack("<I", len(data)) + data)
        self._index.append((offset, len(data)))
        self.frames += 1
        self.bytes_written += len(data) + 8

    def stop(self) -> tuple[int, int]:
        """回填长度与帧数并收尾，返回 (帧数, 文件字节数)。"""
        if self._f.closed:
            return self.frames, self.bytes_written
        f = self._f
        movi_end = f.tell()
        idx = b"".join(b"00db" + struct.pack("<III", 0x10, o, s) for o, s in self._index)
        f.write(self._chunk(b"idx1", idx))
        end = f.tell()
        f.seek(self._riff_size_pos)
        f.write(struct.pack("<I", end - 8))
        f.seek(self._hdrl_size_pos)
        f.write(struct.pack("<I", self._hdrl_end - 20))
        f.seek(self._movi_size_pos)
        f.write(struct.pack("<I", movi_end - self._movi_size_pos - 4))
        f.seek(self._avih_frames_pos)
        f.write(struct.pack("<I", self.frames))
        f.seek(0, 2)
        f.close()
        return self.frames, end

    def close(self) -> None:
        if not self._f.closed:
            self.stop()
