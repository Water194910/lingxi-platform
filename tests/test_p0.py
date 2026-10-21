"""P0 自检：协议解析 + 桥发布，不依赖硬件。"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zf_ai_bridge.bridge import AiBridge, BridgeConfig
from zf_ai_bridge.inputs import build_oscilloscope_frame
from zf_ai_bridge.protocol import (
    DebugParamFrame,
    OscilloscopeFrame,
    ProtocolParser,
    build_debug_param_frame,
)


def test_protocol_oscilloscope() -> None:
    frame = build_oscilloscope_frame([1.5, -2.25, 3.0, 0.125])
    parser = ProtocolParser()
    result = parser.feed(frame)
    assert len(result.frames) == 1, result
    f = result.frames[0]
    assert isinstance(f, OscilloscopeFrame)
    assert f.channel_max == 4
    assert abs(f.values[0] - 1.5) < 1e-6
    assert abs(f.values[1] + 2.25) < 1e-6
    print("PASS protocol/oscilloscope")


def test_protocol_split_feed() -> None:
    frame = build_oscilloscope_frame([1.0, 2.0])
    parser = ProtocolParser()
    r1 = parser.feed(frame[:5])
    assert not r1.frames
    r2 = parser.feed(frame[5:])
    assert len(r2.frames) == 1
    print("PASS protocol/split-feed")


def test_protocol_debug_param() -> None:
    raw = build_debug_param_frame(3, 0.25)
    parser = ProtocolParser()
    result = parser.feed(raw)
    assert len(result.frames) == 1
    f = result.frames[0]
    assert isinstance(f, DebugParamFrame)
    assert f.channel_index == 3
    assert abs(f.value - 0.25) < 1e-6
    print("PASS protocol/debug-param")


def test_bridge_snapshot() -> None:
    bridge = AiBridge(BridgeConfig(port=8766))
    bridge.start()
    time.sleep(0.05)

    sock = socket.create_connection(("127.0.0.1", 8766), timeout=3)
    sock.settimeout(2.0)
    stream = sock.makefile("rwb", buffering=0)
    hello = json.loads(stream.readline())
    assert hello["type"] == "hello", hello

    frame = build_oscilloscope_frame([0.5, 1.5])
    result = ProtocolParser().feed(frame)
    assert len(result.frames) == 1
    f = result.frames[0]
    assert isinstance(f, OscilloscopeFrame)
    chans = [{"key": f"ch{i+1}", "unit": ""} for i in range(f.channel_max)]
    samples = [{"values": {f"ch{i+1}": v for i, v in enumerate(f.values)}}]
    bridge.publish_samples(chans, samples)

    msg = json.loads(stream.readline())
    stream.close()
    sock.close()
    bridge.stop()

    assert msg.get("type") == "samples", msg
    assert msg["samples"][0]["values"]["ch1"] == 0.5
    assert msg["schema"] in ("zf.telemetry/v1", "ek.telemetry/v1")
    print("PASS bridge/snapshot")


def test_write_gate() -> None:
    bridge = AiBridge(BridgeConfig(port=8767, allow_write=False))
    bridge.start()
    time.sleep(0.05)
    sock = socket.create_connection(("127.0.0.1", 8767), timeout=3)
    stream = sock.makefile("rwb", buffering=0)
    json.loads(stream.readline())  # hello
    stream.write(json.dumps({
        "type": "serial.write", "id": "t1", "text": "ch1=1.0", "lineEnding": "lf"
    }).encode() + b"\n")
    stream.flush()
    resp = json.loads(stream.readline())
    assert resp["ok"] is False and "disabled" in resp["error"]
    stream.close()
    sock.close()
    bridge.stop()
    print("PASS bridge/write-gate")


def test_write_path_loopback() -> None:
    """写路径端到端：chN=value → 0x20 帧 → 回环解析。"""
    from zf_ai_bridge.inputs import LoopbackSource

    received: list[object] = []

    def on_bytes(data: bytes) -> None:
        received.extend(ProtocolParser().feed(data).frames)

    src = LoopbackSource(on_bytes)
    frame = build_debug_param_frame(3, 0.25)
    assert src.write(frame)
    assert len(src.written) == 1

    param_frames = [f for f in received if isinstance(f, DebugParamFrame)]
    assert len(param_frames) == 1
    assert param_frames[0].channel_index == 3
    assert abs(param_frames[0].value - 0.25) < 1e-6
    print("PASS write/loopback")


def test_write_path_via_bridge() -> None:
    """桥 write → 真实输出通道。"""
    from zf_ai_bridge.inputs import LoopbackSource

    received: list[object] = []
    parser = ProtocolParser()

    def on_bytes(data: bytes) -> None:
        received.extend(parser.feed(data).frames)

    src = LoopbackSource(on_bytes)
    bridge = AiBridge(BridgeConfig(port=8768, allow_write=True))
    bridge.set_write_handler(lambda text, le: _do_write(src, text, le))
    bridge.start()
    time.sleep(0.05)

    sock = socket.create_connection(("127.0.0.1", 8768), timeout=3)
    sock.settimeout(2.0)
    stream = sock.makefile("rwb", buffering=0)
    json.loads(stream.readline())  # hello
    stream.write(json.dumps({
        "type": "serial.write", "id": "w1", "text": "ch2=0.5", "lineEnding": "lf"
    }).encode() + b"\n")
    stream.flush()
    resp = json.loads(stream.readline())
    stream.close()
    sock.close()
    bridge.stop()

    assert resp["ok"] is True, resp
    assert "sent" in resp.get("message", "")
    assert len(src.written) == 1
    params = [f for f in received if isinstance(f, DebugParamFrame)]
    assert params and params[0].channel_index == 2 and abs(params[0].value - 0.5) < 1e-6
    print("PASS write/bridge")


def _do_write(src, text: str, line_ending: str):
    from zf_ai_bridge.inputs import LoopbackSource  # noqa: F401
    from zf_ai_bridge.protocol import build_debug_param_frame as build

    key, _, val = text.partition("=")
    ch = int(key.strip().lower().replace("ch", ""))
    value = float(val.strip())
    frame = build(ch, value)
    ok = src.write(frame)
    return ok, f"sent {frame.hex()}" if ok else "fail"


def test_bounds_reject() -> None:
    """越界写应被拒绝（通过 __main__ 的边界逻辑单测）。"""
    from zf_ai_bridge.inputs import LoopbackSource

    src = LoopbackSource(lambda b: None)
    bounds = {3: {"min": 0.0, "max": 1.0, "step": 0.01, "restore": 0.5}}

    def check(text: str) -> tuple[bool, str]:
        key, _, val = text.partition("=")
        ch = int(key.strip().lower().replace("ch", ""))
        value = float(val.strip())
        if ch in bounds:
            b = bounds[ch]
            if value < b["min"] or value > b["max"]:
                return False, "out of range"
        frame = build_debug_param_frame(ch, value)
        return src.write(frame), "ok"

    ok, msg = check("ch3=1.5")
    assert ok is False and "out of range" in msg
    ok, msg = check("ch3=0.5")
    assert ok is True
    print("PASS write/bounds")


def test_image_png_roundtrip() -> None:
    """PNG 编码 + 二值/灰度/RGB565 解码。"""
    import tempfile
    from pathlib import Path

    from zf_ai_bridge.image import (
        decode_camera_payload,
        save_test_pattern,
        write_png,
    )

    with tempfile.TemporaryDirectory() as td:
        # 测试图可被 PNG 解码器重新读取大小
        p = Path(td) / "t.png"
        save_test_pattern(p, 16, 12)
        data = p.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert b"IHDR" in data and b"IEND" in data

        # 二值：8x8 → 8 字节
        payload = bytes([0xAA] * 8)
        rgb = decode_camera_payload(1, 8, 8, payload)
        assert len(rgb) == 8 * 8 * 3
        assert rgb[0] == 255 or rgb[0] == 0

        # 灰度 4x4
        gray = bytes(range(16))
        rgb = decode_camera_payload(2, 4, 4, gray)
        assert len(rgb) == 48 and rgb[0] == 0 and rgb[3] == 1

        # RGB565：红/绿/蓝
        import struct as _s
        red = _s.pack("<H", 0xF800)
        green = _s.pack("<H", 0x07E0)
        blue = _s.pack("<H", 0x001F)
        rgb = decode_camera_payload(3, 3, 1, red + green + blue)
        assert rgb[0] > 240 and rgb[1] < 20  # 红
        assert rgb[4] > 240 and rgb[3] < 20  # 绿
        assert rgb[8] > 240 and rgb[6] < 20  # 蓝

        write_png(Path(td) / "w.png", 2, 2, bytes(12))
    print("PASS image/png")


def test_camera_frame_to_png() -> None:
    """摄像头帧 → 解析 → FrameSink 出 PNG。"""
    import struct
    import tempfile
    from pathlib import Path

    from zf_ai_bridge.image import FrameSink
    from zf_ai_bridge.protocol import CameraFrame, ProtocolParser

    # 构造 8x8 灰度摄像头帧
    width, height = 8, 8
    payload = bytes((i * 3) & 0xFF for i in range(width * height))
    hdr = bytearray(12)
    hdr[0] = 0xAA
    hdr[2] = 0x02
    hdr[3] = 2  # 灰度
    struct.pack_into("<HH", hdr, 4, width, height)
    hdr[8] = 1
    hdr[1] = (0xAA + sum(hdr[2:12])) & 0xFF
    frame_bytes = bytes(hdr) + payload

    result = ProtocolParser().feed(frame_bytes)
    assert len(result.frames) == 1
    cam = result.frames[0]
    assert isinstance(cam, CameraFrame)
    assert cam.width == 8 and cam.height == 8 and len(cam.payload) == 64

    with tempfile.TemporaryDirectory() as td:
        sink = FrameSink(td, save_every=1)
        meta = sink.on_camera(cam.camera_type, cam.width, cam.height, cam.payload)
        assert meta and meta["width"] == 8
        latest = Path(td) / "latest.png"
        assert latest.is_file() and latest.stat().st_size > 50
        assert Path(meta["path"]).is_file()
    print("PASS image/camera-frame")


def test_boundary_parse_and_overlay() -> None:
    """边线帧解析坐标 + 叠加绘制。"""
    import struct
    import tempfile
    from pathlib import Path

    from zf_ai_bridge.image import FrameSink
    from zf_ai_bridge.protocol import ProtocolParser

    # 边线帧：3 个点，uint8 坐标，红色 0xF800
    pts = [(10, 10), (20, 30), (30, 15)]
    payload = bytearray()
    for x, y in pts:
        payload += bytes([x, y])
    hdr = bytearray(8)
    hdr[0] = 0xAA
    hdr[2] = 0x03
    hdr[3] = 0  # uint8
    struct.pack_into("<HH", hdr, 4, 3, 0xF800)
    hdr[1] = (0xAA + sum(hdr[2:8])) & 0xFF
    raw = bytes(hdr) + bytes(payload)

    result = ProtocolParser().feed(raw)
    assert len(result.frames) == 1
    b = result.frames[0]
    assert b["kind"] == "boundary" and b["length"] == 3
    assert b["points"] == pts

    # 矩形帧
    rhdr = bytearray(14)
    rhdr[0] = 0xAA
    rhdr[2] = 0x04
    struct.pack_into("<HHHHH", rhdr, 4, 5, 5, 20, 10, 0x07E0)
    rhdr[1] = (0xAA + sum(rhdr[2:14])) & 0xFF
    result2 = ProtocolParser().feed(bytes(rhdr))
    assert len(result2.frames) == 1
    r = result2.frames[0]
    assert r["kind"] == "rect" and r["x"] == 5 and r["w"] == 20

    # 叠加到灰度图
    with tempfile.TemporaryDirectory() as td:
        sink = FrameSink(td)
        from zf_ai_bridge.image import rgb565_to_rgb888
        sink.add_boundary(b["points"], rgb565_to_rgb888(b["color"]))
        sink.add_rect(r["x"], r["y"], r["w"], r["h"], rgb565_to_rgb888(r["color"]))
        gray = bytes(64 * 64)  # 全黑
        meta = sink.on_camera(2, 64, 64, gray)
        assert meta["overlays"]["boundaries"] == 1
        assert meta["overlays"]["rects"] == 1
        data = Path(td, "latest.png").read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
    print("PASS image/overlay")


def test_joystick_and_midline() -> None:
    """手柄命令 + 中线提取。"""
    from zf_ai_bridge.extras import (
        extract_track,
        load_png_rgb,
        normalize_joystick_key,
    )
    from zf_ai_bridge.image import write_png

    # 手柄键
    assert normalize_joystick_key("W") == "W"
    assert normalize_joystick_key("up") == "Up"
    assert normalize_joystick_key("Down") == "Down"
    assert normalize_joystick_key("ch3=1") is None

    # 中线：构造 40x30 中间亮条
    w, h = 40, 30
    rgb = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            v = 200 if 10 <= x <= 30 else 20
            i = (y * w + x) * 3
            rgb[i] = rgb[i + 1] = rgb[i + 2] = v
    track = extract_track(bytes(rgb), w, h, threshold=100)
    assert track["confidence"] == 1.0
    assert abs(track["meanWidth"] - 21) < 2
    # 中线应约在 x=20
    assert all(abs(p[0] - 20) <= 1 for p in track["midline"])

    # PNG 可再读
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.png"
        write_png(p, w, h, bytes(rgb))
        rgb2, w2, h2 = load_png_rgb(p)
        assert w2 == w and h2 == h
        track2 = extract_track(rgb2, w2, h2, threshold=100)
        assert track2["confidence"] == 1.0
    print("PASS extras/joystick+midline")


def test_tcp_listen_source() -> None:
    """WiFi 场景：桥当 TCP 服务端，模块/客户端连进来发帧、也能收写回。"""
    import socket
    import time

    from zf_ai_bridge.inputs import TcpListenSource
    from zf_ai_bridge.protocol import OscilloscopeFrame, ProtocolParser
    from zf_ai_bridge.inputs import build_oscilloscope_frame

    received: list[object] = []
    parser = ProtocolParser()

    def on_bytes(data: bytes) -> None:
        received.extend(parser.feed(data).frames)

    src = TcpListenSource("127.0.0.1", 8790, on_bytes)
    src.start()
    time.sleep(0.15)

    client = socket.create_connection(("127.0.0.1", 8790), timeout=3)
    client.sendall(build_oscilloscope_frame([1.0, 2.0]))
    time.sleep(0.2)

    assert any(isinstance(f, OscilloscopeFrame) for f in received), received
    assert src.writable
    assert src.write(b"\x55\x00")

    client.close()
    src.stop()
    print("PASS input/tcp-listen")


def test_pixel_probe() -> None:
    """像素探针：取值/网格/直方图/伪彩色。"""
    from zf_ai_bridge.extras import (
        false_color_rgb,
        format_grid_ascii,
        histogram,
        pixel_at,
        pixel_grid,
        rgb_to_gray,
        row_profile,
    )

    w, h = 16, 8
    rgb = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            v = x * 16  # 0..240
            i = (y * w + x) * 3
            rgb[i] = rgb[i + 1] = rgb[i + 2] = v
    gray = rgb_to_gray(bytes(rgb), w, h)
    assert pixel_at(gray, w, h, 5, 3) == 80
    g = pixel_grid(gray, w, h, 5, 3, radius=1)
    assert g["min"] == 64 and g["max"] == 96
    assert "80" in format_grid_ascii(g["grid"])
    prof = row_profile(gray, w, h, 0)
    assert prof[0] == 0 and prof[-1] == 240
    hist = histogram(gray)
    assert hist["min"] == 0 and hist["max"] == 240
    fc = false_color_rgb(gray, w, h)
    assert len(fc) == w * h * 3
    print("PASS probe/pixels")


def test_overlay_oneshot_and_stats() -> None:
    """联测反馈：标注只作用同帧、统计 estFps/maxGap。"""
    import struct
    import tempfile
    from pathlib import Path

    from zf_ai_bridge.image import FrameSink

    with tempfile.TemporaryDirectory() as td:
        sink = FrameSink(td)
        sink.add_boundary([(1, 1), (5, 5)], (255, 0, 0))
        sink.add_rect(2, 2, 4, 4, (0, 255, 0))
        assert len(sink._boundaries) == 1 and len(sink._rects) == 1

        gray = bytes(16 * 16)
        meta = sink.on_camera(2, 16, 16, gray)
        assert meta["overlays"]["boundaries"] == 1
        assert meta["overlays"]["rects"] == 1
        # 画完即清
        assert len(sink._boundaries) == 0 and len(sink._rects) == 0

        meta2 = sink.on_camera(1, 16, 16, gray)
        assert meta2["overlays"]["boundaries"] == 0
        assert meta2["frames"] == 2
        assert "estFps" in meta2 and "maxGapSeconds" in meta2

        # 空边线忽略
        sink.add_boundary([], (1, 2, 3))
        assert len(sink._boundaries) == 0
    print("PASS overlay/oneshot+stats")


if __name__ == "__main__":
    test_protocol_oscilloscope()
    test_protocol_split_feed()
    test_protocol_debug_param()
    test_bridge_snapshot()
    test_write_gate()
    test_write_path_loopback()
    test_write_path_via_bridge()
    test_bounds_reject()
    test_image_png_roundtrip()
    test_camera_frame_to_png()
    test_boundary_parse_and_overlay()
    test_joystick_and_midline()
    test_tcp_listen_source()
    test_pixel_probe()
    test_overlay_oneshot_and_stats()
    print("ALL PASS")
