"""zf-ai-bridge 图形界面 —— 一键启动，无需终端。

  python app.py

功能：
  · 选择输入源（WiFi 监听 / 串口 / 演示）并一键启停桥
  · 实时显示：连接状态、样本数、最新图传预览
  · 「允许 AI 写」开关（默认关）
  · 快捷按钮：snapshot / 打开逐飞助手 / 打开 frames 目录
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
SETTINGS_PATH = ROOT / "settings.json"

from zf_ai_bridge.bridge import AiBridge, BridgeConfig  # noqa: E402
from zf_ai_bridge.extras import (  # noqa: E402
    false_color_rgb,
    format_grid_ascii,
    load_png_rgb,
    pixel_grid,
    rgb_to_gray,
)
from zf_ai_bridge.image import FrameSink, write_png, rgb565_to_rgb888  # noqa: E402
from zf_ai_bridge.inputs import parse_input_url  # noqa: E402
from zf_ai_bridge.protocol import (  # noqa: E402
    CameraFrame,
    DebugParamFrame,
    OscilloscopeFrame,
    ProtocolParser,
    build_debug_param_frame,
)
from zf_ai_bridge.tap import TapForwarder  # noqa: E402
from zf_ai_bridge.matrix_view import open_matrix_window, otsu_threshold  # noqa: E402
from zf_ai_bridge.perspective_view import PerspectiveWindow  # noqa: E402

# 找官方逐飞助手
ZF_EXE_CANDIDATES = [
    ROOT.parent / "seekfree-assistant" / "【软件】逐飞助手上位机" / "逐飞助手V2.0.0.11 beta.exe",
    ROOT.parent / "【软件】逐飞助手上位机" / "逐飞助手V2.0.0.11 beta.exe",
]


class BridgeApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("zf-ai-bridge · 逐飞助手 AI 上位机")
        root.geometry("920x620")
        root.minsize(780, 520)

        self.bridge: AiBridge | None = None
        self.source = None
        self.tap: TapForwarder | None = None
        self.proto = ProtocolParser()
        self.frame_sink = FrameSink(ROOT / "frames", save_every=5)
        self.ui_queue: queue.Queue = queue.Queue()
        self.sample_count = 0
        self.last_channels: dict[str, float] = {}
        self.running = False
        # 分析目标：live=实时图传 uploaded=上传/选帧
        self.mode = "live"
        self.work_path: Path = ROOT / "frames" / "latest.png"

        self._build_ui()
        self._load_settings()
        self.root.after(33, self._poll_ui)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_settings(self) -> None:
        import json
        try:
            if SETTINGS_PATH.is_file():
                cfg = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            else:
                cfg = {}
        except Exception:  # noqa: BLE001
            cfg = {}
        if cfg.get("input"):
            self.input_var.set(cfg["input"])
        if "allow_write" in cfg:
            self.write_var.set(bool(cfg["allow_write"]))
        if "tap" in cfg:
            self.tap_var.set(bool(cfg["tap"]))
        if cfg.get("tap_addr"):
            self.tap_addr_var.set(cfg["tap_addr"])
        # 下拉历史
        hist = cfg.get("history") or []
        values = list(self.input_combo["values"])
        for h in hist:
            if h and h not in values:
                values.insert(0, h)
        self.input_combo.configure(values=values[:12])
        if cfg:
            self._log(f"[配置] 已加载 {SETTINGS_PATH.name}")

    def _save_settings(self) -> None:
        import json
        hist = list(self.input_combo.get() and [self.input_combo.get()] or [])
        # 合并下拉里的历史
        for v in self.input_combo["values"]:
            if v not in hist:
                hist.append(v)
        # 当前输入源放到最前
        cur = self.input_var.get().strip()
        if cur and cur in hist:
            hist.remove(cur)
        if cur:
            hist.insert(0, cur)
        cfg = {
            "input": self.input_var.get().strip(),
            "allow_write": bool(self.write_var.get()),
            "tap": bool(self.tap_var.get()),
            "tap_addr": self.tap_addr_var.get().strip(),
            "history": hist[:12],
        }
        try:
            SETTINGS_PATH.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"[配置] 保存失败: {exc}")

    def _on_close(self) -> None:
        if self._rec is not None:  # 关窗口前收尾，避免录出半截 AVI
            self._rec.stop()
            self._rec = None
        try:
            self._save_settings()
        except Exception:  # noqa: BLE001
            pass
        if self.running:
            self._stop_bridge()
        self.root.destroy()

    # ---- UI ----

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # 顶部：输入源 + 启停
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="输入源").pack(side="left")
        self.input_var = tk.StringVar(value="tcp-listen://0.0.0.0:8081")
        self.input_combo = ttk.Combobox(
            top, textvariable=self.input_var, width=32,
            values=[
                "tcp-listen://0.0.0.0:8081",
                "tcp-listen://0.0.0.0:8080",
                "tcp-listen://0.0.0.0:8765",
                "tcp://192.168.4.1:8086",
                "serial://COM5:115200",
                "demo://",
            ],
        )
        self.input_combo.pack(side="left", padx=6)

        self.start_btn = ttk.Button(top, text="▶ 启动桥", command=self.toggle_bridge)
        self.start_btn.pack(side="left", padx=6)

        self.write_var = tk.BooleanVar(value=False)
        self.write_chk = ttk.Checkbutton(
            top, text="允许 AI 写设备", variable=self.write_var,
            command=self._on_write_toggle,
        )
        self.write_chk.pack(side="left", padx=12)

        # 旁路到官方逐飞助手
        self.tap_var = tk.BooleanVar(value=True)
        self.tap_chk = ttk.Checkbutton(top, text="旁路给逐飞助手", variable=self.tap_var)
        self.tap_chk.pack(side="left", padx=4)
        self.tap_addr_var = tk.StringVar(value="127.0.0.1:8086")
        ttk.Entry(top, textvariable=self.tap_addr_var, width=14).pack(side="left", padx=2)

        # 状态栏
        status = ttk.LabelFrame(self.root, text="状态")
        status.pack(fill="x", **pad)
        self.status_var = tk.StringVar(value="未启动")
        ttk.Label(status, textvariable=self.status_var, foreground="#333").pack(
            anchor="w", padx=8, pady=4
        )

        # 中部：左图传预览 / 右数据
        mid = ttk.Panedwindow(self.root, orient="horizontal")
        mid.pack(fill="both", expand=True, **pad)

        left = ttk.LabelFrame(mid, text="图传预览（点击取像素；黑0 白255）")
        mid.add(left, weight=3)
        self.preview = tk.Canvas(left, background="#1a1a1a", highlightthickness=0)
        self.preview.pack(fill="both", expand=True, padx=6, pady=6)
        self._photo = None
        self._zoom = 1
        self._canvas_img = None
        self._scrollregion = None
        self._rec = None
        self._rec_path = None
        self._rec_fps = 15
        self.preview.bind("<Button-1>", self._on_preview_click)
        self.preview.bind("<MouseWheel>", self._on_preview_wheel)
        self.preview.bind("<ButtonPress-3>", self._on_pan_start)
        self.preview.bind("<B3-Motion>", self._on_pan_move)

        zoom_bar = ttk.Frame(left)
        zoom_bar.pack(fill="x", padx=6, pady=2)
        ttk.Button(zoom_bar, text="放大 +", width=7, command=lambda: self._zoom_by(1)).pack(side="left", padx=2)
        ttk.Button(zoom_bar, text="缩小 -", width=7, command=lambda: self._zoom_by(-1)).pack(side="left", padx=2)
        ttk.Button(zoom_bar, text="1:1", width=5, command=lambda: self._zoom_by(0, absolute=1)).pack(side="left", padx=2)
        ttk.Button(zoom_bar, text="复位", width=5, command=self._reset_view).pack(side="left", padx=2)
        self.zoom_var = tk.StringVar(value="x1  ·  滚轮缩放 / 右键拖动")
        ttk.Label(zoom_bar, textvariable=self.zoom_var).pack(side="left", padx=6)
        self.probe_var = tk.StringVar(value="点击图片查看像素值")
        ttk.Label(left, textvariable=self.probe_var, font=("Consolas", 9)).pack(
            anchor="w", padx=6, pady=2
        )
        self.false_var = tk.BooleanVar(value=False)
        self.edge_var = tk.BooleanVar(value=False)
        self.center_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            left, text="显示边线（左紫/右青）", variable=self.edge_var,
            command=self._on_overlay_toggle,
        ).pack(anchor="w", padx=6, pady=2)
        ttk.Checkbutton(
            left, text="显示中线（路径）", variable=self.center_var,
            command=self._on_overlay_toggle,
        ).pack(anchor="w", padx=6, pady=2)
        ttk.Button(left, text="逆透视实验 · 冻结原图选四角", command=self._open_perspective).pack(anchor="w", padx=6, pady=2)
        ttk.Checkbutton(
            left, text="伪彩色（蓝→绿→红）", variable=self.false_var,
            command=self._refresh_preview,
        ).pack(anchor="w", padx=6, pady=2)

        right = ttk.LabelFrame(mid, text="波形 / 事件")
        mid.add(right, weight=2)
        self.data_text = tk.Text(right, height=16, wrap="none", font=("Consolas", 10))
        self.data_text.pack(fill="both", expand=True, padx=6, pady=6)

        # 底部按钮
        bottom = ttk.Frame(self.root)
        bottom.pack(fill="x", **pad)
        ttk.Button(bottom, text="刷新预览", command=self._refresh_preview).pack(side="left", padx=4)
        self.rec_btn = ttk.Button(bottom, text="● 开始录制", command=self._toggle_record)
        self.rec_btn.pack(side="left", padx=4)
        ttk.Button(bottom, text="像素矩阵", command=self._open_matrix).pack(side="left", padx=4)
        ttk.Button(bottom, text="上传图片", command=self._upload_image).pack(side="left", padx=4)
        ttk.Button(bottom, text="选择帧", command=self._pick_frame).pack(side="left", padx=4)
        ttk.Button(bottom, text="回到实时", command=self._back_to_live).pack(side="left", padx=4)
        ttk.Button(bottom, text="snapshot 摘要", command=self._do_snapshot).pack(side="left", padx=4)
        ttk.Button(bottom, text="打开 frames 目录", command=self._open_frames).pack(side="left", padx=4)
        ttk.Button(bottom, text="打开逐飞助手", command=self._open_zf).pack(side="left", padx=4)
        ttk.Button(bottom, text="清空日志", command=lambda: self.data_text.delete("1.0", "end")).pack(
            side="right", padx=4
        )

        self._log("就绪。选择输入源后点「启动桥」。")
        self._log("设备连接：板子 WiFi 连热点后，模块主动连本机 IP:端口。")
        self._log("你的工程默认：tcp-listen://0.0.0.0:8081（板子 PC_PORT=8081）")
        self._log("Windows 热点场景 PC_IP 常为 192.168.137.1")

    # ---- 桥控制 ----

    def toggle_bridge(self) -> None:
        if self.running:
            self._stop_bridge()
        else:
            self._start_bridge()

    def _start_bridge(self) -> None:
        url = self.input_var.get().strip()
        try:
            self.proto = ProtocolParser()
            self.bridge = AiBridge(
                BridgeConfig(port=8765, allow_write=self.write_var.get())
            )
            self.bridge.set_write_handler(self._on_write)
            self.bridge.start()
            self.source = parse_input_url(url, self._on_bytes)
            self.source.start()
            if self.tap_var.get() and self.tap_addr_var.get().strip():
                self.tap = TapForwarder(self.tap_addr_var.get().strip())
                self.tap.start()
            else:
                self.tap = None
        except Exception as exc:  # noqa: BLE001
            self._log(f"[错误] 启动失败: {exc}")
            return
        self.running = True
        self.start_btn.configure(text="■ 停止桥")
        self._save_settings()
        self._log(f"[启动] 输入 {url}")
        self._log("[启动] AI 遥测桥 127.0.0.1:8765 (zf.telemetry/v1)")
        if self.tap is not None:
            self._log(f"[旁路] 转发到逐飞助手 {self.tap_addr_var.get()}")
            self._log("[旁路] 逐飞助手请开 TCP 服务端（本地端口 8086）")
        self._refresh_preview()

    def _stop_bridge(self) -> None:
        if self.source:
            self.source.stop()
        if self.tap:
            self.tap.stop()
        if self.bridge:
            self.bridge.stop()
        self.proto = ProtocolParser()
        self.frame_sink.clear_overlays()
        self.running = False
        self.start_btn.configure(text="▶ 启动桥")
        st = self.frame_sink.stats()
        self.status_var.set(
            f"已停止 · frames={st['frames']} estFps={st['estFps']} maxGap={st['maxGapSeconds']}s"
        )
        self._log(
            f"[停止] frames={st['frames']} estFps={st['estFps']} "
            f"maxGap={st['maxGapSeconds']}s lastType={st['lastCameraType']}"
        )

    def _on_write_toggle(self) -> None:
        if self.bridge:
            self.bridge.set_allow_write(self.write_var.get())
        self._log(f"[权限] 允许 AI 写 = {self.write_var.get()}")

    def _on_write(self, text: str, line_ending: str) -> tuple[bool, str]:
        # 手柄
        from zf_ai_bridge.extras import normalize_joystick_key

        key = normalize_joystick_key(text)
        if key is not None:
            suffix = {"none": b"", "lf": b"\n", "crlf": b"\r\n", "cr": b"\r"}.get(
                line_ending, b"\n"
            )
            payload = key.encode("ascii") + suffix
            ok = self.source.writable and self.source.write(payload)
            return (True, f"joystick {key!r}") if ok else (False, "write failed")

        # 参数 chN=value
        try:
            k, _, v = text.partition("=")
            ch = int(k.strip().lower().replace("ch", ""))
            value = float(v.strip())
            frame = build_debug_param_frame(ch, value)
            ok = self.source.writable and self.source.write(frame)
            return (True, f"sent {frame.hex()} ch{ch}={value}") if ok else (False, "write failed")
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    # ---- 数据回调 ----

    def _on_bytes(self, data: bytes) -> None:
        if self.tap is not None:
            self.tap.forward(data)
        result = self.proto.feed(data)
        for frame in result.frames:
            if isinstance(frame, OscilloscopeFrame):
                self.sample_count += 1
                self.last_channels = {
                    f"ch{i+1}": v for i, v in enumerate(frame.values)
                }
                if self.bridge:
                    chans = [{"key": f"ch{i+1}", "unit": ""} for i in range(frame.channel_max)]
                    samples = [{"values": dict(self.last_channels)}]
                    self.bridge.publish_samples(chans, samples)
                if self.sample_count % 20 == 0:
                    self.ui_queue.put(("ch", dict(self.last_channels)))
            elif isinstance(frame, CameraFrame):
                meta = self.frame_sink.on_camera(
                    frame.camera_type, frame.width, frame.height, frame.payload
                )
                if meta and self.bridge:
                    self.bridge.publish_event("frame", meta)
                self.ui_queue.put(("frame", meta or {}))
            elif isinstance(frame, DebugParamFrame) and self.bridge:
                self.bridge.publish_event(
                    "param", {"channel": frame.channel_index, "value": frame.value}
                )
            elif isinstance(frame, dict):
                # 边线/矩形：交给 FrameSink，下一帧图像上叠加（联测反馈 #1）
                kind = frame.get("kind")
                if kind == "boundary":
                    color = rgb565_to_rgb888(frame.get("color", 0xF800))
                    pts = frame.get("points") or []
                    self.frame_sink.add_boundary([(int(x), int(y)) for x, y in pts], color)
                    if self.bridge:
                        self.bridge.publish_event("boundary", {
                            "length": frame.get("length"),
                            "color": frame.get("color"),
                            "points": len(pts),
                        })
                elif kind == "rect":
                    color = rgb565_to_rgb888(frame.get("color", 0x07E0))
                    self.frame_sink.add_rect(
                        int(frame.get("x", 0)), int(frame.get("y", 0)),
                        int(frame.get("w", 0)), int(frame.get("h", 0)), color,
                    )
                    if self.bridge:
                        self.bridge.publish_event("rect", frame)
        for text in result.raw_texts:
            if self.bridge:
                self.bridge.publish_event("raw", {"line": text})
            self.ui_queue.put(("log", text.strip()))

    # ---- UI 轮询 ----

    def _poll_ui(self) -> None:
        pending = False
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "ch":
                    parts = [f"{k}={v:.4g}" for k, v in payload.items()]
                    self.status_var.set(
                        f"运行中 · 样本 {self.sample_count} · " + "  ".join(parts)
                    )
                elif kind == "frame":
                    # 上传/选帧模式下不被实时帧刷掉；本轮多帧只渲染最新一次
                    if self.mode == "live":
                        pending = True
                    if payload and self.mode == "live":
                        # 节流日志，避免刷屏
                        if self.sample_count % 20 == 0:
                            self._log(
                                f"[图传] {payload.get('width')}x{payload.get('height')} "
                                f"→ latest.png"
                            )
                elif kind == "log":
                    if payload:
                        self._log(f"[串口] {payload}")
        except queue.Empty:
            pass
        if pending:
            self._refresh_preview()
        self.root.after(33, self._poll_ui)

    def _refresh_preview(self) -> None:
        if self.mode == "live":
            latest = ROOT / "frames" / "latest.png"
            self.work_path = latest
        else:
            latest = self.work_path
        # 实时预览直接用内存快照，不读盘：无 PNG 解码、无读写竞争
        frame = self.frame_sink.last_frame
        use_mem = self.mode == "live" and frame is not None
        if not latest.is_file() and not use_mem:
            self._preview_message("暂无图传")
            return
        try:
            if use_mem:
                _, w, h, raw, annotated = frame
                rgb = annotated if (self.edge_var.get() or self.center_var.get()) else raw
            else:
                rgb, w, h = load_png_rgb(latest)
                raw = rgb
            self._display_raw = (w, h, raw)
            if self.false_var.get():
                rgb = false_color_rgb(rgb_to_gray(rgb, w, h), w, h)
            img = tk.PhotoImage(data=b"P6\n%d %d\n255\n" % (w, h) + rgb, format="ppm")
            self._src_size = (w, h)
            zoom = max(1, int(getattr(self, "_zoom", 1)))
            span = max(w / 640.0, h / 360.0, 1.0)
            if zoom > 1:
                img = img.zoom(zoom)
                self._scale = float(zoom)
            elif span > 1:
                k = int(span)
                img = img.subsample(k)
                self._scale = 1.0 / k
            else:
                self._scale = 1.0
            canvas = self.preview
            # 复用同一个画布项：重建会把视图拉回左上角，导致放大后无法拖动
            if self._canvas_img is None:
                self._canvas_img = canvas.create_image(0, 0, anchor="nw", image=img)
            else:
                canvas.itemconfigure(self._canvas_img, image=img)
            sr = (img.width(), img.height())
            if self._scrollregion != sr:
                canvas.configure(scrollregion=(0, 0, sr[0], sr[1]))
                self._scrollregion = sr
            self._photo = img  # 防 GC
            self.zoom_var.set(f"x{zoom}  ·  滚轮缩放 / 右键拖动")
        except Exception as exc:  # noqa: BLE001
            # 快照可能正在写入：保留上一帧，避免画面闪烁
            if self._photo is not None:
                return
            self._preview_message(f"预览失败: {exc}")

    def _preview_message(self, text: str) -> None:
        c = self.preview
        c.delete("all")
        c.configure(scrollregion=(0, 0, 1, 1))
        c.create_text(10, 10, anchor="nw", fill="#cccccc", text=text)
        self._photo = None
        self._canvas_img = None
        self._scrollregion = None

    def _zoom_by(self, delta: int, absolute: int | None = None) -> None:
        """整数倍缩放（Tk 只支持整数放大/抽样），尽量保持视口中心不跳。"""
        cur = int(getattr(self, "_zoom", 1))
        target = absolute if absolute is not None else cur + delta
        target = max(1, min(16, int(target)))
        if target == cur and absolute is None:
            return
        c = self.preview
        vw = max(1, c.winfo_width())
        vh = max(1, c.winfo_height())
        ccx = c.canvasx(vw / 2) / max(cur, 1)
        ccy = c.canvasy(vh / 2) / max(cur, 1)
        self._zoom = target
        self._refresh_preview()
        w, h = getattr(self, "_src_size", (0, 0))
        if w and h:
            c.xview_moveto(max(0.0, min(1.0, (ccx * target - vw / 2) / max(1, w * target))))
            c.yview_moveto(max(0.0, min(1.0, (ccy * target - vh / 2) / max(1, h * target))))

    def _reset_view(self) -> None:
        self._zoom = 1
        self._refresh_preview()
        self.preview.xview_moveto(0)
        self.preview.yview_moveto(0)

    def _on_preview_wheel(self, event) -> None:
        self._zoom_by(1 if getattr(event, "delta", 0) > 0 else -1)

    def _on_pan_start(self, event) -> None:
        self.preview.scan_mark(event.x, event.y)

    def _on_pan_move(self, event) -> None:
        self.preview.scan_dragto(event.x, event.y, gain=1)

    def _on_preview_click(self, event) -> None:
        snapshot = getattr(self, "_display_raw", None)
        if snapshot is None:
            self.probe_var.set("无图像")
            return
        try:
            w, h, rgb = snapshot
            gray = rgb_to_gray(rgb, w, h)
            # Canvas 坐标已含缩放与滚动，除以缩放倍数即源像素
            scale = getattr(self, "_scale", 1.)
            px = int(self.preview.canvasx(event.x) / scale)
            py = int(self.preview.canvasy(event.y) / scale)
            if not (0 <= px < w and 0 <= py < h):
                self.probe_var.set(f"点到图外 ({px},{py})")
                return
            g = pixel_grid(gray, w, h, px, py, radius=3)
            v = gray[py * w + px]
            self.probe_var.set(
                f"({px},{py}) = {v}   周边 min={g['min']} max={g['max']} mean={g['mean']}"
            )
            # 同步到日志
            self._log(f"[probe] ({px},{py}) = {v}")
            self._log(format_grid_ascii(g["grid"]))
        except Exception as exc:  # noqa: BLE001
            self.probe_var.set(f"探针失败: {exc}")

    def _do_snapshot(self) -> None:
        if self.last_channels:
            parts = [f"  {k}: {v:.6g}" for k, v in self.last_channels.items()]
            self._log(f"[snapshot] 样本 {self.sample_count}\n" + "\n".join(parts))
        else:
            self._log("[snapshot] 暂无数据")

    def _on_overlay_toggle(self) -> None:
        self.frame_sink.show_edges = self.edge_var.get()
        self.frame_sink.show_center = self.center_var.get()
        self.frame_sink.save_annotated = self.edge_var.get() or self.center_var.get()
        self._refresh_preview()
        self._refresh_preview()

    def _open_perspective(self) -> None:
        try:
            frame = self.frame_sink.last_frame if self.mode == "live" else None
            if frame:
                camera_type, w, h, rgb, _ = frame
            elif self.mode != "live":
                rgb, w, h = load_png_rgb(self.work_path)
                camera_type = 0
            else:
                self._log("[逆透视] 等待新图传，或先上传一张干净PNG")
                return
            PerspectiveWindow(self.root, rgb, w, h, camera_type, ROOT / "frames")
        except (OSError, ValueError) as exc:
            self._log(f"[逆透视] 无法打开: {exc}")

    def _open_matrix(self) -> None:
        src = self.work_path
        if self.mode == "live":
            src = ROOT / "frames" / "latest.png"
            self.work_path = src
        if not src.is_file():
            self._log("[矩阵] 暂无图像。请先「上传图片」或等待图传/「选择帧」")
            return
        open_matrix_window(self.root, src, title=f"像素矩阵 · {src.name}")
        self._log(f"[矩阵] 打开 {src.name}（滚轮缩放；二值化+大津）")

    def _set_mode(self, mode: str, path: Path, label: str) -> None:
        self.mode = mode
        self.work_path = path
        self._refresh_preview()
        self._log(f"[模式] {label}")
        open_matrix_window(self.root, path, title=label)

    def _upload_image(self) -> None:
        """上传本地照片 → 独立工作图，不被实时图传覆盖。"""
        from tkinter import filedialog
        import shutil

        path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[
                ("图片", "*.png *.jpg *.jpeg *.bmp *.gif"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            src = Path(path)
            dst = ROOT / "frames" / "uploaded.png"
            dst.parent.mkdir(exist_ok=True)
            if src.suffix.lower() in (".jpg", ".jpeg", ".bmp", ".gif"):
                from PIL import Image
                im = Image.open(src).convert("RGB")
                im.save(dst)
            else:
                shutil.copy(src, dst)
            self._set_mode("uploaded", dst, f"上传 · {src.name}")
            self._log(f"[上传] {src.name} → frames/uploaded.png（实时帧不会覆盖此图）")
        except Exception as exc:  # noqa: BLE001
            self._log(f"[上传] 失败: {exc}")

    def _toggle_record(self) -> None:
        """录制预览帧到未压缩 AVI（纯标准库，无需 ffmpeg）。"""
        import time as _time

        if self._rec is not None:
            frames, size = self._rec.stop()
            self._rec = None
            self._log(f"[录制] 结束：{frames} 帧，{size / 1048576:.1f} MB → {self._rec_path.name}")
            self.rec_btn.configure(text="● 开始录制")
            return
        frame = self.frame_sink.last_frame
        if frame is None:
            self._log("[录制] 还没有画面：先启动桥并等出图")
            return
        _, w, h, rgb, _ = frame
        from zf_ai_bridge.recorder import AviWriter

        ts = _time.strftime("%Y%m%d_%H%M%S")
        self._rec_path = ROOT / "frames" / f"rec_{ts}.avi"
        self._rec = AviWriter(self._rec_path, w, h, fps=self._rec_fps)
        self.rec_btn.configure(text="■ 停止录制")
        self._log(f"[录制] 开始 {w}x{h} @{self._rec_fps}fps → {self._rec_path.name}")
        self.root.after(max(1, 1000 // self._rec_fps), self._rec_tick)

    def _rec_tick(self) -> None:
        if self._rec is None:
            return
        frame = self.frame_sink.last_frame
        if frame:
            self._rec.add_rgb(frame[3])
        self.root.after(max(1, 1000 // self._rec_fps), self._rec_tick)

    def _pick_frame(self) -> None:
        """历史帧缩略图选择：看到画面再点，点哪张就锁定哪张。"""
        import shutil

        frames_dir = ROOT / "frames"
        files = sorted(frames_dir.glob("frame_*.png"), reverse=True)[:24]
        if not files:
            self._log("[选帧] frames/ 里没有历史帧")
            return
        win = tk.Toplevel(self.root)
        win.title("选择一帧（点缩略图）")
        win.geometry("780x560")
        ttk.Label(
            win, text=f"最近 {len(files)} 帧 · 点图即锁定分析（复制为 uploaded.png）"
        ).pack(anchor="w", padx=8, pady=6)
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        keep: list[tk.PhotoImage] = []

        def use(f) -> None:
            dst = frames_dir / "uploaded.png"
            shutil.copy(f, dst)
            self._set_mode("uploaded", dst, f"帧 · {f.name}")
            self._log(f"[选帧] {f.name} → uploaded.png（锁定分析，实时不覆盖）")
            win.destroy()

        for i, f in enumerate(files):
            try:
                img = tk.PhotoImage(file=str(f))
                if img.width() > 110:
                    img = img.subsample(2)
            except Exception:  # noqa: BLE001
                continue
            keep.append(img)
            cell = ttk.Frame(body)
            cell.grid(row=i // 4, column=i % 4, padx=6, pady=6)
            ttk.Button(cell, image=img, command=lambda f=f: use(f)).pack()
            ttk.Label(cell, text=f.name[-17:], font=("Consolas", 8)).pack()
        win._thumbs = keep  # 防 GC
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=6)

    def _back_to_live(self) -> None:
        self.mode = "live"
        self.work_path = ROOT / "frames" / "latest.png"
        self._refresh_preview()
        self._log("[模式] 回到实时图传")

    def _open_frames(self) -> None:
        d = ROOT / "frames"
        d.mkdir(exist_ok=True)
        os.startfile(d)  # type: ignore[attr-defined]

    def _open_zf(self) -> None:
        for p in ZF_EXE_CANDIDATES:
            if p.is_file():
                subprocess.Popen([str(p)])
                self._log(f"[启动] 逐飞助手 {p.name}")
                return
        self._log("[提示] 未找到逐飞助手 exe，请手动打开")

    def _log(self, msg: str) -> None:
        self.data_text.insert("end", f"{time.strftime('%H:%M:%S')} {msg}\n")
        self.data_text.see("end")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="逐飞AI图传与逆透视实验")
    parser.add_argument("--autostart", action="store_true", help="打开后启动接收桥")
    args = parser.parse_args()
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:  # noqa: BLE001
        pass
    app = BridgeApp(root)
    if args.autostart:
        root.after(200, app._start_bridge)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
