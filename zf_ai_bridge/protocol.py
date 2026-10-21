"""逐飞助手 V2 协议解析。

帧格式来源：seekfree_assistant.c 逆向（见《逐飞助手逆向分析报告》）。
只用标准库。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterator, Optional

HEAD_SEND = 0xAA          # MCU -> PC
HEAD_RECEIVE = 0x55       # PC -> MCU

CMD_CCD = 0x01
CMD_CAMERA = 0x02
CMD_CAMERA_BOUNDARY = 0x03
CMD_CAMERA_RECT = 0x04
CMD_OSCILLOSCOPE = 0x10
CMD_DEBUG_PARAM = 0x20


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


@dataclass
class OscilloscopeFrame:
    channel_max: int
    values: list[float]           # 长度 = channel_max
    data_type: int = 0            # 0=小端 float, 1=STC 大端


@dataclass
class CameraFrame:
    camera_type: int              # 1=二值 2=灰度 3=RGB565
    width: int
    height: int
    data_type: int
    payload: bytes = b""          # 原始像素负载


@dataclass
class CcdFrame:
    channel_index: int
    data_length: int
    channel_color: int
    data_max: int
    data_type: int


@dataclass
class DebugParamFrame:
    channel_index: int            # 1-based
    value: float


@dataclass
class ParseResult:
    frames: list[object] = field(default_factory=list)
    raw_texts: list[str] = field(default_factory=list)
    errors: int = 0


class ProtocolParser:
    """粘包/断包安全的增量解析器。"""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> ParseResult:
        self._buf.extend(data)
        result = ParseResult()
        while True:
            frame = self._try_extract()
            if frame is None:
                break
            if isinstance(frame, str):
                result.raw_texts.append(frame)
            else:
                result.frames.append(frame)
        return result

    # ---- internals -------------------------------------------------

    @staticmethod
    def _looks_like_text(raw: bytes) -> bool:
        """串口助手纯文本的特征：严格 UTF-8、几乎全可打印。

        帧失步后原图灰度载荷里混着 0x0A 和非法 UTF-8 序列，
        以前会被当成文本抛给上层，界面就出现乱码；这里直接挡掉。
        """
        if len(raw) < 2:
            return False
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            return False
        if not s.strip():
            return False
        printable = sum(1 for ch in s if ch >= " " or ch in "\t\r\n")
        return printable / len(s) >= 0.9

    def _try_extract(self):
        buf = self._buf
        # 跳到下一个帧头
        while buf and buf[0] not in (HEAD_SEND, HEAD_RECEIVE):
            # 可能是串口助手的纯文本
            nl = buf.find(b"\n")
            if 0 <= nl < 32 and ProtocolParser._looks_like_text(bytes(buf[: nl + 1])):
                text = bytes(buf[: nl + 1]).decode("utf-8", "replace").strip()
                del buf[: nl + 1]
                if text:
                    return text
            # 不是文本：整段丢到下一个候选帧头，避免逐字节挪动大块像素载荷
            nxt = -1
            for head in (HEAD_SEND, HEAD_RECEIVE):
                i = buf.find(bytes((head,)), 1)
                if i > 0 and (nxt < 0 or i < nxt):
                    nxt = i
            del buf[: nxt if nxt > 0 else 1]

        if len(buf) < 3:
            return None

        head = buf[0]
        cmd = buf[2]

        if head == HEAD_RECEIVE and cmd == CMD_DEBUG_PARAM:
            return self._take_debug_param()
        if head != HEAD_SEND:
            # 未知 PC->MCU 帧，丢掉帧头重扫
            del buf[0]
            return None

        if cmd == CMD_OSCILLOSCOPE:
            return self._take_oscilloscope()
        if cmd == CMD_CAMERA:
            return self._take_camera()
        if cmd == CMD_CCD:
            return self._take_ccd()
        if cmd == CMD_CAMERA_RECT:
            return self._take_rect()
        if cmd == CMD_CAMERA_BOUNDARY:
            return self._take_boundary()
        # 未知命令：丢帧头
        del buf[0]
        return None

    def _take_oscilloscope(self) -> Optional[OscilloscopeFrame]:
        buf = self._buf
        if len(buf) < 8:
            return None
        channel_max = buf[3] or 1
        if channel_max > 16:
            channel_max = 16
        need = 8 + channel_max * 4
        if len(buf) < need:
            return None
        raw = bytes(buf[:need])
        del buf[:need]

        head = raw[:8]
        payload = raw[8:]
        # 校验：head + cmd..reserve + payload；check_sum 字段跳过（位于 raw[1]）
        calc = (HEAD_SEND + sum(head[2:8]) + sum(payload)) & 0xFF
        if calc != raw[1]:
            return None  # 校验失败，静默丢弃

        data_type = raw[4]
        fmt = "<" + "f" * channel_max
        if data_type == 1:  # STC 大端
            fmt = ">" + "f" * channel_max
        values = list(struct.unpack(fmt, payload))
        return OscilloscopeFrame(channel_max=channel_max, values=values, data_type=data_type)

    def _take_camera(self) -> Optional[CameraFrame]:
        buf = self._buf
        if len(buf) < 12:
            return None
        raw_hdr = bytes(buf[:12])
        calc = (HEAD_SEND + sum(raw_hdr[2:12])) & 0xFF
        if calc != raw_hdr[1]:
            del buf[0]
            return None
        camera_type = raw_hdr[3]
        width, height = struct.unpack_from("<HH", raw_hdr, 4)
        data_type = raw_hdr[8]
        del buf[:12]

        if width == 0 or height == 0:
            return CameraFrame(camera_type, width, height, data_type, b"")
        if camera_type == 1:
            need = (width * height) // 8
        elif camera_type == 3:
            need = width * height * 2
        else:
            need = width * height
        if len(buf) < need:
            # 负载还没到齐：把头放回去等待
            self._buf[:0] = raw_hdr
            return None
        payload = bytes(buf[:need])
        del buf[:need]
        return CameraFrame(camera_type, width, height, data_type, payload)

    def _take_ccd(self) -> Optional[CcdFrame]:
        buf = self._buf
        if len(buf) < 12:
            return None
        raw = bytes(buf[:12])
        calc = (HEAD_SEND + sum(raw[2:12])) & 0xFF
        if calc != raw[1]:
            del buf[0]
            return None
        channel_index = raw[3]
        data_length = struct.unpack_from("<H", raw, 4)[0]
        channel_color = struct.unpack_from("<H", raw, 6)[0]
        data_max = struct.unpack_from("<H", raw, 8)[0]
        data_type = raw[10]
        del buf[:12]
        size = data_length * (2 if data_type else 1)
        if len(buf) < size:
            self._buf[:0] = raw
            return None
        del buf[:size]
        return CcdFrame(channel_index, data_length, channel_color, data_max, data_type)

    def _take_rect(self) -> Optional[dict]:
        buf = self._buf
        if len(buf) < 14:
            return None
        raw = bytes(buf[:14])
        calc = (HEAD_SEND + sum(raw[2:14])) & 0xFF
        del buf[:14]
        if calc != raw[1]:
            return None
        x, y, w, h, color = struct.unpack_from("<HHHHH", raw, 4)
        return {"kind": "rect", "x": x, "y": y, "w": w, "h": h, "color": color}

    def _take_boundary(self) -> Optional[dict]:
        buf = self._buf
        if len(buf) < 8:
            return None
        raw = bytes(buf[:8])
        calc = (HEAD_SEND + sum(raw[2:8])) & 0xFF
        if calc != raw[1]:
            del buf[0]
            return None
        data_type = raw[3]
        length = struct.unpack_from("<H", raw, 4)[0]
        color = struct.unpack_from("<H", raw, 6)[0]
        del buf[:8]
        coord_size = 1 if data_type == 0 else 2
        size = length * 2 * coord_size
        if len(buf) < size:
            self._buf[:0] = raw
            return None
        payload = bytes(buf[:size])
        del buf[:size]

        points: list[tuple[int, int]] = []
        if coord_size == 1:
            for i in range(length):
                x = payload[i * 2]
                y = payload[i * 2 + 1]
                points.append((x, y))
        else:
            for i in range(length):
                x, y = struct.unpack_from("<HH", payload, i * 4)
                points.append((x, y))
        return {
            "kind": "boundary",
            "length": length,
            "color": color,
            "data_type": data_type,
            "points": points,
        }

    def _take_debug_param(self) -> Optional[DebugParamFrame]:
        buf = self._buf
        if len(buf) < 8:
            return None
        raw = bytes(buf[:8])
        calc = checksum(bytes(raw[0:1]) + b"\x00" + raw[2:8])
        if calc != raw[1] or raw[2] != CMD_DEBUG_PARAM:
            del buf[0]
            return None
        del buf[:8]
        value = struct.unpack_from("<f", raw, 4)[0]
        return DebugParamFrame(channel_index=raw[3], value=value)


def build_debug_param_frame(channel_index: int, value: float) -> bytes:
    """构造 PC->MCU 调参帧（V2）。"""
    payload = bytearray(8)
    payload[0] = HEAD_RECEIVE
    payload[2] = CMD_DEBUG_PARAM
    payload[3] = channel_index & 0xFF
    struct.pack_into("<f", payload, 4, value)
    payload[1] = checksum(bytes(payload))  # check_sum 槽位为 0
    return bytes(payload)
