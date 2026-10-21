#!/usr/bin/env python3
"""zf-ai-bridge 客户端（对齐 EK-OmniProbe 的 client.py 用法）。

  python client.py snapshot --port 8765 --seconds 5
  python client.py watch    --port 8765 --seconds 10
  python client.py write    --port 8765 --text "ch3=0.25"
  python client.py status   --port 8765
"""
from __future__ import annotations

import argparse
import json
import math
import os
import select
import socket
import statistics
import sys
import time
import uuid

HOST = "127.0.0.1"

# 桥内纯 Python 辅助（PNG 解码/大津法/中线/探针）的唯一 owner 是 zf_ai_bridge 包。
# 本脚本被安装到别处时（如 Agent Skill 目录）必须仍能找到它，否则
# analyze/probe/profile/hist/otsu/falsecolor 一律 ImportError。


def _ensure_bridge_path() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (here, os.path.dirname(here), os.environ.get("ZF_BRIDGE_PATH", "")):
        if cand and os.path.isdir(os.path.join(cand, "zf_ai_bridge")) and cand not in sys.path:
            sys.path.insert(0, cand)
            return


_ensure_bridge_path()


def connect(port: int):
    sock = socket.create_connection((HOST, port), timeout=3)
    stream = sock.makefile("rwb", buffering=0)
    hello = json.loads(stream.readline())
    if hello.get("type") != "hello":
        raise RuntimeError("服务端未返回 hello 握手")
    return sock, stream, hello


def read_messages(sock, stream, seconds: float):
    deadline = math.inf if seconds <= 0 else time.monotonic() + seconds
    while time.monotonic() < deadline:
        timeout = 0.5 if deadline == math.inf else min(0.5, max(0, deadline - time.monotonic()))
        if not select.select([sock], [], [], timeout)[0]:
            continue
        line = stream.readline()
        if not line:
            break
        yield json.loads(line)


def summarize(messages, duration: float) -> dict:
    values: dict[str, list[float]] = {}
    units: dict[str, str | None] = {}
    sample_rate = 0.0
    sample_count = 0
    for message in messages:
        if message.get("type") != "samples":
            continue
        sample_rate = message.get("sampleRateHz") or sample_rate
        for item in message.get("channels", []):
            units[item["key"]] = item.get("unit")
        for sample in message.get("samples", []):
            sample_count += 1
            for key, value in sample.get("values", {}).items():
                values.setdefault(key, []).append(value)

    channels = {}
    for key, series in values.items():
        channels[key] = {
            "unit": units.get(key),
            "count": len(series),
            "min": min(series),
            "max": max(series),
            "mean": statistics.fmean(series),
            "stddev": statistics.pstdev(series) if len(series) > 1 else 0.0,
            "last": series[-1],
        }
    return {
        "durationSeconds": duration,
        "sampleCount": sample_count,
        "sampleRateHz": sample_rate,
        "channels": channels,
    }


def cmd_watch(args) -> int:
    sock, stream, hello = connect(args.port)
    sock.settimeout(None)
    print(json.dumps(hello, ensure_ascii=False), flush=True)
    try:
        for message in read_messages(sock, stream, args.seconds):
            print(json.dumps(message, ensure_ascii=False), flush=True)
    finally:
        stream.close()
        sock.close()
    return 0


def cmd_snapshot(args) -> int:
    sock, stream, _ = connect(args.port)
    sock.settimeout(None)
    try:
        result = summarize(read_messages(sock, stream, args.seconds), args.seconds)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["sampleCount"] else 2
    finally:
        stream.close()
        sock.close()


def cmd_write(args) -> int:
    sock, stream, hello = connect(args.port)
    command_id = args.id or str(uuid.uuid4())
    command = {
        "type": "serial.write",
        "id": command_id,
        "text": args.text,
        "lineEnding": args.line_ending,
    }
    stream.write(json.dumps(command, ensure_ascii=False).encode() + b"\n")
    stream.flush()
    try:
        while True:
            response = json.loads(stream.readline())
            if response.get("type") == "ack" and response.get("id") == command_id:
                print(json.dumps(response, ensure_ascii=False, indent=2))
                return 0 if response.get("ok") else 1
    finally:
        stream.close()
        sock.close()


def cmd_status(args) -> int:
    sock, stream, hello = connect(args.port)
    stream.write(json.dumps({"type": "status", "id": str(uuid.uuid4())}).encode() + b"\n")
    stream.flush()
    try:
        while True:
            response = json.loads(stream.readline())
            if response.get("type") == "status":
                print(json.dumps(response, ensure_ascii=False, indent=2))
                return 0
    finally:
        stream.close()
        sock.close()


def cmd_frame(args) -> int:
    """等待/读取最近一帧图传事件，输出 PNG 路径（AI 用 Read 工具看图）。"""
    import os

    if args.path:
        latest = args.path
    else:
        latest = os.path.join(args.frames_dir, "latest.png")
    if not os.path.isfile(latest):
        print(f"错误: 未找到 {latest}（桥是否在收图传？）", file=sys.stderr)
        return 2

    sock, stream, _ = connect(args.port)
    sock.settimeout(0.2)
    meta = None
    deadline = time.monotonic() + max(0.1, args.seconds)
    buf = b""
    try:
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = json.loads(line)
                if msg.get("type") == "event" and msg.get("event") == "frame":
                    meta = msg
                    deadline = 0  # 拿到就停
                    break
    finally:
        stream.close()
        sock.close()

    result = {"latestPath": latest, "exists": True}
    if meta:
        result.update(meta)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_joystick(args) -> int:
    """发送虚拟手柄键（W/A/S/D 或 Up/Down/Left/Right）。"""
    key = args.key
    sock, stream, hello = connect(args.port)
    command_id = str(uuid.uuid4())
    stream.write(json.dumps({
        "type": "serial.write",
        "id": command_id,
        "text": key,
        "lineEnding": args.line_ending,
    }).encode() + b"\n")
    stream.flush()
    try:
        while True:
            response = json.loads(stream.readline())
            if response.get("type") == "ack" and response.get("id") == command_id:
                print(json.dumps(response, ensure_ascii=False, indent=2))
                return 0 if response.get("ok") else 1
    finally:
        stream.close()
        sock.close()


def cmd_analyze(args) -> int:
    """分析 frames/latest.png：提取边线/中线，输出统计 + 可选标注图。"""
    import os
    import sys as _sys

    root = os.path.dirname(os.path.abspath(__file__))
    pkg_parent = root
    if os.path.isdir(os.path.join(root, "zf_ai_bridge")):
        _sys.path.insert(0, root)
    else:
        up = os.path.dirname(root)
        if os.path.isdir(os.path.join(up, "zf_ai_bridge")):
            _sys.path.insert(0, up)

    from zf_ai_bridge.extras import annotate_track, extract_track, load_png_rgb
    from zf_ai_bridge.image import write_png

    src = args.path or os.path.join(args.frames_dir, "latest.png")
    if not os.path.isfile(src):
        print(f"错误: 未找到 {src}", file=sys.stderr)
        return 2

    rgb, width, height = load_png_rgb(src)
    track = extract_track(rgb, width, height, threshold=args.threshold)

    if args.out:
        buf = bytearray(rgb)
        annotate_track(buf, width, height, track)
        write_png(args.out, width, height, bytes(buf))

    summary = {
        "source": src,
        "width": width,
        "height": height,
        "confidence": track["confidence"],
        "meanWidth": track["meanWidth"],
        "threshold": track["threshold"],
        "rowCount": len(track["rows"]),
        "midlineHead": track["midline"][:5] if track["midline"] else [],
        "midlineTail": track["midline"][-5:] if track["midline"] else [],
    }
    if args.out:
        summary["annotatedPath"] = args.out
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if track["confidence"] > 0.2 else 3


def _load_gray(args):
    import os
    import sys as _sys

    root = os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(os.path.join(root, "zf_ai_bridge")):
        _sys.path.insert(0, root)
    else:
        up = os.path.dirname(root)
        if os.path.isdir(os.path.join(up, "zf_ai_bridge")):
            _sys.path.insert(0, up)

    from zf_ai_bridge.extras import load_png_rgb, rgb_to_gray

    src = args.path or os.path.join(args.frames_dir, "latest.png")
    if not os.path.isfile(src):
        print(f"错误: 未找到 {src}", file=sys.stderr)
        sys.exit(2)
    rgb, width, height = load_png_rgb(src)
    gray = rgb_to_gray(rgb, width, height)
    return src, gray, width, height


def cmd_probe(args) -> int:
    """取单点/区域像素值（黑0 白255）。"""
    from zf_ai_bridge.extras import pixel_at, pixel_grid, format_grid_ascii

    src, gray, width, height = _load_gray(args)
    result = {"source": src, "width": width, "height": height}

    if args.grid:
        g = pixel_grid(gray, width, height, args.x, args.y, radius=args.radius)
        result.update(g)
        result["ascii"] = format_grid_ascii(g["grid"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print()
        print(result["ascii"])
    else:
        v = pixel_at(gray, width, height, args.x, args.y)
        result.update({"x": args.x, "y": args.y, "value": v})
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_profile(args) -> int:
    """行列亮度剖面。"""
    from zf_ai_bridge.extras import col_profile, row_profile

    src, gray, width, height = _load_gray(args)
    if args.row is not None:
        vals = row_profile(gray, width, height, args.row)
        kind, idx = "row", args.row
    else:
        vals = col_profile(gray, width, height, args.col)
        kind, idx = "col", args.col

    # 压缩输出：每 4 个点取 max，避免太长
    step = max(1, len(vals) // 48)
    spark = [max(vals[i:i + step]) for i in range(0, len(vals), step)]

    out = {
        "source": src, "kind": kind, "index": idx,
        "min": min(vals), "max": max(vals),
        "mean": round(sum(vals) / len(vals), 2),
        "downsampled": spark,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_hist(args) -> int:
    """灰度直方图。"""
    from zf_ai_bridge.extras import histogram

    src, gray, width, height = _load_gray(args)
    h = histogram(gray, bins=args.bins)
    h["source"] = src
    h["width"] = width
    h["height"] = height
    print(json.dumps(h, ensure_ascii=False, indent=2))
    # 简易文本图
    if h["counts"]:
        mx = max(h["counts"]) or 1
        for i, c in enumerate(h["counts"]):
            bar = "#" * max(1, int(c * 30 / mx)) if c else ""
            lo = i * 256 // args.bins
            print(f"{lo:3d}-{lo + 256 // args.bins - 1:3d} | {bar} {c}")
    return 0


def cmd_otsu(args) -> int:
    """大津法阈值 + 可选二值化输出。"""
    from zf_ai_bridge.extras import otsu_threshold
    from zf_ai_bridge.image import write_png

    src, gray, width, height = _load_gray(args)
    t = args.threshold if args.threshold is not None else otsu_threshold(gray)
    above = sum(1 for v in gray if v >= t)
    out = {
        "source": src, "threshold": t, "auto": args.threshold is None,
        "width": width, "height": height,
        "above": above, "below": len(gray) - above,
        "aboveRatio": round(above / (len(gray) or 1), 4),
    }
    if args.out:
        bin_rgb = bytearray(width * height * 3)
        for i, v in enumerate(gray):
            g = 255 if v >= t else 0
            bin_rgb[i * 3] = bin_rgb[i * 3 + 1] = bin_rgb[i * 3 + 2] = g
        write_png(args.out, width, height, bytes(bin_rgb))
        out["output"] = args.out
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_falsecolor(args) -> int:
    """生成伪彩色图（蓝→绿→红），便于肉眼看灰度分布。"""
    import os
    import sys as _sys

    root = os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(os.path.join(root, "zf_ai_bridge")):
        _sys.path.insert(0, root)
    else:
        up = os.path.dirname(root)
        if os.path.isdir(os.path.join(up, "zf_ai_bridge")):
            _sys.path.insert(0, up)
    from zf_ai_bridge.extras import false_color_rgb
    from zf_ai_bridge.image import write_png

    src, gray, width, height = _load_gray(args)
    out = args.out or os.path.join(os.path.dirname(src), "false_color.png")
    write_png(out, width, height, false_color_rgb(gray, width, height))
    print(json.dumps({"source": src, "output": out}, ensure_ascii=False, indent=2))
    return 0


def cmd_self_test(_args) -> int:
    result = summarize(
        [{"type": "samples", "sampleRateHz": 10, "channels": [{"key": "x", "unit": ""}],
          "samples": [{"values": {"x": 1}}, {"values": {"x": 3}}]}],
        1,
    )
    assert result["sampleCount"] == 2
    assert result["channels"]["x"]["mean"] == 2
    # 分析类命令依赖桥内纯 Python 辅助；缺包时在这里就失败，而不是等用户跑 analyze 才炸。
    from zf_ai_bridge.extras import extract_track, load_png_rgb, otsu_threshold
    from zf_ai_bridge.image import write_png

    for fn in (extract_track, load_png_rgb, otsu_threshold, write_png):
        assert callable(fn), fn
    print("ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="zf-ai-bridge 客户端")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("watch", help="持续输出 NDJSON 数据流")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--seconds", type=float, default=0, help="0 表示持续运行")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("snapshot", help="采集时间窗并输出统计摘要")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--seconds", type=float, default=5)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("write", help="发送一条受控写命令")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--text", required=True)
    p.add_argument("--line-ending", choices=("none", "lf", "crlf", "cr"), default="lf")
    p.add_argument("--id")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("status", help="查看桥状态")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("frame", help="获取最近一帧图传 PNG 路径")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--frames-dir", default="frames")
    p.add_argument("--path", default="", help="直接指定 latest.png 路径")
    p.add_argument("--seconds", type=float, default=0.5, help="等待新帧的秒数")
    p.set_defaults(func=cmd_frame)

    p = sub.add_parser("joystick", help="发送虚拟手柄键")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("key", help="W/A/S/D 或 Up/Down/Left/Right")
    p.add_argument("--line-ending", choices=("none", "lf", "crlf", "cr"), default="lf")
    p.set_defaults(func=cmd_joystick)

    p = sub.add_parser("analyze", help="分析图传：提取边线/中线")
    p.add_argument("--frames-dir", default="frames")
    p.add_argument("--path", default="", help="指定 PNG（默认 frames/latest.png）")
    p.add_argument("--threshold", type=int, default=100, help="亮度阈值 0-255")
    p.add_argument("--out", default="", help="保存标注图路径")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("probe", help="像素探针：取点/区域灰度值（黑0 白255）")
    p.add_argument("--frames-dir", default="frames")
    p.add_argument("--path", default="", help="指定 PNG")
    p.add_argument("--x", type=int, required=True)
    p.add_argument("--y", type=int, required=True)
    p.add_argument("--grid", action="store_true", help="显示周边网格")
    p.add_argument("--radius", type=int, default=4, help="网格半径（默认 4 → 9x9）")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("profile", help="行列亮度剖面")
    p.add_argument("--frames-dir", default="frames")
    p.add_argument("--path", default="", help="指定 PNG")
    p.add_argument("--row", type=int, default=None, help="取某一行")
    p.add_argument("--col", type=int, default=None, help="取某一列")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("hist", help="灰度直方图")
    p.add_argument("--frames-dir", default="frames")
    p.add_argument("--path", default="", help="指定 PNG")
    p.add_argument("--bins", type=int, default=16)
    p.set_defaults(func=cmd_hist)

    p = sub.add_parser("otsu", help="大津法阈值（可手动覆盖）")
    p.add_argument("--frames-dir", default="frames")
    p.add_argument("--path", default="", help="指定 PNG")
    p.add_argument("--threshold", type=int, default=None, help="手动阈值（不给则自动大津）")
    p.add_argument("--out", default="", help="保存二值化 PNG")
    p.set_defaults(func=cmd_otsu)

    p = sub.add_parser("falsecolor", help="生成伪彩色图（蓝绿红）")
    p.add_argument("--frames-dir", default="frames")
    p.add_argument("--path", default="", help="指定 PNG")
    p.add_argument("--out", default="", help="输出路径")
    p.set_defaults(func=cmd_falsecolor)

    p = sub.add_parser("self-test", help="离线自检")
    p.set_defaults(func=cmd_self_test)

    args = parser.parse_args()
    try:
        return args.func(args) or 0
    except (ConnectionError, OSError, RuntimeError, json.JSONDecodeError) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
