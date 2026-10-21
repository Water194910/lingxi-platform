"""灵犀平台桌面入口：复用现有逐飞桥，提供浅/深色工作台。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import app as bridge_ui
from zf_ai_bridge.bridge import AiBridge, BridgeConfig
from zf_ai_bridge.inputs import parse_input_url
from zf_ai_bridge.protocol import ProtocolParser
from zf_ai_bridge.tap import TapForwarder
from zf_ai_bridge.desktop_runtime import PRODUCT_NAME, VERSION, data_directory
from zf_ai_bridge.motion import MotionButton
from zf_ai_bridge.ipm_view import AdvancedPerspectiveWindow

PALETTES = {
    "light": dict(bg="#f9f9f9", side="#f0f0f0", card="#ffffff", text="#202020",
                  muted="#737373", line="#e5e5e5", hover="#e8e8e8", accent="#242424",
                  on_accent="#ffffff", canvas="#efefef", green="#23835b"),
    "dark": dict(bg="#212121", side="#181818", card="#292929", text="#ededed",
                 muted="#a0a0a0", line="#383838", hover="#343434", accent="#eeeeee",
                 on_accent="#202020", canvas="#171717", green="#69c49a"),
}


class LingXiApp(bridge_ui.BridgeApp):
    def __init__(self, root: tk.Tk):
        self.data_dir = data_directory()
        bridge_ui.ROOT = self.data_dir
        bridge_ui.SETTINGS_PATH = self.data_dir/"settings.json"
        self.pref_path = self.data_dir/"desktop.json"
        try:
            self.prefs = json.loads(self.pref_path.read_text(encoding="utf-8"))
            if not isinstance(self.prefs, dict):
                self.prefs = {}
        except (OSError, ValueError):
            self.prefs = {}
        self.theme = self.prefs.get("theme", "light")
        if self.theme not in PALETTES:
            self.theme = "light"
        self._page = "vision"
        self._last_frame_notice = 0.
        self._auto_fit_size = None
        self._history_window = None
        self._motion_enabled = bool(self.prefs.get("motion", True))
        self._page_job = None
        super().__init__(root)
        root.title(f"{PRODUCT_NAME}  ·  视觉与设备工作台")
        icon = Path(__file__).resolve().parent/'assets'/'lingxi.ico'
        if icon.is_file():
            root.iconbitmap(str(icon))
        root.geometry("1280x820")
        root.minsize(1080, 720)
        self.input_var.set(self.input_var.get() or "tcp-listen://0.0.0.0:8081")
        # 写权限每次启动均关闭，配置文件不能自动开启设备写入。
        self.write_var.set(False)
        self._apply_theme()
        self._update_metrics()

    def _button(self, parent, text, command, *, primary=False, **pack):
        button = MotionButton(parent, text=text, command=command,
                            palette=lambda: PALETTES[self.theme], enabled=lambda: self._motion_enabled,
                            style="Primary.TButton" if primary else "TButton")
        button.pack(**(pack or dict(fill="x", pady=3)))
        return button

    def _build_ui(self):
        root = self.root
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self.shell = ttk.Frame(root)
        self.shell.pack(fill="both", expand=True)
        self.sidebar = ttk.Frame(self.shell, style="Side.TFrame", width=172, padding=(16, 24))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        ttk.Label(self.sidebar, text="灵犀", style="Brand.TLabel").pack(anchor="w", pady=(0, 2))
        ttk.Label(self.sidebar, text="LINGXI PLATFORM", style="SideMuted.TLabel", font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 30))
        self.nav_buttons = {}
        for key, title in (("vision", "视觉工作台"), ("analysis", "图像分析"), ("events", "设备与事件")):
            self.nav_buttons[key] = self._button(self.sidebar, title, lambda k=key: self.show_page(k))
        ttk.Label(self.sidebar, text="工作区", style="SideMuted.TLabel").pack(anchor="w", pady=(28, 8))
        self._button(self.sidebar, "打开数据目录", self._open_frames)
        self._button(self.sidebar, "关于灵犀", self._about)
        footer = ttk.Frame(self.sidebar, style="Side.TFrame")
        footer.pack(side="bottom", fill="x")
        self.theme_btn = self._button(footer, "切换深色", self.toggle_theme)
        ttk.Label(footer, text=f"预览版 {VERSION}", style="SideMuted.TLabel", font=("Segoe UI", 8)).pack(anchor="w", pady=(10, 0))

        workspace = ttk.Frame(self.shell, padding=(24, 18))
        workspace.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(workspace)
        header.pack(fill="x", pady=(0, 18))
        heading = ttk.Frame(header)
        heading.pack(side="left")
        self.page_title = tk.StringVar(value="视觉工作台")
        ttk.Label(heading, textvariable=self.page_title, style="Title.TLabel").pack(anchor="w")
        self.page_description = tk.StringVar(value="从原始图像出发，观察、分析与标定。")
        ttk.Label(heading, textvariable=self.page_description, style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
        self.connection_var = tk.StringVar(value="未连接")
        ttk.Label(header, textvariable=self.connection_var, style="Pill.TLabel", padding=(12, 6)).pack(side="right")

        self.pages = {}
        self.status_var = tk.StringVar(value="就绪")
        self.page_host = ttk.Frame(workspace)
        self.page_host.pack(fill="both", expand=True)
        for key in ("vision", "analysis", "events"):
            self.pages[key] = ttk.Frame(self.page_host)
        self.pages["vision"].place(x=0, y=0, relwidth=1, relheight=1)
        vision = self.pages["vision"]
        vision.columnconfigure(0, weight=1)
        vision.columnconfigure(1, weight=0, minsize=260)
        vision.rowconfigure(0, weight=1)

        viewer = ttk.Frame(vision, style="Card.TFrame", padding=16)
        viewer.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        top = ttk.Frame(viewer, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 12))
        ttk.Label(top, text="摄像头", style="CardHeading.TLabel").pack(side="left")
        self.frame_mode_var = tk.StringVar(value="等待图传")
        self.live_btn = self._button(top, "回到实时", self._back_to_live, side="right", padx=(10, 0))
        self.live_btn.state(["disabled"])
        ttk.Label(top, textvariable=self.frame_mode_var, style="CardMuted.TLabel").pack(side="right")
        self.preview = tk.Canvas(viewer, highlightthickness=0, borderwidth=0)
        self.preview.pack(fill="both", expand=True)
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
        toolbar = ttk.Frame(viewer, style="Card.TFrame")
        toolbar.pack(fill="x", pady=(12, 6))
        for text, command in (("−", lambda: self._zoom_by(-1)), ("＋", lambda: self._zoom_by(1)),
                              ("适应", self._fit_view), ("1:1", lambda: self._zoom_by(0, absolute=1))):
            self._button(toolbar, text, command, side="left", padx=(0, 5))
        self._button(toolbar, "高级标定", self._open_advanced_perspective, side="left", padx=3)
        self.zoom_var = tk.StringVar(value="滚轮缩放 · 右键移动")
        ttk.Label(toolbar, textvariable=self.zoom_var, style="CardMuted.TLabel").pack(side="right")
        self.probe_var = tk.StringVar(value="点击原图查看像素值")
        ttk.Label(viewer, textvariable=self.probe_var, style="CardMuted.TLabel", wraplength=500).pack(anchor="w", pady=(2, 0))

        controls = ttk.Frame(vision, width=260)
        controls.grid(row=0, column=1, sticky="nsew")
        self.control_tabs = ttk.Notebook(controls)
        self.control_tabs.pack(fill="both", expand=True)
        connection, appearance, capture = (ttk.Frame(self.control_tabs) for _ in range(3))
        for title, tab in (("连接", connection), ("显示", appearance), ("采集", capture)):
            self.control_tabs.add(tab, text=title)
        self._build_connection_card(connection)
        display = self._card(appearance, "显示")
        self.edge_var, self.center_var = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        self.false_var = tk.BooleanVar(value=False)
        for title, var, command in (("边线", self.edge_var, self._on_overlay_toggle),
                                     ("路径 / 中线", self.center_var, self._on_overlay_toggle),
                                     ("伪彩色", self.false_var, self._refresh_preview)):
            ttk.Checkbutton(display, text=title, variable=var, command=command, style="Card.TCheckbutton").pack(anchor="w", pady=3)
        ttk.Label(display, text="默认显示干净原图，保存不含标注。", style="CardMuted.TLabel", wraplength=220).pack(anchor="w", pady=(8, 0))
        self.motion_var = tk.BooleanVar(value=self._motion_enabled)
        ttk.Checkbutton(display, text="界面动效", variable=self.motion_var,
                        command=self._toggle_motion, style="Card.TCheckbutton").pack(anchor="w", pady=(12, 0))
        actions = self._card(capture, "采集与标定")
        self._button(actions, "四点逆透视", self._open_perspective, primary=True)
        self._button(actions, "高级逆透视标定", self._open_advanced_perspective)
        row = ttk.Frame(actions, style="Card.TFrame")
        row.pack(fill="x", pady=3)
        self._button(row, "导入图片", self._upload_image, side="left", expand=True, fill="x", padx=(0, 4))
        self._button(row, "历史帧", self._pick_frame, side="left", expand=True, fill="x")
        self._button(actions, "回到实时", self._back_to_live)
        self.save_image_btn = self._button(actions, "保存当前原图…", self._save_current_image)
        self.rec_btn = self._button(actions, "开始录制", self._toggle_record)

        self._build_analysis_page()
        self._build_events_page()
        bottom = ttk.Frame(workspace)
        bottom.pack(fill="x", pady=(14, 0))
        self.metrics_var = tk.StringVar(value="尚无新帧")
        ttk.Label(bottom, textvariable=self.metrics_var, style="Muted.TLabel").pack(side="left")
        ttk.Label(bottom, text="AI 127.0.0.1:8765", style="Muted.TLabel").pack(side="right")
        self._log("灵犀平台已就绪。连接设备或导入图片，开始视觉分析。")

    def _card(self, parent, title):
        card = ttk.Frame(parent, style="Card.TFrame", padding=14)
        card.pack(fill="x", pady=(0, 12))
        ttk.Label(card, text=title, style="CardHeading.TLabel").pack(anchor="w", pady=(0, 10))
        return card

    def _build_connection_card(self, parent):
        card = self._card(parent, "设备连接")
        ttk.Label(card, text="输入源", style="CardMuted.TLabel").pack(anchor="w", pady=(0, 4))
        self.input_var = tk.StringVar(value="tcp-listen://0.0.0.0:8081")
        self.input_combo = ttk.Combobox(card, textvariable=self.input_var, width=26, values=(
            "tcp-listen://0.0.0.0:8081", "serial://COM5:115200", "demo://"))
        self.input_combo.pack(fill="x", pady=(0, 8))
        self.start_btn = self._button(card, "连接设备", self.toggle_bridge, primary=True)
        self.write_var = tk.BooleanVar(value=False)
        self.write_chk = ttk.Checkbutton(card, text="允许 AI 写设备", variable=self.write_var,
                                        command=self._on_write_toggle, style="Card.TCheckbutton")
        self.write_chk.pack(anchor="w", pady=(8, 2))
        self.tap_var = tk.BooleanVar(value=False)
        self.tap_chk = ttk.Checkbutton(card, text="旁路到官方助手", variable=self.tap_var, style="Card.TCheckbutton")
        self.tap_chk.pack(anchor="w", pady=2)
        self.tap_addr_var = tk.StringVar(value="127.0.0.1:8086")
        ttk.Entry(card, textvariable=self.tap_addr_var).pack(fill="x", pady=(5, 0))

    def _build_analysis_page(self):
        page = self.pages["analysis"]
        card = ttk.Frame(page, style="Card.TFrame", padding=24)
        card.pack(fill="both", expand=True)
        ttk.Label(card, text="分析当前图像", style="CardHeading.TLabel").pack(anchor="w")
        ttk.Label(card, text="使用实时原图，或导入一张图片。所有分析入口共用当前工作图。",
                  style="CardMuted.TLabel", wraplength=660).pack(anchor="w", pady=(8, 24))
        for title, description, command in (
            ("高级逆透视", "多组实测拟合、滑条与精确输入、实时鸟瞰、矩阵与C查表导出。", self._open_advanced_perspective),
            ("四点逆透视", "冻结干净原图，选择地面矩形四角，保存实测标定。", self._open_perspective),
            ("像素矩阵", "查看灰度数值与阈值，检查二值化结果。", self._open_matrix),
            ("导入图片", "支持 PNG、JPEG、BMP 等常见格式。", self._upload_image),
            ("选择历史帧", "预览、锁定或批量保存历史原图。", self._pick_frame)):
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x", pady=10)
            self._button(row, title, command, side="left", padx=(0, 18))
            ttk.Label(row, text=description, style="CardMuted.TLabel", wraplength=580).pack(side="left")

    def _build_events_page(self):
        page = self.pages["events"]
        card = ttk.Frame(page, style="Card.TFrame", padding=16)
        card.pack(fill="both", expand=True)
        top = ttk.Frame(card, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 12))
        ttk.Label(top, text="设备与事件", style="CardHeading.TLabel").pack(side="left")
        self._button(top, "清空", self._clear_log, side="right")
        self._button(top, "通道摘要", self._do_snapshot, side="right", padx=8)
        self._button(top, "打开官方助手", self._open_zf, side="right")
        self.data_text = tk.Text(card, wrap="word", borderwidth=0, highlightthickness=0,
                                 font=("Cascadia Mono", 10), padx=8, pady=8, state="disabled")
        self.data_text.pack(fill="both", expand=True)
        ttk.Label(card, textvariable=self.status_var, style="CardMuted.TLabel", wraplength=750).pack(anchor="w")

    def show_page(self, key):
        changed = key != self._page
        self._finish_page_motion()
        for page in self.pages.values():
            page.place_forget()
        page = self.pages[key]
        page.place(x=0, y=0, relwidth=1, relheight=1)
        self._page = key
        title, description = {
            "vision": ("视觉工作台", "从原始图像出发，观察、分析与标定。"),
            "analysis": ("图像分析", "把像素变成可以验证的几何信息。"),
            "events": ("设备与事件", "查看接收状态、通道摘要与运行记录。"),
        }[key]
        self.page_title.set(title)
        self.page_description.set(description)
        for name, button in self.nav_buttons.items():
            button.configure(style="Selected.TButton" if name == key else "TButton")
        if changed and self._motion_enabled:
            started = time.monotonic()

            def tick():
                self._page_job = None
                progress = min(1., (time.monotonic()-started)/.18)
                page.place_configure(x=round(14*(1-progress)**3))
                if progress < 1:
                    self._page_job = self.root.after(16, tick)

            tick()

    def _finish_page_motion(self):
        if self._page_job is not None:
            self.root.after_cancel(self._page_job)
            self._page_job = None
        if self._page in self.pages:
            self.pages[self._page].place_configure(x=0)

    def _refresh_motion_buttons(self, parent=None):
        for widget in (parent or self.root).winfo_children():
            if isinstance(widget, MotionButton):
                widget.refresh()
            self._refresh_motion_buttons(widget)

    def _toggle_motion(self):
        self._motion_enabled = self.motion_var.get()
        self._finish_page_motion()
        self._refresh_motion_buttons()
        self._save_desktop_preferences()

    def _ipm_current_frame(self):
        frame = self.frame_sink.last_frame if self.mode == 'live' else None
        if frame:
            return frame[:4]
        snapshot = getattr(self, '_display_raw', None)
        if snapshot:
            w, h, rgb = snapshot
            return (0, w, h, rgb)
        return None

    def _open_advanced_perspective(self):
        existing = getattr(self, '_ipm_window', None)
        if existing and not existing._closed:
            existing.win.lift()
            return
        frame = self._ipm_current_frame()
        if not frame:
            messagebox.showinfo('高级逆透视', '请先接收图传，或导入一张188×120干净原图。', parent=self.root)
            return
        try:
            kind, w, h, rgb = frame
            window = AdvancedPerspectiveWindow(self.root, rgb, w, h, kind, self.data_dir/'frames',
                frame_provider=lambda: self.frame_sink.last_frame[:4] if self.frame_sink.last_frame else None,
                palette=PALETTES[self.theme], capture_provider=self._ipm_current_frame)
            self._ipm_window = window
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法打开高级标定', str(exc), parent=self.root)

    def _apply_theme(self):
        c = PALETTES[self.theme]
        self.root.configure(bg=c["bg"])
        style = self.style
        style.configure(".", background=c["bg"], foreground=c["text"], font=("Microsoft YaHei UI", 10))
        for name, color in (("TFrame", c["bg"]), ("Card.TFrame", c["card"]), ("Side.TFrame", c["side"])):
            style.configure(name, background=color)
        for name, background, foreground in (
            ("TLabel", c["bg"], c["text"]), ("Muted.TLabel", c["bg"], c["muted"]),
            ("CardHeading.TLabel", c["card"], c["text"]), ("CardMuted.TLabel", c["card"], c["muted"]),
            ("SideMuted.TLabel", c["side"], c["muted"]), ("Brand.TLabel", c["side"], c["text"]),
            ("Title.TLabel", c["bg"], c["text"]), ("Pill.TLabel", c["side"], c["green"])):
            style.configure(name, background=background, foreground=foreground)
        style.configure("Brand.TLabel", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("CardHeading.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TButton", padding=(10, 8), background=c["card"], foreground=c["text"],
                        borderwidth=0, relief="flat", focusthickness=0)
        style.map("TButton", background=[("active", c["hover"]), ("disabled", c["side"])],
                  foreground=[("disabled", c["muted"])])
        style.configure("Selected.TButton", background=c["hover"])
        style.configure("Primary.TButton", background=c["accent"], foreground=c["on_accent"])
        style.map("Primary.TButton", background=[("active", c["muted"])], foreground=[("active", c["on_accent"])])
        style.configure("TEntry", fieldbackground=c["card"], foreground=c["text"], padding=6, bordercolor=c["line"], insertcolor=c["text"])
        style.configure("TCombobox", fieldbackground=c["card"], background=c["card"], foreground=c["text"],
                        arrowcolor=c["muted"], padding=6, bordercolor=c["line"], insertcolor=c["text"])
        style.map("TCombobox", fieldbackground=[("readonly", c["card"])], foreground=[("readonly", c["text"])])
        style.configure("TNotebook", background=c["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=c["side"], foreground=c["muted"], padding=(13, 8), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", c["card"]), ("active", c["hover"])],
                  foreground=[("selected", c["text"])])
        for name, bg in (("TCheckbutton", c["bg"]), ("Card.TCheckbutton", c["card"])):
            style.configure(name, background=bg, foreground=c["text"], padding=3)
            style.map(name, background=[("active", bg)], indicatorbackground=[("selected", c["accent"]), ("!selected", c["side"])],
                      indicatorforeground=[("selected", c["on_accent"])])
        self.preview.configure(background=c["canvas"])
        self.data_text.configure(background=c["card"], foreground=c["text"], insertbackground=c["text"], selectbackground=c["hover"])
        self.root.option_add("*TCombobox*Listbox.background", c["card"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.theme_btn.configure(text="切换浅色" if self.theme == "dark" else "切换深色")
        for name, button in self.nav_buttons.items():
            button.configure(style="Selected.TButton" if name == self._page else "TButton")
        if self._photo is None:
            self._preview_message("等待图像\n\n连接设备，或导入一张图片")
        self._refresh_motion_buttons()
        ipm = getattr(self, '_ipm_window', None)
        if ipm and not ipm._closed:
            ipm.apply_palette(c)

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self._apply_theme()
        self._save_desktop_preferences()

    def _save_desktop_preferences(self):
        self.prefs["theme"] = self.theme
        self.prefs["motion"] = self._motion_enabled
        try:
            self.pref_path.write_text(json.dumps(self.prefs, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            self._log(f"[设置] 保存失败：{exc}")

    def _preview_message(self, text):
        self.preview.delete("all")
        self._canvas_img = None
        self.preview.create_text(max(180, self.preview.winfo_width()/2), max(140, self.preview.winfo_height()/2),
                                 text=text, fill=PALETTES[self.theme]["muted"], justify="center",
                                 font=("Microsoft YaHei UI", 12), tags="empty")

    def _refresh_preview(self):
        self.preview.delete("empty")
        frame = self.frame_sink.last_frame
        size = (frame[1], frame[2]) if frame and self.mode == "live" else None
        if size and size != self._auto_fit_size and self.preview.winfo_width() > 1:
            self._auto_fit_size = size
            self._zoom = max(1, min(16, int(min(self.preview.winfo_width()/size[0], self.preview.winfo_height()/size[1]))))
        super()._refresh_preview()

    def _fit_view(self):
        w, h = self.frame_sink.last_size
        if self.mode != "live":
            w, h = getattr(self, "_src_size", (0, 0))
        if w and h:
            available_w, available_h = self.preview.winfo_width(), self.preview.winfo_height()
            self._zoom = max(1, min(16, int(min(available_w/w, available_h/h))))
            self._refresh_preview()
            self.preview.xview_moveto(0)
            self.preview.yview_moveto(0)

    def _set_mode(self, mode, path, label):
        # 选择工作图只切换主预览；像素矩阵由用户单独打开。
        self.mode, self.work_path = mode, path
        self._auto_fit_size = None
        self._refresh_preview()
        self._fit_view()
        self.live_btn.state(["!disabled"])
        self.frame_mode_var.set("本地图像 · 已锁定")
        self.show_page("vision")
        self._log(f"[模式] {label}；点击画面顶部「回到实时」恢复接收预览。")

    def _back_to_live(self):
        self.mode = "live"
        self.work_path = self.data_dir/"frames"/"latest.png"
        self._auto_fit_size = None
        self._display_raw = None
        self._photo = None
        self._src_size = (0, 0)
        self._zoom = 1
        # 清除锁定画面，立即呈现最新内存帧；无实时图时显示等待状态。
        self._preview_message("等待实时图像")
        self._refresh_preview()
        self.preview.xview_moveto(0)
        self.preview.yview_moveto(0)
        self.live_btn.state(["disabled"])
        self.frame_mode_var.set("实时图像" if self.frame_sink.last_frame else "等待实时图像")
        self.show_page("vision")
        self._log("[模式] 已回到实时图传")

    def _save_current_image(self):
        from zf_ai_bridge.image import write_png
        try:
            if self.mode == "live":
                frame = self.frame_sink.last_frame
                if frame is None:
                    messagebox.showinfo("保存原图", "还没有实时图像，请先连接设备或选择历史帧。", parent=self.root)
                    return
                _, w, h, rgb, _ = frame
                # 冻结点击时的原始像素，保存对话框内即使收到新帧也不替换。
                import tempfile
                with tempfile.TemporaryDirectory(prefix="lingxi-save-") as folder:
                    snapshot = Path(folder)/"raw.png"
                    write_png(snapshot, w, h, rgb)
                    data = snapshot.read_bytes()
                name = time.strftime("frame_%Y%m%d_%H%M%S.png")
            else:
                data = self.work_path.read_bytes()
                name = getattr(self, "_history_name", "image.png") if self.mode == "history" else "image.png"
            self._export_history_frames([(name, data)])
        except (OSError, ValueError) as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)

    def _export_history_frames(self, frames, parent=None):
        parent = parent or self.root
        if not frames:
            return
        try:
            if len(frames) == 1:
                name, data = frames[0]
                selected = filedialog.asksaveasfilename(parent=parent, title="保存原始图像",
                    initialfile=name, defaultextension=".png", filetypes=[("PNG 图像", "*.png")])
                if not selected:
                    return
                Path(selected).write_bytes(data)
                self._log(f"[保存] 原图 → {selected}")
            else:
                selected = filedialog.askdirectory(parent=parent, title=f"保存 {len(frames)} 张历史原图到文件夹")
                if not selected:
                    return
                for name, data in frames:
                    target = Path(selected)/name
                    index = 1
                    while True:
                        try:
                            # 批量导出不覆盖已有文件；保留原文件名，重名加序号。
                            with target.open("xb") as stream:
                                stream.write(data)
                            break
                        except FileExistsError:
                            target = Path(selected)/f"{Path(name).stem}_{index}.png"
                            index += 1
                self._log(f"[保存] {len(frames)} 张历史原图 → {selected}")
        except OSError as exc:
            messagebox.showerror("保存失败", f"部分文件可能已保存。\n{exc}", parent=parent)

    def _pick_frame(self):
        if self._history_window is not None and self._history_window.winfo_exists():
            self._history_window.lift()
            return
        # 捕获原文件字节，列表打开期间后台淘汰旧帧也能查看和导出。
        from zf_ai_bridge.extras import load_png_rgb
        frames = []
        for path in sorted((self.data_dir/"frames").glob("frame_*.png"), reverse=True):
            try:
                frames.append((path.name, path.read_bytes()))
            except OSError:
                continue
        if not frames:
            messagebox.showinfo("历史帧", "暂无历史图像，请先连接设备并接收图传。", parent=self.root)
            return
        win = self._history_window = tk.Toplevel(self.root)
        win.title("历史帧 · 预览与保存")
        win.geometry("900x620")
        win.minsize(760, 500)
        ttk.Label(win, text=f"{len(frames)} 张原图 · Ctrl / Shift 多选保存 · 双击锁定查看",
                  padding=12).pack(anchor="w")
        body = ttk.Frame(win, padding=(12, 0))
        body.pack(fill="both", expand=True)
        listing = ttk.Frame(body)
        listing.pack(side="left", fill="both", expand=True)
        tree = ttk.Treeview(listing, show="tree", selectmode="extended", height=15)
        tree.column("#0", width=330, stretch=True)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        preview = ttk.Label(body, anchor="center", width=36)
        preview.pack(side="right", fill="both", expand=True, padx=(16, 0))
        for i, (name, _) in enumerate(frames):
            tree.insert("", "end", iid=str(i), text=name)

        def chosen():
            selected = tree.selection()
            focus = tree.focus()
            return int(focus if focus in selected else selected[0]) if selected else None

        def show_preview(_event=None):
            index = chosen()
            if index is None:
                return
            try:
                img = tk.PhotoImage(data=frames[index][1], format="png")
                if img.width() <= 200 and img.height() <= 150:
                    img = img.zoom(2)
                else:
                    factor = max(1, (max(img.width(), img.height())+359)//360)
                    img = img.subsample(factor)
                preview.configure(image=img, text="")
                preview._photo = img
            except tk.TclError:
                preview.configure(image="", text="图像无法预览")

        def lock(_event=None):
            index = chosen()
            if index is None:
                return
            name, data = frames[index]
            try:
                # 使用独立工作图，后台仅维护frame_*.png，不会清理它。
                target = self.data_dir/"frames"/"history_selected.png"
                target.write_bytes(data)
                load_png_rgb(target)
                self._history_name = name
                self._set_mode("history", target, f"历史帧 · {name}")
                win.destroy()
            except (OSError, ValueError) as exc:
                messagebox.showerror("无法打开历史帧", str(exc), parent=win)

        tree.bind("<<TreeviewSelect>>", show_preview)
        tree.bind("<Double-1>", lock)
        buttons = ttk.Frame(win, padding=12)
        buttons.pack(fill="x")
        self._button(buttons, "锁定查看", lock, side="left", padx=(0, 8))
        self._button(buttons, "保存选中…", lambda: self._export_history_frames(
            [frames[int(i)] for i in tree.selection()], parent=win), side="left", padx=(0, 8))
        self._button(buttons, "保存全部…", lambda: self._export_history_frames(frames, parent=win), side="left")
        self._button(buttons, "关闭", win.destroy, side="right")
        # 持有快照和控件，方便自动化检查真实按钮流程。
        win.frame_list = tree
        tree.selection_set("0")
        tree.focus("0")
        show_preview()

    def _on_overlay_toggle(self):
        self.frame_sink.show_edges = self.edge_var.get()
        self.frame_sink.show_center = self.center_var.get()
        self.frame_sink.save_annotated = self.edge_var.get() or self.center_var.get()
        self._refresh_preview()

    def _log(self, msg):
        # 图传高帧率时避免持续刷日志；数据统计仍每帧更新。
        if msg.startswith("[图传]"):
            if time.monotonic()-self._last_frame_notice < 5:
                return
            self._last_frame_notice = time.monotonic()
        self.data_text.configure(state="normal")
        super()._log(msg)
        if int(self.data_text.index("end-1c").split(".")[0]) > 1000:
            self.data_text.delete("1.0", "200.0")
        self.data_text.configure(state="disabled")

    def _clear_log(self):
        self.data_text.configure(state="normal")
        self.data_text.delete("1.0", "end")
        self.data_text.configure(state="disabled")

    def _start_bridge(self):
        if self.running:
            return
        try:
            self.proto = ProtocolParser()
            self.frame_sink.clear_overlays()
            self.bridge = AiBridge(BridgeConfig(port=8765, allow_write=self.write_var.get()))
            self.bridge.set_write_handler(self._on_write)
            self.bridge.start()
            self.source = parse_input_url(self.input_var.get().strip(), self._on_bytes)
            self.source.start()
            self.tap = TapForwarder(self.tap_addr_var.get().strip()) if self.tap_var.get() else None
            if self.tap:
                self.tap.start()
        except Exception as exc:
            for resource in (self.source, self.tap, self.bridge):
                if resource:
                    resource.stop()
            self.source = self.tap = self.bridge = None
            self._log(f"[连接] 失败：{exc}")
            messagebox.showerror("连接失败", f"{exc}\n\n请检查输入源，以及是否已有接收程序占用端口。", parent=self.root)
            return
        self.running = True
        self.input_combo.configure(state="disabled")
        self.start_btn.configure(text="断开连接")
        self.connection_var.set("接收中 · 等待数据")
        self._save_settings()
        self._log(f"[连接] {self.input_var.get()}；AI 127.0.0.1:8765")

    def _stop_bridge(self):
        super()._stop_bridge()
        self.input_combo.configure(state="normal")
        self.start_btn.configure(text="连接设备")
        self.connection_var.set("已断开")

    def _update_metrics(self):
        stats = self.frame_sink.stats()
        frame = self.frame_sink.last_frame
        if self.mode != "live":
            self.frame_mode_var.set("本地图像 · 已锁定")
        elif frame:
            kind, w, h, _, _ = frame
            self.frame_mode_var.set(f"{ {1:'二值', 2:'灰度', 3:'RGB565'}.get(kind, '图像')} · {w} × {h}")
            self.metrics_var.set(f"{stats['frames']} 帧   ·   接收 {stats['estFps']:.1f} fps   ·   最大间隔 {stats['maxGapSeconds']} s")
        if self.running:
            age = time.time()-self.frame_sink._last_ts if frame else float("inf")
            self.connection_var.set("数据接收中" if age < 2 else
                                    (f"通道接收中 · {self.sample_count} 样本" if self.sample_count else "接收中 · 等待新帧"))
        self.root.after(500, self._update_metrics)

    def _open_zf(self):
        selected = self.prefs.get("official_exe", "")
        if not selected or not Path(selected).is_file():
            selected = filedialog.askopenfilename(parent=self.root, title="选择官方逐飞助手 EXE", filetypes=[("Windows应用", "*.exe")])
        if selected:
            import subprocess
            try:
                subprocess.Popen([selected], cwd=str(Path(selected).parent))
                self.prefs["official_exe"] = selected
                self._save_desktop_preferences()
            except OSError as exc:
                messagebox.showerror("无法启动", str(exc), parent=self.root)

    def _about(self):
        messagebox.showinfo("关于灵犀平台", f"{PRODUCT_NAME}\n{VERSION}\n\n逐飞 V2 图传 · 原始像素 · 四点逆透视\n基于组内 zf-ai-bridge，沿用 GPLv3 许可。\n\n数据目录：{self.data_dir}\n\n逆透视工具待实车标定验证。", parent=self.root)

    def _on_close(self):
        self._finish_page_motion()
        self._refresh_motion_buttons()
        self._save_desktop_preferences()
        super()._on_close()


def main():
    parser = argparse.ArgumentParser(description=PRODUCT_NAME)
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--self-test-report", type=Path, help="离线运行桌面功能自检并写入JSON")
    args = parser.parse_args()
    if args.self_test_report:
        from zf_ai_bridge.desktop_smoke import run
        return run(args.self_test_report)
    root = tk.Tk()
    try:
        app = LingXiApp(root)
    except OSError as exc:
        root.withdraw()
        messagebox.showerror("灵犀平台无法启动", str(exc), parent=root)
        root.destroy()
        return 1
    if args.autostart:
        root.after(200, app._start_bridge)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
