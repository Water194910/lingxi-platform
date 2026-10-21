"""zf-ai-bridge 入口。

示例：
  python -m zf_ai_bridge --input demo:// --port 8765
  python -m zf_ai_bridge --input tcp://192.168.4.1:8086 --allow-write
  python -m zf_ai_bridge --input serial://COM5:115200 --allow-write --bounds bounds.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .bridge import AiBridge, BridgeConfig
from .extras import normalize_joystick_key
from .image import FrameSink
from .inputs import parse_input_url
from .protocol import (
    CameraFrame,
    DebugParamFrame,
    OscilloscopeFrame,
    ProtocolParser,
    build_debug_param_frame,
)
from .tap import TapForwarder

LINE_ENDINGS = {
    "none": b"",
    "lf": b"\n",
    "crlf": b"\r\n",
    "cr": b"\r",
}


def load_bounds(path: str) -> dict[int, dict]:
    """bounds.json: {"3": {"min": 0, "max": 1, "step": 0.01, "restore": 0.5}}"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[int, dict] = {}
    for key, val in data.items():
        out[int(key)] = val
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="逐飞助手 AI 桥接（zf.telemetry/v1）")
    parser.add_argument("--input", default="demo://",
                        help="demo:// | loop:// | file://path | tcp://host:port | tcp-listen://host:port | serial://COMx:baud")
    parser.add_argument("--host", default="127.0.0.1", help="桥监听地址（仅本机）")
    parser.add_argument("--port", type=int, default=8765, help="桥监听端口")
    parser.add_argument("--allow-write", action="store_true",
                        help="允许 AI 写设备（默认关闭）")
    parser.add_argument("--bounds", default="",
                        help="参数边界 JSON 文件：{\"ch\": {min,max,step,restore}}")
    parser.add_argument("--frames-dir", default="frames",
                        help="图传 PNG 输出目录（AI 读这里）")
    parser.add_argument("--save-every", type=int, default=1,
                        help="每 N 帧存一张 PNG（默认 1）")
    parser.add_argument("--save-annotated", action="store_true",
                        help="另外保存latest_annotated.png；latest.png始终保留原始像素")
    parser.add_argument("--tap", default="",
                        help="旁路转发到官方逐飞助手，如 127.0.0.1:8086（其 TCP 服务端本地端口）")
    parser.add_argument("--verbose", action="store_true", help="打印解析到的帧")
    args = parser.parse_args(argv)

    bounds: dict[int, dict] = {}
    if args.bounds:
        bounds = load_bounds(args.bounds)
        print(f"  bounds     = {args.bounds} ({len(bounds)} channels)", flush=True)

    proto = ProtocolParser()
    bridge = AiBridge(BridgeConfig(host=args.host, port=args.port, allow_write=args.allow_write))
    frame_sink = FrameSink(args.frames_dir, save_every=args.save_every)
    frame_sink.save_annotated = args.save_annotated
    tap = TapForwarder(args.tap) if args.tap else None

    pending: list[OscilloscopeFrame] = []

    def on_bytes(data: bytes) -> None:
        # 旁路转发原始字节（官方逐飞助手同屏）
        if tap is not None:
            tap.forward(data)
        result = proto.feed(data)
        for frame in result.frames:
            if args.verbose:
                print(f"[frame] {frame}", flush=True)
            if isinstance(frame, OscilloscopeFrame):
                pending.append(frame)
                if len(pending) >= 1:
                    chans = [{"key": f"ch{i+1}", "unit": ""} for i in range(frame.channel_max)]
                    samples = [{"values": {f"ch{i+1}": v for i, v in enumerate(frame.values)}}]
                    bridge.publish_samples(chans, samples, sample_rate_hz=0)
                    pending.clear()
            elif isinstance(frame, CameraFrame):
                meta = frame_sink.on_camera(
                    frame.camera_type, frame.width, frame.height, frame.payload
                )
                if meta:
                    bridge.publish_event("frame", meta)
                    if args.verbose:
                        print(f"[frame] saved {meta.get('path')}", flush=True)
            elif isinstance(frame, DebugParamFrame):
                bridge.publish_event("param", {
                    "channel": frame.channel_index,
                    "value": frame.value,
                })
            elif isinstance(frame, dict):
                kind = frame.get("kind")
                if kind == "boundary":
                    from .image import rgb565_to_rgb888
                    color = rgb565_to_rgb888(frame.get("color", 0xF800))
                    frame_sink.add_boundary(frame.get("points", []), color)
                    bridge.publish_event("boundary", {
                        "length": frame.get("length"),
                        "color": frame.get("color"),
                    })
                elif kind == "rect":
                    from .image import rgb565_to_rgb888
                    color = rgb565_to_rgb888(frame.get("color", 0x07E0))
                    frame_sink.add_rect(frame["x"], frame["y"], frame["w"], frame["h"], color)
                    bridge.publish_event("rect", frame)
        for text in result.raw_texts:
            bridge.publish_event("raw", {"line": text})

    # ---- 写路径：chN=value → V2 调参帧；手柄键 → 文本 ----
    def on_write(text: str, line_ending: str) -> tuple[bool, str]:
        if not source.writable:
            return False, f"input source {type(source).__name__} not writable"

        # 1) 虚拟手柄
        key = normalize_joystick_key(text)
        if key is not None:
            suffix = LINE_ENDINGS.get(line_ending, b"\n")
            payload = key.encode("ascii") + suffix
            ok = source.write(payload)
            if ok:
                bridge.publish_event("joystick", {"key": key, "raw": payload.hex()})
                return True, f"sent joystick {key!r} ({payload.hex()})"
            return False, "physical write failed"

        # 2) 参数 chN=value
        try:
            if "=" in text and text.strip().lower().startswith("ch"):
                key_s, _, val = text.partition("=")
                ch = int(key_s.strip().lower().replace("ch", ""))
                value = float(val.strip())
            else:
                return False, f"unsupported write format: {text!r} (use chN=value or joystick key)"
        except (ValueError, TypeError) as exc:
            return False, f"parse error: {exc}"

        if not (1 <= ch <= 8):
            return False, f"channel {ch} out of range [1,8]"

        # 参数边界校验
        if ch in bounds:
            b = bounds[ch]
            if "min" in b and value < b["min"]:
                return False, f"value {value} below min {b['min']}"
            if "max" in b and value > b["max"]:
                return False, f"value {value} above max {b['max']}"
            if "step" in b and b["step"]:
                base = b.get("restore", 0)
                steps = round((value - base) / b["step"])
                if abs(base + steps * b["step"] - value) > 1e-6:
                    return False, f"value {value} not aligned to step {b['step']}"

        frame = build_debug_param_frame(ch, value)
        ok = source.write(frame)
        if ok:
            bridge.publish_event("write", {"channel": ch, "value": value, "frame": frame.hex()})
            return True, f"sent {frame.hex()} ({len(frame)}B) ch{ch}={value}"
        return False, "physical write failed"

    source = parse_input_url(args.input, on_bytes)
    bridge.set_write_handler(on_write)
    bridge.start()
    source.start()
    if tap is not None:
        tap.start()

    print(f"zf-ai-bridge  listening on {args.host}:{args.port}", flush=True)
    print(f"  input      = {args.input}", flush=True)
    print(f"  writable   = {source.writable}", flush=True)
    print(f"  allowWrite = {args.allow_write}", flush=True)
    print(f"  framesDir  = {args.frames_dir}   (AI 读 latest.png 看图传)", flush=True)
    if tap is not None:
        print(f"  tap        = {args.tap}   (官方逐飞助手 TCP 服务端)", flush=True)
    print("  schema     = zf.telemetry/v1   (compatible with ek.telemetry/v1)", flush=True)
    print("  client.py snapshot/watch/frame 观察；write --text chN=value 调参；Ctrl+C 退出", flush=True)

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        source.stop()
        if tap is not None:
            tap.stop()
        bridge.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
