"""灵犀平台设计系统：双主题令牌、抗锯齿圆角贴图与 ttk 样式。

设计约束：
  · 不新增第三方依赖——圆角、渐变、描边由本模块用标准库生成 PNG，再交给 Tk；
  · 贴图按（尺寸、圆角、配色）缓存，主题切换后自动换一套；
  · 只做视觉与手感，不改任何图传、标定或协议行为。
"""
from __future__ import annotations

import math
import struct
import zlib
import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------- 基础令牌

SPACING = {"xs": 4, "sm": 6, "md": 10, "lg": 16, "xl": 22, "xxl": 30}
RADIUS = {"card": 16, "panel": 13, "button": 10, "input": 9, "chip": 999}

FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Cascadia Mono"

# 全局动效开关：界面设置里可一键关闭，所有补间直接落到终态。
MOTION = True

# 界面缩放：DPI 感知后按系统缩放比例放大像素尺寸（字体走 point 单位自动跟随）。
UI_SCALE = 1.0


def set_scale(scale: float) -> None:
    global UI_SCALE
    UI_SCALE = max(0.75, min(3.0, float(scale)))


def sp(value: float) -> int:
    """设计像素 → 当前 DPI 下的实际像素。"""
    return int(round(value * UI_SCALE))


def blend_palette(first: dict, second: dict, t: float) -> dict:
    """两套主题按比例混合，用于主题切换的过渡帧。"""
    result = dict(second)
    for key, value in first.items():
        target = second.get(key)
        if (isinstance(value, str) and isinstance(target, str)
                and value.startswith("#") and target.startswith("#")):
            result[key] = mix(value, target, t)
    return result

TYPE = {
    "display": (FONT_UI, 20, "bold"),
    "title": (FONT_UI, 14, "bold"),
    "heading": (FONT_UI, 11, "bold"),
    "body": (FONT_UI, 10),
    "small": (FONT_UI, 9),
    "mono": (FONT_MONO, 9),
    "mono_big": (FONT_MONO, 13, "bold"),
}

# 浅色：中性冷灰底 + 薄荷青强调，大面积留白、细描边、极弱阴影
LIGHT = dict(
    name="light",
    bg="#F2F4F7",
    sidebar="#F8F9FB",
    surface="#FAFAFC",
    surface_alt="#F6F8FA",
    surface_hover="#F3F6FA",
    surface_press="#EAEFF5",
    border="#E1E6ED",
    border_soft="#EDF0F5",
    text="#11161D",
    text_secondary="#49525F",
    muted="#647084",
    accent="#2F6FED",
    accent_hover="#1F5AD9",
    accent_press="#1746B0",
    accent_soft="#E7EEFE",
    accent_text="#FFFFFF",
    on_accent="#FFFFFF",
    success="#22A06B",
    success_soft="#E6F4EC",
    warn="#C2740A",
    warn_soft="#FBF1DF",
    danger="#D64545",
    danger_soft="#FBEAEA",
    canvas="#E7EBF1",
    canvas_edge="#DCE2EA",
    overlay="#FFFFFF",
    shadow=(17, 24, 39, 0.075),
    shadow_strong=(17, 24, 39, 0.12),
    gradient_alpha=0.0,
    grid="#D7DEE7",
    # 液态玻璃：浅色主题是"白色磨砂"，光晕很淡
    glass_tint="#FFFFFF",
    glass_alpha=0.58,
    glass_edge_alpha=0.85,
    glass_highlight=0.10,
    glass_inner_shadow=0.10,
    aurora=((0.16, 0.04, 0.64, "#A9C6FF", 0.40),
            (0.90, 0.14, 0.56, "#C6BEF7", 0.34),
            (0.50, 1.02, 0.80, "#D9E6FF", 0.30)),
)

# 深色：蓝调近黑，层次靠 surface 逐级提亮，薄荷青提亮强调
DARK = dict(
    name="dark",
    bg="#0C1015",
    sidebar="#1A1F24",
    surface="#1C2126",
    surface_alt="#171B20",
    surface_hover="#262B33",
    surface_press="#2E343D",
    border="#2A313C",
    border_soft="#232932",
    text="#E7ECF3",
    text_secondary="#AFBACA",
    muted="#7B8798",
    accent="#5AA2FF",
    accent_hover="#7BB6FF",
    accent_press="#3F86E6",
    accent_soft="#17263F",
    accent_text="#04241B",
    on_accent="#04241B",
    success="#4CCB8F",
    success_soft="#12291F",
    warn="#E0A441",
    warn_soft="#33280F",
    danger="#F0715F",
    danger_soft="#3A1C1A",
    canvas="#090D12",
    canvas_edge="#1B222C",
    overlay="#1A212B",
    shadow=(0, 0, 0, 0.28),
    shadow_strong=(0, 0, 0, 0.42),
    gradient_alpha=0.035,
    grid="#1E2632",
    # 液态玻璃：深色主题是"暗色玻璃 + 边缘高光"，光晕更明显
    glass_tint="#E8EEF6",
    glass_alpha=0.075,
    glass_edge_alpha=0.18,
    glass_highlight=0.075,
    glass_inner_shadow=0.18,
    aurora=((0.10, 0.02, 0.66, "#1C4A86", 0.92),
            (0.92, 0.12, 0.58, "#2B2F79", 0.82),
            (0.44, 1.05, 0.88, "#103A6E", 0.66)),
)

THEMES = {"light": LIGHT, "dark": DARK}


# ---------------------------------------------------------------- 颜色工具

def to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v))) for v in rgb)


def mix(first: str, second: str, t: float) -> str:
    """按比例混合两色；t=0 取 first，t=1 取 second。"""
    a, b = to_rgb(first), to_rgb(second)
    return to_hex(a[i] + (b[i] - a[i]) * t for i in range(3))


def shade(color: str, amount: float) -> str:
    return mix(color, "#000000" if amount < 0 else "#ffffff", abs(amount))


def luminance(color: str) -> float:
    r, g, b = (v / 255 for v in to_rgb(color))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(first: str, second: str) -> float:
    a, b = luminance(first), luminance(second)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------- 贴图渲染

def _ppm_bytes(width: int, height: int, pixels: bytearray) -> bytes:
    """PPM(P6)：Tk 原生解析，无需压缩，作为纹理传输格式开销最低。"""
    return b"P6\n%d %d\n255\n" % (width, height) + bytes(pixels)


def _row_color(fill, gradient, t):
    return (int(fill[0] + (gradient[0] - fill[0]) * t),
            int(fill[1] + (gradient[1] - fill[1]) * t),
            int(fill[2] + (gradient[2] - fill[2]) * t))


def _blend(bg, color, coverage: float):
    return (int(bg[0] + (color[0] - bg[0]) * coverage),
            int(bg[1] + (color[1] - bg[1]) * coverage),
            int(bg[2] + (color[2] - bg[2]) * coverage))


def render_surface(width: int, height: int, radius: int, fill: str, bg: str, *,
                   shadow=(17, 24, 39, 0.08), outline: str | None = None,
                   gradient: str | None = None) -> tuple[bytes, int, int, int]:
    """渲染圆角面板贴图（抗锯齿边 + 极轻渐变 + 柔和阴影）。

    返回 (png, 总宽, 总高, 外边距)。绘制时把图片左上角对齐到
    (x - margin, y - margin)，阴影就与背景无缝融合。

    性能约束：逐像素只处理边缘与角，中间整段走切片填充，
    一张 340×440 的卡片约 10ms，可缓存复用。
    """
    if width <= 0 or height <= 0:
        return b"", 0, 0, 0
    fill_rgb = to_rgb(fill)
    grad_rgb = to_rgb(gradient) if gradient else fill_rgb
    bg_rgb = to_rgb(bg)
    outline_rgb = to_rgb(outline) if outline else None
    sr, sg, sb, strength = shadow
    margin = max(0, min(int(round(radius * 0.9)), 14)) if strength > 0 else 0
    total_w, total_h = width + margin * 2, height + margin * 2

    # --- 卡片本体（不含阴影）---
    r = min(float(radius), width / 2.0 - .01, height / 2.0 - .01)
    half_w, half_h = width / 2.0, height / 2.0
    lo = max(0, int(math.ceil(r)))
    hi = min(width, int(width - r) + 1)
    band = max(0, int(math.ceil(r)) + 1)
    card = bytearray(width * height * 3)
    for y in range(height):
        base = y * width * 3
        yy = y + 0.5
        qy = abs(yy - half_h) - (half_h - r)
        t = min(1.0, max(0.0, y / float(height)))
        row = _row_color(fill_rgb, grad_rgb, t)
        if qy <= 0.0:
            # 纵向内部：中间整段同色，只有左右圆角带要逐像素
            if hi > lo:
                card[base + lo * 3:base + hi * 3] = bytes(row) * (hi - lo)
            cols = list(range(0, min(band, lo))) + list(range(max(hi, width - band), width))
        else:
            cols = range(width)
        for x in cols:
            xx = x + 0.5
            qx = abs(xx - half_w) - (half_w - r)
            ax, ay = max(qx, 0.0), max(qy, 0.0)
            if ax and ay:
                d = math.hypot(ax, ay) + min(max(qx, qy), 0.0) - r
            else:
                d = ax + ay + min(max(qx, qy), 0.0) - r
            i = base + x * 3
            if d <= -1.0:
                card[i], card[i + 1], card[i + 2] = row
            elif d <= 0.0:
                coverage = min(1.0, max(0.0, 0.5 - d))
                color = outline_rgb if (outline_rgb is not None and d > -1.35) else row
                card[i], card[i + 1], card[i + 2] = _blend(bg_rgb, color, coverage)
            else:
                card[i], card[i + 1], card[i + 2] = bg_rgb

    if margin <= 0:
        return _ppm_bytes(width, height, card), width, height, 0

    # --- 合成：卡片整行贴入，阴影只在外扩边带逐像素计算 ---
    pixels = bytearray(bytes(bg_rgb) * (total_w * total_h))
    for y in range(height):
        dst = ((y + margin) * total_w + margin) * 3
        pixels[dst:dst + width * 3] = card[y * width * 3:(y + 1) * width * 3]
    span = float(margin)

    def shade(tx, ty, dx, dy):
        qx = abs(dx - half_w) - (half_w - r)
        qy = abs(dy - half_h) - (half_h - r)
        depth = max(qx, qy)
        if depth <= 0:
            return
        fade = max(0.0, 1.0 - depth / span)
        alpha = strength * fade * fade
        i = (ty * total_w + tx) * 3
        pixels[i] = int(bg_rgb[0] + (sr - bg_rgb[0]) * alpha)
        pixels[i + 1] = int(bg_rgb[1] + (sg - bg_rgb[1]) * alpha)
        pixels[i + 2] = int(bg_rgb[2] + (sb - bg_rgb[2]) * alpha)

    for ty in list(range(margin)) + list(range(margin + height, total_h)):
        for tx in range(total_w):
            shade(tx, ty, tx - margin + 0.5, ty - margin + 0.5)
    for y in range(height):
        ty = y + margin
        for tx in list(range(margin)) + list(range(margin + width, total_w)):
            shade(tx, ty, tx - margin + 0.5, y + 0.5)

    return _ppm_bytes(total_w, total_h, pixels), total_w, total_h, margin


class SurfaceCache:
    """按（尺寸、圆角、配色）缓存圆角贴图，避免每帧重算。"""

    def __init__(self) -> None:
        self._images: dict[tuple, tuple[tk.PhotoImage, int]] = {}

    def get(self, master: tk.Misc, width: int, height: int, radius: int, fill: str,
            bg: str, *, shadow=None, outline=None, gradient=None) -> tuple[tk.PhotoImage, int]:
        key = (width, height, radius, fill, bg, shadow, outline, gradient)
        cached = self._images.get(key)
        if cached is not None:
            return cached
        png, _w, _h, margin = render_surface(
            width, height, radius, fill, bg,
            shadow=shadow if shadow is not None else (0, 0, 0, 0),
            outline=outline, gradient=gradient)
        image = tk.PhotoImage(master=master, data=png, format="ppm") if png else None
        self._images[key] = (image, margin)
        return image, margin


CACHE = SurfaceCache()


def find_palette(widget, default=None):
    """沿 master 链找最近的主题令牌，供二级窗口里的自绘控件使用。"""
    node = widget
    while node is not None:
        palette = getattr(node, "palette", None)
        if isinstance(palette, dict) and "surface" in palette:
            return palette
        node = getattr(node, "master", None)
    return default or LIGHT


def _recolor(widget, mapping) -> None:
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
        _recolor(child, mapping)


def restyle_tree(widget: tk.Misc, old: dict | None, new: dict) -> None:
    """主题切换：先按色值映射纠正原生控件，再让自绘组件自己换皮。"""
    if old is not None:
        mapping = {}
        for key, value in old.items():
            target = new.get(key)
            if isinstance(value, str) and len(value) == 7 and isinstance(target, str):
                mapping[value.lower()] = target
        _recolor(widget, mapping)

    def walk(node):
        for child in node.winfo_children():
            apply = getattr(child, "apply_palette", None)
            if callable(apply):
                try:
                    apply(new)
                except tk.TclError:
                    pass
            walk(child)

    walk(widget)


# ---------------------------------------------------------------- ttk 样式

def apply_ttk(style: ttk.Style, p: dict) -> None:
    """把双主题令牌灌进 ttk；形状类效果交给自绘组件，这里只负责配色与内边距。"""
    style.theme_use("clam")
    font_body = TYPE["body"]
    style.configure(".", background=p["bg"], foreground=p["text"], font=font_body,
                    borderwidth=0, focuscolor=p["bg"])

    style.configure("TFrame", background=p["bg"])
    style.configure("Surface.TFrame", background=p["surface"])
    style.configure("Alt.TFrame", background=p["surface_alt"])
    style.configure("Sidebar.TFrame", background=p["sidebar"])
    style.configure("Card.TFrame", background=p["surface"])
    style.configure("Bare.TFrame", background=p["surface"])

    style.configure("TLabel", background=p["bg"], foreground=p["text"], font=font_body)
    style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"], font=TYPE["small"])
    style.configure("BodyMuted.TLabel", background=p["bg"], foreground=p["muted"], font=font_body)
    style.configure("Title.TLabel", background=p["bg"], foreground=p["text"], font=TYPE["display"])
    style.configure("Heading.TLabel", background=p["bg"], foreground=p["text"], font=TYPE["heading"])
    style.configure("Card.TLabel", background=p["surface"], foreground=p["text"], font=font_body)
    style.configure("CardHeading.TLabel", background=p["surface"], foreground=p["text"],
                    font=TYPE["heading"])
    style.configure("CardMuted.TLabel", background=p["surface"], foreground=p["muted"],
                    font=TYPE["small"])
    style.configure("Sidebar.TLabel", background=p["sidebar"], foreground=p["text"],
                    font=(FONT_UI, 17, "bold"))
    style.configure("SidebarMuted.TLabel", background=p["sidebar"], foreground=p["muted"],
                    font=(FONT_UI, 8))
    style.configure("Metric.TLabel", background=p["surface_alt"], foreground=p["text"],
                    font=TYPE["mono_big"])
    style.configure("MetricMuted.TLabel", background=p["surface_alt"], foreground=p["muted"],
                    font=(FONT_UI, 8))

    # 输入类：扁平、无浮雕、浅底、细边
    style.configure("TEntry", fieldbackground=p["surface_alt"], background=p["surface_alt"],
                    foreground=p["text"], insertcolor=p["text"], bordercolor=p["border"],
                    lightcolor=p["surface_alt"], darkcolor=p["surface_alt"],
                    padding=(9, 7), borderwidth=1, relief="flat", selectbackground=p["accent_soft"],
                    selectforeground=p["text"])
    style.map("TEntry",
              bordercolor=[("focus", p["accent"])],
              fieldbackground=[("disabled", p["bg"])],
              foreground=[("disabled", p["muted"])])

    style.configure("TCombobox", fieldbackground=p["surface_alt"], background=p["surface_alt"],
                    foreground=p["text"], arrowcolor=p["muted"], bordercolor=p["border"],
                    lightcolor=p["surface_alt"], darkcolor=p["surface_alt"],
                    padding=(9, 6), borderwidth=1, relief="flat", arrowsize=13)
    style.map("TCombobox",
              fieldbackground=[("readonly", p["surface_alt"]), ("disabled", p["bg"])],
              foreground=[("readonly", p["text"]), ("disabled", p["muted"])],
              bordercolor=[("focus", p["accent"])],
              arrowcolor=[("active", p["accent"])])
    style.configure("TSpinbox", fieldbackground=p["surface_alt"], background=p["surface_alt"],
                    foreground=p["text"], arrowcolor=p["muted"], bordercolor=p["border"],
                    lightcolor=p["surface_alt"], darkcolor=p["surface_alt"],
                    padding=(7, 5), borderwidth=1, relief="flat", arrowsize=12)

    # 滚动条：细条、无箭头、圆角感靠留白实现
    for orient, name in (("vertical", "Vertical.TScrollbar"), ("horizontal", "Horizontal.TScrollbar")):
        style.configure(name, background=p["border"], troughcolor=p["surface_alt"],
                        bordercolor=p["surface_alt"], lightcolor=p["border"], darkcolor=p["border"],
                        arrowcolor=p["muted"], relief="flat", borderwidth=0,
                        width=9 if orient == "vertical" else 0)
        style.map(name, background=[("active", p["muted"])])

    style.configure("TNotebook", background=p["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"],
                    padding=(14, 8), borderwidth=0, font=font_body)
    style.map("TNotebook.Tab",
              background=[("selected", p["surface"]), ("active", p["surface_alt"])],
              foreground=[("selected", p["text"]), ("active", p["text_secondary"])])

    style.configure("TCheckbutton", background=p["bg"], foreground=p["text"],
                    padding=2, font=font_body, indicatorcolor=p["surface_alt"])
    style.map("TCheckbutton",
              background=[("active", p["bg"])],
              indicatorcolor=[("selected", p["accent"]), ("active", p["border"])],
              foreground=[("disabled", p["muted"])])
    style.configure("Card.TCheckbutton", background=p["surface"], foreground=p["text"],
                    padding=2, font=font_body, indicatorcolor=p["surface_alt"])
    style.map("Card.TCheckbutton",
              background=[("active", p["surface"])],
              indicatorcolor=[("selected", p["accent"]), ("active", p["border"])],
              foreground=[("disabled", p["muted"])])

    style.configure("TScale", background=p["surface"], troughcolor=p["surface_alt"],
                    bordercolor=p["surface"], lightcolor=p["surface"], darkcolor=p["surface"])
    style.configure("TRadiobutton", background=p["surface"], foreground=p["text"], font=font_body)
    style.map("TRadiobutton", background=[("active", p["surface"])])

    style.configure("Treeview", background=p["surface"], fieldbackground=p["surface"],
                    foreground=p["text"], borderwidth=0, rowheight=28, font=font_body)
    style.configure("Treeview.Heading", background=p["surface_alt"], foreground=p["muted"],
                    borderwidth=0, font=TYPE["small"], padding=(8, 6))
    style.map("Treeview", background=[("selected", p["accent_soft"])],
              foreground=[("selected", p["text"])])

    style.configure("TButton", background=p["surface_alt"], foreground=p["text"], borderwidth=0,
                    padding=(11, 8), font=font_body, relief="flat")
    style.map("TButton", background=[("active", p["surface_hover"]), ("disabled", p["surface_alt"])],
              foreground=[("disabled", p["muted"])])
    style.configure("Primary.TButton", background=p["accent"], foreground=p["on_accent"])
    style.map("Primary.TButton", background=[("active", p["accent_hover"])],
              foreground=[("active", p["on_accent"])])

    style.configure("TSeparator", background=p["border"])


# ---------------------------------------------------------------- 缓动动画

def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_back(t: float) -> float:
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


class Animator:
    """基于 after() 的轻量补间；统一取消，避免销毁后回调报错。"""

    def __init__(self, widget: tk.Misc) -> None:
        self.widget = widget
        self._job = None

    def cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def run(self, duration_ms: int, on_frame, *, easing=ease_out_cubic, on_done=None) -> None:
        self.cancel()
        if not MOTION:
            try:
                on_frame(1.0, 1.0)
            except tk.TclError:
                pass
            if on_done:
                on_done()
            return
        duration = max(1, int(duration_ms))
        started = [None]

        def tick():
            import time
            now = time.monotonic()
            if started[0] is None:
                started[0] = now
            progress = min(1.0, (now - started[0]) * 1000.0 / duration)
            try:
                on_frame(easing(progress), progress)
            except tk.TclError:
                self._job = None
                return
            if progress < 1.0:
                self._job = self.widget.after(16, tick)
            else:
                self._job = None
                if on_done:
                    on_done()

        tick()


# ---------------------------------------------------------------- 液态玻璃

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    PIL_AVAILABLE = True
except ImportError:            # 无 Pillow 时退回实色面板，界面依然可用
    PIL_AVAILABLE = False


def spring(t: float, *, damping: float = 1.05, frequency: float = 7.5) -> float:
    """iOS 风格弹簧缓动：约 6% 过冲后收敛。t 是 0..1 的归一化进度。"""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    seconds = t * 0.6
    return 1.0 - math.exp(-damping * frequency * seconds) * math.cos(frequency * seconds)


def _image_bytes(image) -> bytes:
    import io
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class Backdrop:
    """氛围背景：低频径向光晕。玻璃面板按位置采样它，视觉上像透出背景。"""

    def __init__(self) -> None:
        self._key = None
        self.image = None
        self.photo = None
        self.width = self.height = 0

    def build(self, master, palette: dict, width: int, height: int):
        width, height = max(2, int(width)), max(2, int(height))
        key = (palette["name"], width, height)
        if key == self._key and self.photo is not None:
            return self.photo
        self._key = key
        self.width, self.height = width, height
        if not PIL_AVAILABLE:
            self.image, self.photo = None, None
            return None
        scale = 0.28
        sw, sh = max(96, int(width * scale)), max(64, int(height * scale))
        base = Image.new("RGB", (sw, sh), to_rgb(palette["bg"]))
        for rx, ry, rs, color, strength in palette["aurora"]:
            size = max(16, int(rs * max(sw, sh)))
            mask = Image.radial_gradient("L").resize((size, size), Image.BILINEAR)
            mask = mask.point(lambda v: int((255 - v) * strength))
            glow = Image.new("RGB", (size, size), to_rgb(color))
            base.paste(glow, (int(rx * sw - size / 2), int(ry * sh - size / 2)), mask)
        base = base.filter(ImageFilter.GaussianBlur(9)).resize((width, height), Image.BILINEAR)
        self.image = base
        self.photo = tk.PhotoImage(master=master, data=_image_bytes(base), format="png")
        return self.photo

    def sample(self, x: float, y: float, width: float, height: float, fallback: str) -> str:
        """取某区域的平均背景色；无背景图时回退主题底色。"""
        if self.image is None:
            return fallback
        left = max(0, min(self.width - 1, int(x)))
        top = max(0, min(self.height - 1, int(y)))
        right = max(left + 1, min(self.width, int(x + width)))
        bottom = max(top + 1, min(self.height, int(y + height)))
        crop = self.image.crop((left, top, right, bottom)).resize((1, 1), Image.BOX)
        return to_hex(crop.getpixel((0, 0)))


BACKDROP = Backdrop()


def glass_png(width: int, height: int, radius: int, palette: dict, base_hex: str, *,
              tint: str | None = None, alpha: float | None = None,
              edge_alpha: float | None = None) -> bytes | None:
    """玻璃面板贴图：半透明底色 + 边缘高光 + 顶部内高光 + 底部内阴影。

    tint/alpha 可覆盖主题默认值，用来做"主色玻璃"（主按钮、选中态）。
    """
    if not PIL_AVAILABLE or width < 6 or height < 6:
        return None
    w, h = int(width), int(height)
    r = int(max(2, min(radius, w / 2 - 1, h / 2 - 1)))
    ss = 3
    tint_rgb = to_rgb(tint) if tint else to_rgb(palette["glass_tint"])
    strength = palette["glass_alpha"] if alpha is None else alpha
    edge_strength = palette["glass_edge_alpha"] if edge_alpha is None else edge_alpha
    base_rgb = to_rgb(base_hex)
    fill = tuple(int(base_rgb[i] + (tint_rgb[i] - base_rgb[i]) * strength) for i in range(3))
    img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1],
                                          radius=r * ss, fill=fill + (255,))
    img = img.resize((w, h), Image.LANCZOS)
    shape = img.split()[3]
    # 顶部约 45% 的白色渐变高光，只落在圆角形状内
    highlight = Image.new("L", (1, h))
    for y in range(h):
        weight = max(0.0, 1.0 - y / max(1.0, h * 0.45))
        highlight.putpixel((0, y), int(255 * palette["glass_highlight"] * weight))
    glow = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    glow.putalpha(ImageChops.multiply(highlight.resize((w, h), Image.BILINEAR), shape))
    img = Image.alpha_composite(img, glow)
    # 1px 边缘高光环
    edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=r,
        outline=(255, 255, 255, int(255 * edge_strength)), width=1)
    img = Image.alpha_composite(img, edge)
    # 底部内阴影，让面板看起来有厚度
    inner = Image.new("L", (1, h))
    for y in range(h):
        weight = max(0.0, (y - h * 0.72) / max(1.0, h * 0.28))
        inner.putpixel((0, y), int(255 * palette["glass_inner_shadow"] * weight))
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shade.putalpha(ImageChops.multiply(inner.resize((w, h), Image.BILINEAR), shape))
    return _image_bytes(Image.alpha_composite(img, shade))


def glass_fill(palette: dict, base_hex: str, *, tint: str | None = None,
               alpha: float | None = None) -> str:
    """玻璃面板内部纯色：面板内的 Frame/Label 用它，和贴图内部完全一致。"""
    tint_color = tint or palette["glass_tint"]
    strength = palette["glass_alpha"] if alpha is None else alpha
    return mix(base_hex, tint_color, strength)


def glow_png(size: int, color: str, alpha: float = 0.30) -> bytes | None:
    """柔和径向光晕；叠在深色底上像玻璃后面的光。"""
    if not PIL_AVAILABLE:
        return None
    size = int(max(8, size))
    mask = Image.radial_gradient("L").resize((size, size), Image.BILINEAR)
    mask = mask.point(lambda v: int((255 - v) * alpha))
    glow = Image.new("RGBA", (size, size), to_rgb(color) + (0,))
    glow.putalpha(mask)
    return _image_bytes(glow)

