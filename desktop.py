"""灵犀平台桌面入口：双主题工作台，自绘圆角组件与轻量动效。

职责与旧版一致：接收逐飞 V2 图传、AI 桥、历史帧、逆透视入口。
这里只把外观换成设计系统组件，不改变任何协议、标定或保存行为。
"""
from __future__ import annotations

import argparse
import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import app as bridge_ui
from zf_ai_bridge import theme, widgets
from zf_ai_bridge.bridge import AiBridge, BridgeConfig
from zf_ai_bridge.desktop_runtime import PRODUCT_NAME, VERSION, data_directory
from zf_ai_bridge.inputs import parse_input_url
from zf_ai_bridge.ipm_view import AdvancedPerspectiveWindow
from zf_ai_bridge.protocol import ProtocolParser
from zf_ai_bridge.tap import TapForwarder

PALETTES = theme.THEMES
UI = theme.FONT_UI
MONO = theme.FONT_MONO

PAGES = {
    "vision": ("视觉工作台", "从原始图像出发，观察、分析与标定。"),
    "analysis": ("图像分析", "把像素变成可以验证的几何信息。"),
    "events": ("设备与事件", "查看接收状态、通道摘要与运行记录。"),
}

ANALYSIS_ENTRIES = (
    ("高级逆透视", "多组实测拟合、滑条与精确输入、实时鸟瞰、矩阵与 C 查表导出。",
     "calibrate", "_open_advanced_perspective"),
    ("四点逆透视", "冻结干净原图，选择地面矩形四角，保存实测标定。", "fit",
     "_open_perspective"),
    ("像素矩阵", "查看灰度数值与阈值，检查二值化结果。", "analysis", "_open_matrix"),
    ("导入图片", "支持 PNG、JPEG、BMP 等常见格式。", "upload", "_upload_image"),
    ("选择历史帧", "预览、锁定或批量保存历史原图。", "history", "_pick_frame"),
)


class LingXiApp(bridge_ui.BridgeApp):
    def __init__(self, root: tk.Tk):
        self.data_dir = data_directory()
        bridge_ui.ROOT = self.data_dir
        bridge_ui.SETTINGS_PATH = self.data_dir / "settings.json"
        self.pref_path = self.data_dir / "desktop.json"
        try:
            self.prefs = json.loads(self.pref_path.read_text(encoding="utf-8"))
            if not isinstance(self.prefs, dict):
                self.prefs = {}
        except (OSError, ValueError):
            self.prefs = {}
        self.theme = self.prefs.get("theme", "light")
        if self.theme not in PALETTES:
            self.theme = "light"
        self.palette = PALETTES[self.theme]
        self._page = "vision"
        self._last_frame_notice = 0.0
        self._auto_fit_size = None
        self._history_window = None
        self._motion_enabled = bool(self.prefs.get("motion", True))
        self._page_job = None
        self.empty = None
        super().__init__(root)
        root.title(f"{PRODUCT_NAME}  ·  视觉与设备工作台")
        icon = Path(__file__).resolve().parent / "assets" / "lingxi.ico"
        if icon.is_file():
            try:
                root.iconbitmap(str(icon))
            except tk.TclError:
                pass
        root.geometry("1360x860")
        root.minsize(1140, 760)
        self.input_var.set(self.input_var.get() or "tcp-listen://0.0.0.0:8081")
        # 写权限每次启动均关闭，配置文件不能自动开启设备写入。
        self.write_var.set(False)
        self._apply_theme()
        self._update_metrics()

    # ------------------------------------------------------------ 主题

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self._apply_theme()
        self._save_desktop_preferences()

    def _walk(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self._walk(child)

    def _recolor(self, widget, mapping):
        """把原生 tk 控件里仍是旧主题色的 bg/fg 换成新主题色。"""
        for option in ("bg", "fg", "background", "foreground", "insertbackground"):
            try:
                value = widget.cget(option)
            except (tk.TclError, AttributeError):
                continue
            if isinstance(value, str) and value.lower() in mapping:
                try:
                    widget.configure(**{option: mapping[value.lower()]})
                except tk.TclError:
                    pass
        for child in widget.winfo_children():
            self._recolor(child, mapping)

    def _apply_theme(self):
        old = getattr(self, "palette", None)
        new = self.palette = PALETTES[self.theme]
        theme.MOTION = self._motion_enabled
        theme.apply_ttk(self.style, new)
        self.root.configure(bg=new["bg"])
        self.root.option_add("*TCombobox*Listbox.background", new["surface_alt"])
        self.root.option_add("*TCombobox*Listbox.foreground", new["text"])
        if old is not None:
            mapping = {}
            for key, value in old.items():
                target = new.get(key)
                if isinstance(value, str) and len(value) == 7 and isinstance(target, str):
                    mapping[value.lower()] = target
            self._recolor(self.root, mapping)
        for widget in self._walk(self.root):
            apply = getattr(widget, "apply_palette", None)
            if callable(apply):
                try:
                    apply(new)
                except tk.TclError:
                    pass
        if getattr(self, "preview", None) is not None:
            self.preview.configure(bg=new["canvas"])
        if getattr(self, "preview_wrap", None) is not None:
            self.preview_wrap.configure(bg=new["surface"])
        if getattr(self, "preview_bg", None) is not None:
            self.preview_bg.configure(bg=new["surface"])
            self._paint_preview_round()
        if getattr(self, "data_text", None) is not None:
            self.data_text.configure(bg=new["surface"], fg=new["text"],
                                     insertbackground=new["text"],
                                     selectbackground=new["surface_hover"])
        if getattr(self, "theme_btn", None) is not None:
            self.theme_btn.icon = "sun" if self.theme == "dark" else "moon"
            self.theme_btn.set_text("切换浅色" if self.theme == "dark" else "切换深色")
        for name, item in getattr(self, "nav_buttons", {}).items():
            item.set_selected(name == self._page, animate=False)
        ipm = getattr(self, "_ipm_window", None)
        if ipm and not ipm._closed:
            try:
                ipm.apply_palette(new)
            except tk.TclError:
                pass
        if getattr(self, "_photo", None) is None:
            self._preview_message("等待图像\n连接设备，或导入一张图片")

    def _save_desktop_preferences(self):
        self.prefs["theme"] = self.theme
        self.prefs["motion"] = self._motion_enabled
        try:
            self.pref_path.write_text(json.dumps(self.prefs, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
        except OSError as exc:
            self._log(f"[设置] 保存失败：{exc}")

    # ------------------------------------------------------------ 构建

    def _button(self, parent, text, command, *, kind="ghost", icon=None,
                height=34, bg=None, expand=False, padx=(0, 0), pady=(0, 0), fill=None):
        button = widgets.RoundedButton(parent, text, command, self.palette, kind=kind,
                                       icon=icon, height=height, bg=bg)
        if expand:
            button.pack(side="left", expand=True, fill="x", padx=padx, pady=pady)
        else:
            button.pack(fill=fill or "x", padx=padx, pady=pady)
        return button

    def _build_ui(self):
        root = self.root
        root.option_add("*Font", (UI, 10))
        self.style = ttk.Style(root)
        theme.apply_ttk(self.style, self.palette)
        self.shell = tk.Frame(root, bg=self.palette["bg"])
        self.shell.pack(fill="both", expand=True)
        self._build_sidebar(self.shell)
        workspace = tk.Frame(self.shell, bg=self.palette["bg"])
        workspace.pack(side="left", fill="both", expand=True, padx=(28, 28), pady=(20, 14))
        self._build_header(workspace)
        self.status_var = tk.StringVar(value="就绪")
        self.page_host = tk.Frame(workspace, bg=self.palette["bg"])
        self.page_host.pack(fill="both", expand=True, pady=(16, 0))
        self.pages = {}
        for key in ("vision", "analysis", "events"):
            self.pages[key] = tk.Frame(self.page_host, bg=self.palette["bg"])
        self.pages["vision"].place(x=0, y=0, relwidth=1, relheight=1)
        self._build_vision_page(self.pages["vision"])
        self._build_analysis_page(self.pages["analysis"])
        self._build_events_page(self.pages["events"])
        self._build_footer(workspace)
        self._log("灵犀平台已就绪。连接设备或导入图片，开始视觉分析。")

    def _build_sidebar(self, parent):
        p = self.palette
        bar = tk.Frame(parent, bg=p["sidebar"], width=200)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        self.sidebar = bar
        brand = tk.Frame(bar, bg=p["sidebar"])
        brand.pack(fill="x", padx=22, pady=(26, 20))
        tk.Label(brand, text="灵犀", bg=p["sidebar"], fg=p["text"],
                 font=(UI, 19, "bold")).pack(anchor="w")
        tk.Label(brand, text="LINGXI PLATFORM", bg=p["sidebar"], fg=p["muted"],
                 font=(UI, 8)).pack(anchor="w", pady=(3, 0))
        self.nav_buttons = {}
        for key, icon in (("vision", "vision"), ("analysis", "analysis"), ("events", "events")):
            title = PAGES[key][0]
            item = widgets.NavItem(bar, p, key, title, lambda k=key: self.show_page(k),
                                   icon=icon, height=44)
            item.pack(fill="x", padx=10, pady=1)
            self.nav_buttons[key] = item
        self.nav_buttons["vision"].set_selected(True, animate=False)

        divider = tk.Frame(bar, bg=p["border_soft"], height=1)
        divider.pack(fill="x", padx=22, pady=(16, 14))
        tk.Label(bar, text="工作区", bg=p["sidebar"], fg=p["muted"],
                 font=(UI, 8)).pack(anchor="w", padx=24, pady=(0, 8))
        for text, icon, command in (("数据目录", "folder", self._open_frames),
                                    ("关于灵犀", "info", self._about)):
            widgets.RoundedButton(bar, text, command, p, icon=icon, height=34,
                                  bg=p["sidebar"]).pack(fill="x", padx=12, pady=2)

        footer = tk.Frame(bar, bg=p["sidebar"])
        footer.pack(side="bottom", fill="x", padx=12, pady=(0, 14))
        self.theme_btn = widgets.RoundedButton(footer, "切换深色", self.toggle_theme, p,
                                               icon="moon", height=34, bg=p["sidebar"])
        self.theme_btn.pack(fill="x")
        tk.Label(footer, text=f"预览版 {VERSION}", bg=p["sidebar"], fg=p["muted"],
                 font=(UI, 8)).pack(anchor="w", padx=6, pady=(10, 0))

    def _build_header(self, parent):
        p = self.palette
        head = tk.Frame(parent, bg=p["bg"])
        head.pack(fill="x")
        left = tk.Frame(head, bg=p["bg"])
        left.pack(side="left")
        self.page_title = tk.StringVar(value=PAGES["vision"][0])
        tk.Label(left, textvariable=self.page_title, bg=p["bg"], fg=p["text"],
                 font=(UI, 21, "bold")).pack(anchor="w")
        self.page_description = tk.StringVar(value=PAGES["vision"][1])
        tk.Label(left, textvariable=self.page_description, bg=p["bg"], fg=p["muted"],
                 font=(UI, 10)).pack(anchor="w", pady=(5, 0))
        self.connection_pill = widgets.Pill(head, p, "未连接", "neutral", bg=p["bg"])
        self.connection_pill.pack(side="right", pady=(8, 0))

    def _build_footer(self, parent):
        p = self.palette
        foot = tk.Frame(parent, bg=p["bg"])
        foot.pack(fill="x", pady=(14, 0))
        chips = tk.Frame(foot, bg=p["bg"])
        chips.pack(side="left")
        self.chip_frames = widgets.MetricChip(chips, p, "总帧数", digits=0)
        self.chip_fps = widgets.MetricChip(chips, p, "接收帧率", unit=" fps", digits=1)
        self.chip_gap = widgets.MetricChip(chips, p, "最大间隔", unit=" s", digits=3)
        for chip in (self.chip_frames, self.chip_fps, self.chip_gap):
            chip.pack(side="left", padx=(0, 10))
        tk.Label(foot, text="AI 接口  127.0.0.1:8765", bg=p["bg"], fg=p["muted"],
                 font=(MONO, 9)).pack(side="right", pady=(16, 0))

    def _build_vision_page(self, page):
        p = self.palette
        page.columnconfigure(0, weight=1)
        page.columnconfigure(1, weight=0, minsize=280)
        page.rowconfigure(0, weight=1)

        self.preview_card = widgets.Card(page, p, padding=(16, 14))
        self.preview_card.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        body = self.preview_card.body
        top = tk.Frame(body, bg=p["surface"])
        top.pack(fill="x")
        tk.Label(top, text="摄像头", bg=p["surface"], fg=p["text"],
                 font=(UI, 11, "bold")).pack(side="left")
        self.live_btn = widgets.RoundedButton(top, "回到实时", self._back_to_live, p,
                                              icon="live", height=30, bg=p["surface"])
        self.live_btn.pack(side="right")
        self.live_btn.set_enabled(False)
        self.frame_mode_var = tk.StringVar(value="等待图传")
        tk.Label(top, textvariable=self.frame_mode_var, bg=p["surface"], fg=p["muted"],
                 font=(UI, 9)).pack(side="right", padx=(0, 12))

        self.preview_wrap = tk.Frame(body, bg=p["surface"])
        self.preview_wrap.pack(fill="both", expand=True, pady=(12, 10))
        self.preview_bg = tk.Canvas(self.preview_wrap, bg=p["surface"],
                                    highlightthickness=0, bd=0)
        self.preview_bg.place(relwidth=1, relheight=1)
        self.preview = tk.Canvas(self.preview_wrap, highlightthickness=0, borderwidth=0,
                                 bg=p["canvas"], takefocus=0)
        self.preview.place(x=1, y=1, relwidth=1, relheight=1, width=-2, height=-2)
        self._preview_round_job = None
        self.preview_wrap.bind("<Configure>", self._redraw_preview_round, add="+")
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
        self.empty = widgets.EmptyState(self.preview_wrap, p)
        self.empty.place(x=1, y=1, relwidth=1, relheight=1, width=-2, height=-2)
        self.empty.start()

        toolbar = tk.Frame(body, bg=p["surface"])
        toolbar.pack(fill="x")
        for text, command in (("−", lambda: self._zoom_by(-1)), ("＋", lambda: self._zoom_by(1)),
                              ("适应", self._fit_view), ("1:1", lambda: self._zoom_by(0, absolute=1))):
            widgets.RoundedButton(toolbar, text, command, p, height=30,
                                  bg=p["surface"]).pack(side="left", padx=(0, 6))
        widgets.RoundedButton(toolbar, "高级标定", self._open_advanced_perspective, p,
                              kind="soft", icon="calibrate", height=30,
                              bg=p["surface"]).pack(side="left", padx=(6, 0))
        self.zoom_var = tk.StringVar(value="滚轮缩放 · 右键移动")
        tk.Label(toolbar, textvariable=self.zoom_var, bg=p["surface"], fg=p["muted"],
                 font=(UI, 9)).pack(side="right")
        self.probe_var = tk.StringVar(value="点击原图查看像素值")
        tk.Label(body, textvariable=self.probe_var, bg=p["surface"], fg=p["muted"],
                 font=(UI, 9)).pack(anchor="w", pady=(8, 0))

        controls = tk.Frame(page, bg=p["bg"])
        controls.grid(row=0, column=1, sticky="nsew")
        self.control_card = widgets.Card(controls, p, padding=(16, 14))
        self.control_card.pack(fill="x")
        self.control_card.configure(height=384)
        self.control_card.pack_propagate(False)
        hint_card = widgets.Card(controls, p, padding=(16, 14))
        hint_card.pack(fill="x", pady=(14, 0))
        hint = hint_card.body
        tk.Label(hint, text="图传速查", bg=p["surface"], fg=p["text"],
                 font=(UI, 11, "bold")).pack(anchor="w")
        tk.Label(hint, text="输入源填 tcp-listen://0.0.0.0:8081，模块主动连接本机 IP。\n"
                            "同一时刻只运行一个接收程序；无帧时先核对端口、防火墙与板端发送状态。",
                 bg=p["surface"], fg=p["muted"], font=(UI, 9), wraplength=238,
                 justify="left").pack(anchor="w", pady=(8, 0))
        cbody = self.control_card.body
        self.control_tabs = widgets.SegmentedControl(
            cbody, p, ("连接", "显示", "采集"), self._on_control_segment, height=32,
            bg=p["surface"])
        self.control_tabs.pack(fill="x", pady=(0, 14))
        host = tk.Frame(cbody, bg=p["surface"])
        host.pack(fill="both", expand=True)
        self.control_pages = {}
        for key in ("connect", "display", "capture"):
            frame = tk.Frame(host, bg=p["surface"])
            self.control_pages[key] = frame
        self.control_pages["connect"].pack(fill="both", expand=True)
        self._build_connection_card(self.control_pages["connect"])
        self._build_display_card(self.control_pages["display"])
        self._build_capture_card(self.control_pages["capture"])

    def _on_control_segment(self, index):
        keys = ("connect", "display", "capture")
        for name, frame in self.control_pages.items():
            frame.pack_forget()
        frame = self.control_pages[keys[index]]
        frame.pack(fill="both", expand=True)
        if self._motion_enabled:
            start = time.monotonic()

            def tick():
                progress = min(1.0, (time.monotonic() - start) / 0.16)
                off = round(10 * (1 - theme.ease_out_cubic(progress)))
                try:
                    frame.pack_configure(padx=(off, 0))
                except tk.TclError:
                    return
                if progress < 1:
                    frame.after(16, tick)
                else:
                    try:
                        frame.pack_configure(padx=0)
                    except tk.TclError:
                        pass

            tick()

    def _build_connection_card(self, parent):
        p = self.palette
        tk.Label(parent, text="输入源", bg=p["surface"], fg=p["muted"],
                 font=(UI, 9)).pack(anchor="w")
        self.input_var = tk.StringVar(value="tcp-listen://0.0.0.0:8081")
        self.input_combo = ttk.Combobox(parent, textvariable=self.input_var, values=(
            "tcp-listen://0.0.0.0:8081", "serial://COM5:115200", "demo://"))
        self.input_combo.pack(fill="x", pady=(6, 12))
        self.start_btn = widgets.RoundedButton(parent, "连接设备", self.toggle_bridge, p,
                                               kind="primary", icon="live", height=38,
                                               bg=p["surface"])
        self.start_btn.pack(fill="x", pady=(0, 14))
        self.write_var = tk.BooleanVar(value=False)
        widgets.CheckRow(parent, p, "允许 AI 写设备", self.write_var,
                         command=self._on_write_toggle).pack(fill="x")
        self.tap_var = tk.BooleanVar(value=False)
        widgets.CheckRow(parent, p, "旁路到官方助手", self.tap_var).pack(fill="x")
        self.tap_addr_var = tk.StringVar(value="127.0.0.1:8086")
        ttk.Entry(parent, textvariable=self.tap_addr_var).pack(fill="x", pady=(8, 0))
        tk.Label(parent, text="同一时刻只运行一个接收程序；模块主动连本机 IP:8081。",
                 bg=p["surface"], fg=p["muted"], font=(UI, 9), wraplength=230,
                 justify="left").pack(anchor="w", pady=(12, 0))

    def _build_display_card(self, parent):
        p = self.palette
        self.edge_var = tk.BooleanVar(value=False)
        self.center_var = tk.BooleanVar(value=False)
        self.false_var = tk.BooleanVar(value=False)
        widgets.CheckRow(parent, p, "显示边线", self.edge_var,
                         command=self._on_overlay_toggle).pack(fill="x", pady=2)
        widgets.CheckRow(parent, p, "显示路径 / 中线", self.center_var,
                         command=self._on_overlay_toggle).pack(fill="x", pady=2)
        widgets.CheckRow(parent, p, "伪彩色（蓝→绿→红）", self.false_var,
                         command=self._refresh_preview).pack(fill="x", pady=2)
        tk.Frame(parent, bg=p["border_soft"], height=1).pack(fill="x", pady=10)
        self.motion_var = tk.BooleanVar(value=self._motion_enabled)
        widgets.CheckRow(parent, p, "界面动效", self.motion_var,
                         command=self._toggle_motion).pack(fill="x", pady=2)
        tk.Label(parent, text="默认显示干净原图，保存与导出不含任何标注。",
                 bg=p["surface"], fg=p["muted"], font=(UI, 9), wraplength=230,
                 justify="left").pack(anchor="w", pady=(12, 0))

    def _build_capture_card(self, parent):
        p = self.palette
        for text, icon, command, kind in (
                ("四点逆透视", "fit", self._open_perspective, "ghost"),
                ("高级逆透视标定", "calibrate", self._open_advanced_perspective, "soft"),
                ("导入图片", "upload", self._upload_image, "ghost"),
                ("历史帧", "history", self._pick_frame, "ghost")):
            widgets.RoundedButton(parent, text, command, p, kind=kind, icon=icon,
                                  height=34, bg=p["surface"]).pack(fill="x", pady=2)
        self.save_image_btn = widgets.RoundedButton(parent, "保存当前原图…",
                                                    self._save_current_image, p,
                                                    icon="save", height=34, bg=p["surface"])
        self.save_image_btn.pack(fill="x", pady=2)
        self.rec_btn = widgets.RoundedButton(parent, "开始录制", self._toggle_record, p,
                                             icon="play", height=34, bg=p["surface"])
        self.rec_btn.pack(fill="x", pady=2)
        tk.Label(parent, text="录制为未压缩 AVI，帧率 15 fps；不写入实时原图。",
                 bg=p["surface"], fg=p["muted"], font=(UI, 9), wraplength=230,
                 justify="left").pack(anchor="w", pady=(10, 0))

    def _build_analysis_page(self, page):
        p = self.palette
        wrap = tk.Frame(page, bg=p["bg"])
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure((0, 1), weight=1, uniform="analysis")
        tk.Label(wrap, text="分析当前图像", bg=p["bg"], fg=p["text"],
                 font=(UI, 11, "bold")).grid(row=0, column=0, sticky="w", columnspan=2)
        tk.Label(wrap, text="使用实时原图，或导入一张图片。所有分析入口共用当前工作图。",
                 bg=p["bg"], fg=p["muted"], font=(UI, 9)).grid(
            row=1, column=0, sticky="w", columnspan=2, pady=(4, 14))
        for index, (title, description, icon, handler) in enumerate(ANALYSIS_ENTRIES):
            row = index // 2 + 2
            column = index % 2
            card = widgets.Card(wrap, p, padding=(18, 16))
            card.grid(row=row, column=column, sticky="nsew",
                      padx=(0, 12) if column == 0 else (0, 0), pady=(0, 12))
            inner = card.body
            head = tk.Frame(inner, bg=p["surface"])
            head.pack(fill="x")
            icon_canvas = tk.Canvas(head, width=20, height=20, bg=p["surface"],
                                    highlightthickness=0, bd=0)
            icon_canvas.pack(side="left")
            widgets.draw_icon(icon_canvas, icon, 0, 0, 19, p["accent"], 1.8)
            tk.Label(head, text=title, bg=p["surface"], fg=p["text"],
                     font=(UI, 11, "bold")).pack(side="left", padx=(9, 0))
            tk.Label(inner, text=description, bg=p["surface"], fg=p["muted"],
                     font=(UI, 9), wraplength=340, justify="left").pack(
                anchor="w", pady=(10, 14))
            widgets.RoundedButton(inner, "打开", getattr(self, handler), p, height=32,
                                  bg=p["surface"]).pack(anchor="w")

    def _build_events_page(self, page):
        p = self.palette
        card = widgets.Card(page, p, padding=(16, 14))
        card.pack(fill="both", expand=True)
        inner = card.body
        top = tk.Frame(inner, bg=p["surface"])
        top.pack(fill="x", pady=(0, 10))
        tk.Label(top, text="设备与事件", bg=p["surface"], fg=p["text"],
                 font=(UI, 11, "bold")).pack(side="left")
        widgets.RoundedButton(top, "打开官方助手", self._open_zf, p, height=30,
                              bg=p["surface"]).pack(side="right")
        widgets.RoundedButton(top, "清空", self._clear_log, p, height=30,
                              bg=p["surface"]).pack(side="right", padx=(0, 8))
        widgets.RoundedButton(top, "通道摘要", self._do_snapshot, p, height=30,
                              bg=p["surface"]).pack(side="right", padx=(0, 8))
        holder = tk.Frame(inner, bg=p["surface_alt"])
        holder.pack(fill="both", expand=True)
        self.data_text = tk.Text(holder, wrap="word", borderwidth=0, highlightthickness=0,
                                 font=(MONO, 10), padx=12, pady=10, state="disabled",
                                 bg=p["surface_alt"], fg=p["text"],
                                 insertbackground=p["text"], selectbackground=p["surface_hover"])
        self.data_text.pack(fill="both", expand=True)
        tk.Label(inner, textvariable=self.status_var, bg=p["surface"], fg=p["muted"],
                 font=(UI, 9), wraplength=760, justify="left").pack(anchor="w", pady=(10, 0))

    # ------------------------------------------------------------ 页面切换

    def show_page(self, key):
        changed = key != self._page
        self._finish_page_motion()
        for page in self.pages.values():
            page.place_forget()
        page = self.pages[key]
        page.place(x=0, y=0, relwidth=1, relheight=1)
        self._page = key
        title, description = PAGES[key]
        self.page_title.set(title)
        self.page_description.set(description)
        for name, item in self.nav_buttons.items():
            item.set_selected(name == key, animate=changed)
        if changed and self._motion_enabled:
            started = time.monotonic()

            def tick():
                self._page_job = None
                progress = min(1.0, (time.monotonic() - started) / 0.26)
                offset = round(18 * (1 - theme.ease_out_back(progress)))
                try:
                    page.place_configure(x=offset)
                except tk.TclError:
                    return
                if progress < 1:
                    self._page_job = self.root.after(16, tick)
                else:
                    page.place_configure(x=0)

            tick()

    def _finish_page_motion(self):
        if self._page_job is not None:
            try:
                self.root.after_cancel(self._page_job)
            except tk.TclError:
                pass
            self._page_job = None
        if self._page in self.pages:
            self.pages[self._page].place_configure(x=0)

    # ------------------------------------------------------------ 预览

    def _hide_empty(self):
        empty = getattr(self, "empty", None)
        if empty is not None:
            try:
                empty.stop()
                empty.place_forget()
            except tk.TclError:
                pass

    def _preview_message(self, text):
        self.preview.delete("all")
        self._canvas_img = None
        self._scrollregion = None
        self._photo = None
        empty = getattr(self, "empty", None)
        if empty is not None:
            lines = [line for line in (text or "").split("\n") if line.strip()]
            empty.set(lines[0] if lines else "等待图像",
                      lines[1] if len(lines) > 1 else "连接设备，或导入一张图片")
            empty.place(x=1, y=1, relwidth=1, relheight=1, width=-2, height=-2)
            empty.start()

    def _redraw_preview_round(self, _event=None):
        if self._preview_round_job is not None:
            try:
                self.root.after_cancel(self._preview_round_job)
            except tk.TclError:
                pass
        self._preview_round_job = self.root.after(30, self._paint_preview_round)

    def _paint_preview_round(self):
        """预览容器用圆角贴图打底，画布内缩 1px，四角因此是圆角。"""
        self._preview_round_job = None
        p = self.palette
        width = self.preview_wrap.winfo_width()
        height = self.preview_wrap.winfo_height()
        if width < 8 or height < 8:
            return
        image, margin = theme.CACHE.get(self.preview_bg, width, height, 12,
                                        p["canvas"], p["surface"],
                                        shadow=(0, 0, 0, 0), outline=p["canvas_edge"])
        if image is None:
            return
        self._preview_round_image = image
        self.preview_bg.delete("all")
        self.preview_bg.create_image(-margin, -margin, anchor="nw", image=image)

    def _refresh_preview(self):
        frame = self.frame_sink.last_frame
        size = (frame[1], frame[2]) if frame and self.mode == "live" else None
        if size and size != self._auto_fit_size and self.preview.winfo_width() > 1:
            self._auto_fit_size = size
            self._zoom = max(1, min(16, int(min(self.preview.winfo_width() / size[0],
                                                self.preview.winfo_height() / size[1]))))
        if self.mode == "live" and frame is not None:
            self._hide_empty()
        if self.mode != "live":
            self._hide_empty()
        super()._refresh_preview()

    def _fit_view(self):
        w, h = self.frame_sink.last_size
        if self.mode != "live":
            w, h = getattr(self, "_src_size", (0, 0))
        if w and h:
            available_w, available_h = self.preview.winfo_width(), self.preview.winfo_height()
            self._zoom = max(1, min(16, int(min(available_w / w, available_h / h))))
            self._refresh_preview()
            self.preview.xview_moveto(0)
            self.preview.yview_moveto(0)

    # ------------------------------------------------------------ 指标

    def _update_metrics(self):
        stats = self.frame_sink.stats()
        frame = self.frame_sink.last_frame
        if self.mode != "live":
            self.frame_mode_var.set("本地图像 · 已锁定")
        elif frame:
            kind, w, h, _, _ = frame
            label = {1: "二值", 2: "灰度", 3: "RGB565"}.get(kind, "图像")
            self.frame_mode_var.set(f"{label} · {w} × {h}")
            self.chip_frames.set(numeric=stats["frames"])
            self.chip_fps.set(numeric=stats["estFps"])
            self.chip_gap.set(numeric=stats["maxGapSeconds"])
        if self.running:
            age = time.time() - self.frame_sink._last_ts if frame else float("inf")
            if age < 2:
                self.connection_pill.set("数据接收中", "live")
            elif self.sample_count:
                self.connection_pill.set(f"通道接收中 · {self.sample_count} 样本", "waiting")
            else:
                self.connection_pill.set("接收中 · 等待数据", "accent")
        else:
            self.connection_pill.set("未连接", "neutral")
        self.root.after(400, self._update_metrics)

    # ------------------------------------------------------------ 桥控制

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
        except Exception as exc:  # noqa: BLE001
            for resource in (self.source, self.tap, self.bridge):
                if resource:
                    resource.stop()
            self.source = self.tap = self.bridge = None
            self._log(f"[连接] 失败：{exc}")
            messagebox.showerror("连接失败",
                                 f"{exc}\n\n请检查输入源，以及是否已有接收程序占用端口。",
                                 parent=self.root)
            return
        self.running = True
        self.input_combo.configure(state="disabled")
        self.start_btn.set_text("断开连接")
        self.connection_pill.set("接收中 · 等待数据", "accent")
        self.empty.set("等待图像", "已连接，等待第一帧图传")
        self._save_settings()
        self._log(f"[连接] {self.input_var.get()}；AI 127.0.0.1:8765")

    def _stop_bridge(self):
        super()._stop_bridge()
        self.input_combo.configure(state="normal")
        self.start_btn.set_text("连接设备")
        self.connection_pill.set("已断开", "neutral")
        self._preview_message("等待图像\n连接设备，或导入一张图片")

    # ------------------------------------------------------------ 工作图与保存

    def _set_mode(self, mode, path, label):
        self.mode, self.work_path = mode, path
        self._auto_fit_size = None
        self._refresh_preview()
        self._fit_view()
        self.live_btn.set_enabled(True)
        self.frame_mode_var.set("本地图像 · 已锁定")
        self.show_page("vision")
        self._log(f"[模式] {label}；点击画面顶部「回到实时」恢复接收预览。")

    def _back_to_live(self):
        self.mode = "live"
        self.work_path = self.data_dir / "frames" / "latest.png"
        self._auto_fit_size = None
        self._display_raw = None
        self._photo = None
        self._src_size = (0, 0)
        self._zoom = 1
        self._preview_message("等待实时图像")
        self._refresh_preview()
        self.preview.xview_moveto(0)
        self.preview.yview_moveto(0)
        self.live_btn.set_enabled(False)
        self.frame_mode_var.set("实时图像" if self.frame_sink.last_frame else "等待实时图像")
        self.show_page("vision")
        self._log("[模式] 已回到实时图传")

    def _save_current_image(self):
        from zf_ai_bridge.image import write_png
        try:
            if self.mode == "live":
                frame = self.frame_sink.last_frame
                if frame is None:
                    messagebox.showinfo("保存原图",
                                        "还没有实时图像，请先连接设备或选择历史帧。",
                                        parent=self.root)
                    return
                _, w, h, rgb, _ = frame
                # 冻结点击时的原始像素，保存对话框内即使收到新帧也不替换。
                import tempfile
                with tempfile.TemporaryDirectory(prefix="lingxi-save-") as folder:
                    snapshot = Path(folder) / "raw.png"
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
                selected = filedialog.asksaveasfilename(
                    parent=parent, title="保存原始图像", initialfile=name,
                    defaultextension=".png", filetypes=[("PNG 图像", "*.png")])
                if not selected:
                    return
                Path(selected).write_bytes(data)
                self._log(f"[保存] 原图 → {selected}")
            else:
                selected = filedialog.askdirectory(
                    parent=parent, title=f"保存 {len(frames)} 张历史原图到文件夹")
                if not selected:
                    return
                for name, data in frames:
                    target = Path(selected) / name
                    index = 1
                    while True:
                        try:
                            # 批量导出不覆盖已有文件；保留原文件名，重名加序号。
                            with target.open("xb") as stream:
                                stream.write(data)
                            break
                        except FileExistsError:
                            target = Path(selected) / f"{Path(name).stem}_{index}.png"
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
        for path in sorted((self.data_dir / "frames").glob("frame_*.png"), reverse=True):
            try:
                frames.append((path.name, path.read_bytes()))
            except OSError:
                continue
        if not frames:
            messagebox.showinfo("历史帧", "暂无历史图像，请先连接设备并接收图传。",
                                parent=self.root)
            return
        p = self.palette
        win = self._history_window = tk.Toplevel(self.root)
        win.title("历史帧 · 预览与保存")
        win.geometry("980x660")
        win.minsize(820, 540)
        win.configure(bg=p["bg"])
        shell = tk.Frame(win, bg=p["bg"])
        shell.pack(fill="both", expand=True, padx=20, pady=18)
        head = tk.Frame(shell, bg=p["bg"])
        head.pack(fill="x", pady=(0, 14))
        tk.Label(head, text="历史帧", bg=p["bg"], fg=p["text"],
                 font=(UI, 16, "bold")).pack(side="left")
        tk.Label(head, text=f"{len(frames)} 张原图 · Ctrl / Shift 多选 · 双击锁定查看",
                 bg=p["bg"], fg=p["muted"], font=(UI, 9)).pack(side="left", padx=(12, 0), pady=(6, 0))

        body = tk.Frame(shell, bg=p["bg"])
        body.pack(fill="both", expand=True)
        list_card = widgets.Card(body, p, padding=(12, 12))
        list_card.pack(side="left", fill="both", expand=True, padx=(0, 14))
        listing = list_card.body
        tree = ttk.Treeview(listing, show="tree", selectmode="extended", height=16)
        tree.column("#0", width=300, stretch=True)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        preview_card = widgets.Card(body, p, padding=(14, 14), radius=14)
        preview_card.pack(side="right", fill="both")
        preview_holder = tk.Frame(preview_card.body, bg=p["surface_alt"])
        preview_holder.pack(fill="both", expand=True)
        preview = tk.Label(preview_holder, anchor="center", bg=p["surface_alt"],
                           fg=p["muted"], font=(UI, 9))
        preview.pack(fill="both", expand=True)
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
                    factor = max(1, (max(img.width(), img.height()) + 359) // 360)
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
                target = self.data_dir / "frames" / "history_selected.png"
                target.write_bytes(data)
                load_png_rgb(target)
                self._history_name = name
                self._set_mode("history", target, f"历史帧 · {name}")
                win.destroy()
            except (OSError, ValueError) as exc:
                messagebox.showerror("无法打开历史帧", str(exc), parent=win)

        tree.bind("<<TreeviewSelect>>", show_preview)
        tree.bind("<Double-1>", lock)
        buttons = tk.Frame(shell, bg=p["bg"])
        buttons.pack(fill="x", pady=(14, 0))
        widgets.RoundedButton(buttons, "锁定查看", lock, p, kind="primary", height=34,
                              bg=p["bg"]).pack(side="left", padx=(0, 8))
        widgets.RoundedButton(buttons, "保存选中…", lambda: self._export_history_frames(
            [frames[int(i)] for i in tree.selection()], parent=win), p, height=34,
            bg=p["bg"]).pack(side="left", padx=(0, 8))
        widgets.RoundedButton(buttons, "保存全部…", lambda: self._export_history_frames(
            frames, parent=win), p, height=34, bg=p["bg"]).pack(side="left")
        widgets.RoundedButton(buttons, "关闭", win.destroy, p, height=34,
                              bg=p["bg"]).pack(side="right")
        # 持有快照和控件，方便自动化检查真实按钮流程。
        win.frame_list = tree
        tree.selection_set("0")
        tree.focus("0")
        show_preview()

    # ------------------------------------------------------------ 杂项

    def _toggle_motion(self):
        self._motion_enabled = self.motion_var.get()
        theme.MOTION = self._motion_enabled
        self._finish_page_motion()
        self._save_desktop_preferences()

    def _on_overlay_toggle(self):
        self.frame_sink.show_edges = self.edge_var.get()
        self.frame_sink.show_center = self.center_var.get()
        self.frame_sink.save_annotated = self.edge_var.get() or self.center_var.get()
        self._refresh_preview()

    def _log(self, msg):
        # 图传高帧率时避免持续刷日志；数据统计仍每帧更新。
        if msg.startswith("[图传]"):
            if time.monotonic() - self._last_frame_notice < 5:
                return
            self._last_frame_notice = time.monotonic()
        text = getattr(self, "data_text", None)
        if text is None:
            return
        text.configure(state="normal")
        super()._log(msg)
        if int(text.index("end-1c").split(".")[0]) > 1000:
            text.delete("1.0", "200.0")
        text.configure(state="disabled")

    def _clear_log(self):
        self.data_text.configure(state="normal")
        self.data_text.delete("1.0", "end")
        self.data_text.configure(state="disabled")

    def _open_zf(self):
        selected = self.prefs.get("official_exe", "")
        if not selected or not Path(selected).is_file():
            selected = filedialog.askopenfilename(parent=self.root, title="选择官方逐飞助手 EXE",
                                                  filetypes=[("Windows应用", "*.exe")])
        if selected:
            import subprocess
            try:
                subprocess.Popen([selected], cwd=str(Path(selected).parent))
                self.prefs["official_exe"] = selected
                self._save_desktop_preferences()
            except OSError as exc:
                messagebox.showerror("无法启动", str(exc), parent=self.root)

    def _about(self):
        messagebox.showinfo("关于灵犀平台",
                            f"{PRODUCT_NAME}\n{VERSION}\n\n逐飞 V2 图传 · 原始像素 · 四点逆透视\n"
                            f"基于组内 zf-ai-bridge，沿用 GPLv3 许可。\n\n"
                            f"数据目录：{self.data_dir}\n\n逆透视工具待实车标定验证。",
                            parent=self.root)

    # ------------------------------------------------------------ 逆透视入口

    def _ipm_current_frame(self):
        frame = self.frame_sink.last_frame if self.mode == "live" else None
        if frame:
            return frame[:4]
        snapshot = getattr(self, "_display_raw", None)
        if snapshot:
            w, h, rgb = snapshot
            return (0, w, h, rgb)
        return None

    def _open_advanced_perspective(self):
        existing = getattr(self, "_ipm_window", None)
        if existing and not existing._closed:
            existing.win.lift()
            return
        frame = self._ipm_current_frame()
        if not frame:
            messagebox.showinfo("高级逆透视", "请先接收图传，或导入一张188×120干净原图。",
                                parent=self.root)
            return
        try:
            kind, w, h, rgb = frame
            window = AdvancedPerspectiveWindow(
                self.root, rgb, w, h, kind, self.data_dir / "frames",
                frame_provider=lambda: self.frame_sink.last_frame[:4] if self.frame_sink.last_frame else None,
                palette=self.palette, capture_provider=self._ipm_current_frame)
            self._ipm_window = window
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法打开高级标定", str(exc), parent=self.root)

    def _on_close(self):
        self._finish_page_motion()
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
