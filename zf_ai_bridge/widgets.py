"""灵犀自绘组件库：圆角卡片、状态胶囊、按钮、导航项、分段控件、滑块、空态。

全部基于 Tk Canvas 绘制：ttk 在 clam 主题下只有矩形与浮雕，无法做出圆角、
渐变和位移反馈，所以关键交互组件自己画，其余控件仍用 ttk 并统一配色。
"""
from __future__ import annotations

import math
import tkinter as tk

from . import theme
from .theme import FONT_MONO, FONT_UI, RADIUS, TYPE, CACHE, mix, sp


# ---------------------------------------------------------------- 文本度量

_FONT_CACHE: dict[tuple, "tk.font.Font"] = {}


def measure_text(text: str, font) -> int:
    """用 Tk 字体度量真实文字宽度，避免按字数估算导致的错位。"""
    import tkinter.font as tkfont
    key = tuple(font)
    handle = _FONT_CACHE.get(key)
    if handle is None:
        handle = tkfont.Font(font=font)
        _FONT_CACHE[key] = handle
    return handle.measure(text or "")


# ---------------------------------------------------------------- 矢量图标

def draw_icon(canvas: tk.Canvas, name: str, x: float, y: float, size: float,
              color: str, width: float = 1.8, tags=()) -> None:
    """在 24×24 网格上绘制线条图标，原点为 (x, y) 左上角。"""
    size = sp(size)
    s = size / 24.0

    def P(px, py):
        return x + px * s, y + py * s

    def line(points, w=width):
        flat = []
        for px, py in points:
            flat.extend(P(px, py))
        canvas.create_line(*flat, fill=color, width=w, capstyle="round",
                           joinstyle="round", tags=tags)

    def circle(cx, cy, r, fill="", w=width):
        a, b = P(cx - r, cy - r)
        c, d = P(cx + r, cy + r)
        canvas.create_oval(a, b, c, d, outline=color if not fill else "",
                           fill=fill, width=w, tags=tags)

    def dot(cx, cy, r, fill=None):
        a, b = P(cx - r, cy - r)
        c, d = P(cx + r, cy + r)
        canvas.create_oval(a, b, c, d, fill=fill or color, outline="", tags=tags)

    def box(x0, y0, x1, y1, r=3, w=width):
        """圆角矩形描边（用四条线 + 四个弧近似）。"""
        a, b = P(x0 + r, y0)
        c, d = P(x1 - r, y0)
        canvas.create_line(a, b, c, d, fill=color, width=w, capstyle="round", tags=tags)
        a, b = P(x1, y0 + r)
        c, d = P(x1, y1 - r)
        canvas.create_line(a, b, c, d, fill=color, width=w, capstyle="round", tags=tags)
        a, b = P(x1 - r, y1)
        c, d = P(x0 + r, y1)
        canvas.create_line(a, b, c, d, fill=color, width=w, capstyle="round", tags=tags)
        a, b = P(x0, y1 - r)
        c, d = P(x0, y0 + r)
        canvas.create_line(a, b, c, d, fill=color, width=w, capstyle="round", tags=tags)
        for cx, cy, start in ((x0 + r, y0 + r, 180), (x1 - r, y0 + r, 270),
                              (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90)):
            a, b = P(cx - r, cy - r)
            c, d = P(cx + r, cy + r)
            canvas.create_arc(a, b, c, d, start=start, extent=90, style="arc",
                              outline=color, width=w, tags=tags)

    if name == "vision":
        box(2.5, 6.5, 21.5, 18.5, r=3)
        circle(12, 12.5, 3.6)
        dot(12, 3.6, 1.2)
    elif name == "analysis":
        box(3, 3.5, 21, 20.5, r=3)
        line([(7.5, 16.5), (11, 12), (14.5, 15), (18, 8.5)], 1.9)
    elif name == "events":
        for i, yy in enumerate((6.5, 12, 17.5)):
            line([(4, yy), (10 + i * 2.5, yy)])
            dot(18.5 if i != 1 else 16, yy, 1.5)
    elif name == "folder":
        line([(3, 18.5), (3, 6.5), (9.5, 6.5), (11.5, 9), (21, 9), (21, 18.5), (3, 18.5)])
    elif name == "info":
        circle(12, 12, 8.6)
        line([(12, 11), (12, 16.5)], 2.0)
        dot(12, 7.8, 1.25)
    elif name == "sun":
        circle(12, 12, 4.2)
        for angle in range(0, 360, 45):
            r1, r2 = 7.2, 9.6
            a1 = math.radians(angle)
            line([(12 + r1 * math.cos(a1), 12 + r1 * math.sin(a1)),
                  (12 + r2 * math.cos(a1), 12 + r2 * math.sin(a1))], 1.7)
    elif name == "moon":
        a, b = P(4.5, 4.5)
        c, d = P(19.5, 19.5)
        canvas.create_arc(a, b, c, d, start=60, extent=250, style="arc",
                          outline=color, width=width, tags=tags)
        a, b = P(9.5, 3.5)
        c, d = P(22, 16)
        canvas.create_arc(a, b, c, d, start=200, extent=100, style="arc",
                          outline=color, width=width, tags=tags)
    elif name == "live":
        dot(8, 12, 3.2)
        line([(13.5, 12), (20, 12)])
        line([(16.5, 8.6), (20, 12), (16.5, 15.4)])
    elif name in ("zoom_in", "zoom_out"):
        circle(10.5, 10.5, 6.2)
        line([(15.2, 15.2), (20.5, 20.5)], 1.9)
        line([(7.5, 10.5), (13.5, 10.5)], 1.6)
        if name == "zoom_in":
            line([(10.5, 7.5), (10.5, 13.5)], 1.6)
    elif name == "fit":
        for x0, y0, dx, dy in ((3, 3, 1, 1), (21, 3, -1, 1), (3, 21, 1, -1), (21, 21, -1, -1)):
            line([(x0 + dx * 5, y0), (x0, y0), (x0, y0 + dy * 5)], 1.8)
    elif name == "calibrate":
        circle(12, 12, 6.4)
        line([(12, 3), (12, 8)])
        line([(12, 16), (12, 21)])
        line([(3, 12), (8, 12)])
        line([(16, 12), (21, 12)])
        dot(12, 12, 1.5)
    elif name == "save":
        line([(12, 3.5), (12, 14.5)])
        line([(7.5, 10.5), (12, 15), (16.5, 10.5)])
        line([(4.5, 19.5), (19.5, 19.5)])
    elif name == "upload":
        line([(12, 20), (12, 8)])
        line([(7.5, 12.5), (12, 8), (16.5, 12.5)])
        line([(4.5, 4), (19.5, 4)])
    elif name == "history":
        circle(12, 12, 8.4)
        line([(12, 7), (12, 12), (16, 14.5)], 1.8)
    elif name == "play":
        a, b = P(8, 5)
        c, d = P(19.5, 12)
        e, f = P(8, 19)
        canvas.create_polygon(a, b, c, d, e, f, fill=color, outline="", tags=tags)
    elif name == "stop":
        a, b = P(6.5, 6.5)
        c, d = P(17.5, 17.5)
        canvas.create_rectangle(a, b, c, d, fill=color, outline="", width=sp(0), tags=tags)
    elif name == "refresh":
        a, b = P(4.5, 4.5)
        c, d = P(19.5, 19.5)
        canvas.create_arc(a, b, c, d, start=40, extent=280, style="arc",
                          outline=color, width=width, tags=tags)
        line([(17.5, 3.5), (19.5, 8), (15, 8.5)], 1.6)
    elif name == "chevron_down":
        line([(7, 10), (12, 15), (17, 10)], 1.9)
    elif name == "chevron_right":
        line([(10, 7), (15, 12), (10, 17)], 1.9)
    elif name == "camera":
        box(3, 7.5, 21, 19, r=3)
        circle(12, 13.2, 3.4)
        line([(8.5, 7.5), (10, 4.5), (14, 4.5), (15.5, 7.5)], 1.7)
    elif name == "check":
        line([(6, 12.5), (10.5, 17), (18, 7.5)], 2.2)


# ---------------------------------------------------------------- 玻璃贴图

class GlassCache:
    """玻璃面板 PhotoImage 缓存；尺寸/色调变化才重新生成。"""

    def __init__(self, limit: int = 260) -> None:
        self._images: dict[tuple, object] = {}
        self._limit = limit

    def get(self, master, width, height, radius, palette, base_hex, *,
            tint=None, alpha=None, edge_alpha=None):
        key = (int(width), int(height), int(radius), palette["name"], base_hex,
               tint, alpha, edge_alpha)
        cached = self._images.get(key)
        if cached is not None:
            return cached
        if len(self._images) > self._limit:
            self._images.clear()
        data = theme.glass_png(width, height, radius, palette, base_hex,
                              tint=tint, alpha=alpha, edge_alpha=edge_alpha)
        image = tk.PhotoImage(master=master, data=data, format="png") if data else None
        self._images[key] = image
        return image


GLASS = GlassCache()


def base_color(widget, palette) -> str:
    """采样组件所在位置的背景平均色；未映射时退回主题底色。"""
    try:
        root = widget.winfo_toplevel()
        x = widget.winfo_rootx() - root.winfo_rootx()
        y = widget.winfo_rooty() - root.winfo_rooty()
        return theme.BACKDROP.sample(x, y, max(1, widget.winfo_width()),
                                     max(1, widget.winfo_height()), palette["bg"])
    except tk.TclError:
        return palette["bg"]


# ---------------------------------------------------------------- 圆角卡片

class Card(tk.Frame):
    """带抗锯齿圆角、细描边与柔和阴影的卡片容器。"""

    def __init__(self, master, palette, *, radius=None, padding=(sp(18), sp(16)),
                 fill=None, bg=None, outline=True, shadow=None, tags=()):
        radius = sp(RADIUS["card"]) if radius is None else radius
        self.palette = palette
        self.fill = fill or palette["surface"]
        self.bg = bg or palette["bg"]
        self.radius = radius
        self.outline = palette["border"] if outline else None
        self.shadow = shadow if shadow is not None else palette["shadow"]
        self.pad_x = max(padding[0], radius)
        self.pad_y = max(padding[1], radius)
        self._image = None
        self._job = None
        super().__init__(master, bg=self.bg, highlightthickness=0, bd=0)
        self.canvas = tk.Canvas(self, bg=self.bg, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._item = None
        self.body = tk.Frame(self, bg=self.fill, highlightthickness=0, bd=0)
        self.body.pack(fill="both", expand=True, padx=self.pad_x, pady=self.pad_y)
        self.bind("<Configure>", self._schedule)

    def _schedule(self, _event=None):
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(24, self._render)

    def _render(self):
        self._job = None
        width, height = self.winfo_width(), self.winfo_height()
        if width < 8 or height < 8:
            return
        palette = self.palette
        base = base_color(self, palette)
        image = GLASS.get(self, width, height, self.radius, palette, base)
        if image is None:
            # 没有 Pillow：退回实色圆角面板，界面依旧可用
            image, margin = CACHE.get(self, width, height, self.radius, palette["surface"],
                                      palette["bg"], shadow=palette["shadow"],
                                      outline=palette["border"],
                                      gradient=mix(palette["surface"], palette["bg"], 0.30))
            self.canvas.configure(bg=palette["bg"])
            self.body.configure(bg=palette["surface"])
            self.fill, self.bg = palette["surface"], palette["bg"]
        else:
            self.canvas.configure(bg=base)
            self.body.configure(bg=theme.glass_fill(palette, base))
            self.fill, self.bg = theme.glass_fill(palette, base), base
            margin = 0
        self._image = image
        if self._item is None:
            self._item = self.canvas.create_image(0, 0, anchor="nw", image=image)
        else:
            self.canvas.itemconfigure(self._item, image=image)
        self.canvas.coords(self._item, -margin, -margin)

    def apply_palette(self, palette):
        self.palette = palette
        self.fill = palette["surface"]
        self.bg = palette["bg"]
        self.outline = palette["border"]
        self.shadow = palette["shadow"]
        self.configure(bg=self.bg)
        self.canvas.configure(bg=self.bg)
        self.body.configure(bg=self.fill)
        self._render()


def margin_of(radius: int) -> int:
    return max(0, min(int(round(radius * 0.9)), 14))


# ---------------------------------------------------------------- 状态胶囊

class Pill(tk.Canvas):
    """圆角状态胶囊：小圆点 + 文字，颜色随状态切换。"""

    def __init__(self, master, palette, text="", tone="neutral", *, height=sp(26), bg=None):
        self.palette = palette
        self.height = height = sp(height)
        self._text = text
        self._tone = tone
        self._image = None
        if bg is None:
            try:
                bg = master.cget("bg")
            except Exception:
                bg = palette["surface"]
        self.surface = bg
        super().__init__(master, highlightthickness=0, bd=0, height=height, bg=bg)
        self._item = self.create_image(0, 0, anchor="nw")
        self._dot = self.create_oval(0, 0, 0, 0, outline="")
        self._label = self.create_text(0, 0, anchor="w", font=(FONT_UI, 9), text=text)
        self._width = 90
        self.set(text, tone, animate=False)

    def _colors(self, tone):
        """返回 (玻璃 tint, 玻璃 alpha, 文字/圆点色)。"""
        p = self.palette
        return {
            "neutral": (p["glass_tint"], p["glass_alpha"] + 0.02, p["text_secondary"]),
            "live": (p["success"], 0.26, p["success"]),
            "waiting": (p["warn"], 0.26, p["warn"]),
            "error": (p["danger"], 0.26, p["danger"]),
            "accent": (p["accent"], 0.24, p["accent"]),
        }.get(tone, (p["glass_tint"], p["glass_alpha"] + 0.02, p["text_secondary"]))

    def set(self, text=None, tone=None, *, animate=True):
        if text is not None:
            self._text = text
        if tone is not None:
            self._tone = tone
        tint, alpha, ink = self._colors(self._tone)
        self.itemconfigure(self._label, text=self._text, fill=ink, font=(FONT_UI, 9))
        self.itemconfigure(self._dot, fill=ink)
        text_w = measure_text(self._text, (FONT_UI, 9)) if self._text else 0
        width = max(62, int(text_w + 46))
        height = self.height
        self._width = width
        base = base_color(self, self.palette)
        self.surface = base
        self.configure(width=width, bg=base)
        image = GLASS.get(self, width, height, height // 2, self.palette, base,
                          tint=tint, alpha=alpha, edge_alpha=self.palette["glass_edge_alpha"])
        self._image = image
        self.itemconfigure(self._item, image=image)
        self.coords(self._item, 0, 0)
        cy = height // 2
        self.coords(self._dot, sp(13), cy - sp(3), sp(18), cy + sp(3))
        self.coords(self._label, sp(24), cy)

    def apply_palette(self, palette):
        self.palette = palette
        self.set(animate=False)


# ---------------------------------------------------------------- 圆角按钮

class RoundedButton(tk.Canvas):
    """自绘圆角按钮：三种语气（ghost / soft / primary）+ 可选前导图标。"""

    def __init__(self, master, text, command, palette, *, kind="ghost",
                 icon=None, height=sp(34), width=None, radius=None, font=None,
                 pad_x=sp(15), bg=None):
        self.palette = palette
        self.kind = kind
        self.text = text
        self.command = command
        self.icon = icon
        self.height = height
        self.radius = sp(RADIUS["button"]) if radius is None else radius
        self.pad_x = pad_x
        self.font = font or TYPE["body"]
        if bg is None:
            try:
                bg = master.cget("bg")
            except Exception:
                bg = palette["surface"]
        self.surface = bg
        self._surface = bg
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._image = None
        self._lift = 0.0
        self._scale = 1.0
        self._anim = theme.Animator(self)
        super().__init__(master, highlightthickness=0, bd=0, height=height,
                         bg=bg, takefocus=1)
        self._bg_item = self.create_image(0, 0, anchor="nw")
        self._icon_id = None
        self._text_id = self.create_text(0, 0, text=text, font=self.font, fill=palette["text"])
        self._width = width or self._measure()
        self.configure(width=self._width)
        self._render()
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonPress-1>", lambda _e: self._set_press(True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda _e: self._render())
        self.bind("<KeyPress-space>", lambda _e: self._set_press(True))
        self.bind("<KeyRelease-space>", lambda _e: self._release(None))

    def _measure(self):
        if not self.text:
            content = 22 if self.icon else 0
        else:
            content = measure_text(self.text, self.font) + (22 if self.icon else 0)
        return max(64, int(content + self.pad_x * 2))

    def _tones(self):
        """玻璃色调：(tint, alpha, edge_alpha, 文字色)。"""
        p = self.palette
        if not self._enabled:
            return p["muted"], 0.08, 0.04, p["muted"]
        if self.kind == "primary":
            tint = p["accent"]
            if self._pressed:
                tint = p["accent_press"]
            elif self._hover:
                tint = p["accent_hover"]
            return tint, 0.96, 0.42, p["on_accent"]
        if self.kind == "soft":
            alpha = 0.16 if p["name"] == "light" else 0.24
            if self._pressed:
                alpha += 0.12
            elif self._hover:
                alpha += 0.07
            return p["accent"], alpha, p["glass_edge_alpha"] + 0.12, p["accent"]
        alpha = p["glass_alpha"] + (0.07 if self._hover else 0.0)
        if self._pressed:
            alpha += 0.05
        return p["glass_tint"], min(0.96, alpha), p["glass_edge_alpha"], p["text"]

    def _layout(self, lift=0.0):
        actual = self.winfo_width()
        width = max(24, actual if actual > 1 else self._width)
        height = self.height
        palette = self.palette
        base = base_color(self, palette)
        tint, alpha, edge_alpha, ink = self._tones()
        scale = getattr(self, "_scale", 1.0)
        draw_w = max(12, int(round((width - 2) * scale)))
        draw_h = max(8, int(round((height - 3) * scale)))
        image = GLASS.get(self, draw_w, draw_h, min(self.radius, draw_h // 2), palette, base,
                          tint=tint, alpha=alpha, edge_alpha=edge_alpha)
        self._image = image
        self._surface = base
        self.configure(bg=base)
        self.itemconfigure(self._bg_item, image=image)
        self.coords(self._bg_item, (width - draw_w) / 2, (height - draw_h) / 2 + lift)
        self.delete("icon")
        text_w = measure_text(self.text, self.font) if self.text else 0
        icon_w = sp(22) if self.icon else 0
        start = (width - text_w - icon_w) / 2
        cy = height / 2 + lift
        if self.icon:
            draw_icon(self, self.icon, start, cy - sp(9), 18, ink, 1.7, tags=("icon",))
        self.itemconfigure(self._text_id, text=self.text, fill=ink,
                           font=self.font, anchor="w")
        self.coords(self._text_id, start + icon_w, cy)

    def _render(self):
        self._layout(self._lift)

    def _set_hover(self, value):
        self._hover = value
        self._layout(self._lift)
        self._animate_lift(-1.5 if value else 0.0)

    def _animate_lift(self, target):
        start = self._lift
        if abs(start - target) < 0.05:
            self._lift = target
            self._layout(target)
            return

        def frame(eased, _raw):
            self._lift = start + (target - start) * eased
            self._layout(self._lift)

        self._anim.run(220, frame, easing=theme.spring)

    def _animate_scale(self, target, duration, easing):
        start = getattr(self, "_scale", 1.0)
        if abs(start - target) < 0.004:
            self._scale = target
            self._layout(self._lift)
            return

        def frame(eased, _raw):
            self._scale = start + (target - start) * eased
            self._layout(self._lift)

        self._anim.run(duration, frame, easing=easing)

    def _set_press(self, value):
        if not self._enabled:
            return
        self._pressed = value
        self._layout(self._lift)
        if value:
            self._animate_scale(0.955, 110, theme.ease_out_cubic)
        else:
            self._animate_scale(1.0, 340, theme.spring)

    def _release(self, _event):
        if not self._enabled:
            return
        if self._pressed:
            self._pressed = False
            self._layout(self._lift)
            self._animate_scale(1.0, 340, theme.spring)
            if callable(self.command):
                self.command()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._layout(self._lift)

    # ---- ttk.Button 兼容层：自检与外部代码按 ttk 语义操作按钮 ----

    @property
    def _job(self):
        return self._anim._job

    def invoke(self):
        if self._enabled and callable(self.command):
            return self.command()

    def state(self, spec=None):
        if spec is None:
            return () if self._enabled else ("disabled",)
        for item in spec:
            if item == "disabled":
                self.set_enabled(False)
            elif item == "!disabled":
                self.set_enabled(True)
        return ()

    def instate(self, statespec=None, callback=None):
        wanted_disabled = "disabled" in (statespec or [])
        return (not self._enabled) == wanted_disabled

    def set_text(self, text):
        self.text = text
        self._width = self._measure()
        self.configure(width=self._width)
        self._layout(self._lift)

    def configure(self, cnf=None, **kwargs):
        if "text" in kwargs:
            self.set_text(kwargs.pop("text"))
            if not kwargs and cnf is None:
                return
        return super().configure(cnf, **kwargs)

    config = configure

    def apply_palette(self, palette):
        self.palette = palette
        self._layout(self._lift)


# ---------------------------------------------------------------- 侧栏导航

class NavItem(tk.Canvas):
    """侧栏导航项：图标 + 文本 + 左侧选中指示条（切换时收缩/展开动画）。"""

    def __init__(self, master, palette, key, title, command, *, icon=None, height=sp(42)):
        self.palette = palette
        self.key = key
        self.title = title
        self.command = command
        self.icon = icon or "vision"
        self.height = height
        self.selected = False
        self._hover = False
        self._bar = 0.0
        self._image = None
        self._anim = theme.Animator(self)
        super().__init__(master, highlightthickness=0, bd=0, height=height,
                         bg=palette["sidebar"], cursor="hand2")
        self._bg = self.create_image(0, 0, anchor="nw")
        self._indicator = self.create_rectangle(0, 0, 0, 0, fill=palette["accent"], outline="")
        self.bind("<Configure>", lambda _e: self._render())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", lambda _e: self.command())

    def _render(self):
        width = self.winfo_width() if self.winfo_width() > 1 else 150
        height = self.height
        p = self.palette
        base = base_color(self, p)
        if self.selected or self._hover:
            if self.selected:
                tint, alpha, edge = p["accent"], (0.20 if p["name"] == "light" else 0.26), 0.30
            else:
                tint, alpha, edge = p["glass_tint"], p["glass_alpha"] + 0.03, p["glass_edge_alpha"]
            image = GLASS.get(self, max(20, width - 8), height - 4, 12, p, base,
                              tint=tint, alpha=alpha, edge_alpha=edge)
            self._image = image
            self.itemconfigure(self._bg, image=image, state="normal")
            self.coords(self._bg, 4, 2)
        else:
            self.itemconfigure(self._bg, state="hidden")
        self.configure(bg=base)
        ink = p["accent"] if self.selected else (p["text"] if self._hover else p["text_secondary"])
        self.delete("icon")
        draw_icon(self, self.icon, sp(15), height / 2 - sp(9), 18, ink, 1.75, tags=("icon",))
        self.delete("label")
        self.create_text(sp(42), height / 2, text=self.title, anchor="w",
                         fill=ink, font=(FONT_UI, 10, "bold" if self.selected else "normal"),
                         tags=("label",))
        # 选中指示条：高度按弹簧动画值收缩/展开
        bar_h = (height - 16) * self._bar
        top = (height - bar_h) / 2
        self.coords(self._indicator, 0, top, 3, top + bar_h)
        self.itemconfigure(self._indicator, fill=p["accent"])

    def _set_hover(self, value):
        self._hover = value
        self._render()

    def set_selected(self, selected, animate=True):
        self.selected = selected
        target = 1.0 if selected else 0.0
        if not animate:
            self._bar = target
            self._render()
            return
        start = self._bar

        def frame(eased, _raw):
            self._bar = start + (target - start) * eased
            self._render()

        if animate:
            self._anim.run(320, frame, easing=theme.spring)

    def invoke(self):
        self.command()

    def state(self, spec=None):
        return ()

    def instate(self, statespec=None, callback=None):
        return True

    def apply_palette(self, palette):
        self.palette = palette
        self._render()


# ---------------------------------------------------------------- 分段控件

class SegmentedControl(tk.Canvas):
    """分段选择器：选中块滑动到目标段，替代 ttk.Notebook 的方角标签。"""

    def __init__(self, master, palette, options, command, *, height=sp(32), font=None, bg=None):
        self.palette = palette
        self.options = list(options)
        self.command = command
        self.height = height
        self.font = font or (FONT_UI, 9)
        self.index = 0
        self._offset = 0.0
        self._target = 0.0
        self._width = 200
        self._image = None
        self._anim = theme.Animator(self)
        if bg is None:
            try:
                bg = master.cget("bg")
            except Exception:
                bg = palette["surface"]
        self.surface = bg
        super().__init__(master, highlightthickness=0, bd=0, height=height, bg=bg)
        self._bg = self.create_image(0, 0, anchor="nw")
        self._thumb = self.create_image(0, 0, anchor="nw")
        self._labels = []
        for i, text in enumerate(self.options):
            self._labels.append(self.create_text(0, height / 2, text=text,
                                                 font=self.font, fill=palette["muted"]))
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_click)

    def _on_configure(self, _event=None):
        self._width = max(self.winfo_width(), 60)
        self._render()

    def _render(self):
        p = self.palette
        width = self._width
        height = self.height
        base = base_color(self, p)
        self.surface = base
        self.configure(bg=base)
        bg_image = GLASS.get(self, width, height, height // 2, p, base,
                             tint=p["glass_tint"], alpha=p["glass_alpha"] + 0.03,
                             edge_alpha=p["glass_edge_alpha"])
        self._image = bg_image
        self.itemconfigure(self._bg, image=bg_image)
        self.coords(self._bg, 0, 0)
        seg = width / max(1, len(self.options))
        thumb_w = seg - 6
        thumb_x = 3 + self._offset * seg
        t_image = GLASS.get(self, int(thumb_w), height - 6, (height - 6) // 2, p, base,
                            tint=p["accent"] if p["name"] == "light" else "#FFFFFF",
                            alpha=0.14 if p["name"] == "light" else 0.10,
                            edge_alpha=p["glass_edge_alpha"] + 0.2)
        self._thumb_image = t_image
        self.itemconfigure(self._thumb, image=t_image)
        self.coords(self._thumb, thumb_x, 3)
        for i, item in enumerate(self._labels):
            active = abs(self._offset - i) < 0.5
            self.coords(item, seg * i + seg / 2, height / 2)
            self.itemconfigure(item, fill=p["text"] if active else p["muted"],
                               font=(FONT_UI, 9, "bold") if active else self.font)

    def _on_click(self, event):
        seg = self._width / max(1, len(self.options))
        index = max(0, min(len(self.options) - 1, int(event.x // seg)))
        self.select(index)

    def select(self, index, animate=True):
        if index == self.index and animate:
            return
        self.index = index
        start, target = self._offset, float(index)
        if not animate:
            self._offset = target
            self._render()
            self.command(index)
            return

        def frame(eased, _raw):
            self._offset = start + (target - start) * eased
            self._render()

        # 内容立即切换，动画只负责滑块跟随：避免"点了没反应"的脱节感。
        self.command(index)
        self._anim.run(340, frame, easing=theme.spring)

    def apply_palette(self, palette):
        self.palette = palette
        for item in self._labels:
            self.itemconfigure(item, fill=palette["muted"])
        self._render()


# ---------------------------------------------------------------- 滑块

class Slider(tk.Canvas):
    """圆角轨道 + 圆形滑块；hover 放大，拖动改变数值。"""

    def __init__(self, master, palette, from_=0.0, to=1.0, value=0.0, command=None,
                 *, width=sp(170), height=sp(24), step=None):
        self.palette = palette
        self.low, self.high = float(from_), float(to)
        self.value = float(value)
        self.step = step
        self.command = command
        self.height = height
        self._active = False
        self._grow = 1.0
        self._anim = theme.Animator(self)
        try:
            bg = master.cget("bg")
        except Exception:
            bg = palette["surface"]
        self._bg = bg
        super().__init__(master, highlightthickness=0, bd=0, width=width, height=height,
                         bg=bg, cursor="hand2")
        self._track = self.create_line(0, 0, 0, 0, width=sp(4), capstyle="round")
        self._fill = self.create_line(0, 0, 0, 0, width=sp(4), capstyle="round")
        self._knob = self.create_oval(0, 0, 0, 0, width=sp(2))
        self.bind("<Configure>", lambda _e: self._render())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _e: self._release())
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))

    def _set_value(self, value, notify=True):
        span = (self.high - self.low) or 1.0
        value = max(self.low, min(self.high, value))
        if self.step:
            value = round(value / self.step) * self.step
        changed = abs(value - self.value) > 1e-9
        self.value = value
        self._render()
        if changed and notify and self.command:
            self.command(value)

    def _render(self):
        p = self.palette
        width = max(self.winfo_width(), 20)
        height = self.height
        y = height / 2
        pad = 7
        self.coords(self._track, pad, y, width - pad, y)
        self.itemconfigure(self._track, fill=mix(p["surface_alt"], p["text"], 0.16))
        span = (self.high - self.low) or 1.0
        ratio = (self.value - self.low) / span
        x = pad + ratio * (width - 2 * pad)
        self.coords(self._fill, pad, y, x, y)
        accent = p["accent"] if getattr(self, "_enabled", True) else p["muted"]
        self.itemconfigure(self._fill, fill=accent)
        radius = 6.5 * self._grow
        self.coords(self._knob, x - radius, y - radius, x + radius, y + radius)
        self.itemconfigure(self._knob, fill=p["surface"], outline=accent, width=2.4)

    def _hover(self, inside):
        target = 1.14 if inside else 1.0
        start = self._grow

        def frame(eased, _raw):
            self._grow = start + (target - start) * eased
            self._render()

        self._anim.run(120, frame)

    def _press(self, event):
        self._active = True
        self._drag(event)

    def _drag(self, event):
        width = max(self.winfo_width(), 20)
        pad = 7
        ratio = (event.x - pad) / max(1, width - 2 * pad)
        self._set_value(self.low + ratio * (self.high - self.low))

    def _release(self):
        self._active = False

    def get(self):
        return self.value

    def set(self, value):
        self._set_value(value, notify=False)

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._render()

    def apply_palette(self, palette):
        self.palette = palette
        try:
            self._bg = self.master.cget("bg")
        except Exception:
            self._bg = palette["surface"]
        self.configure(bg=self._bg)
        self._render()


# ---------------------------------------------------------------- 勾选行

class CheckRow(tk.Canvas):
    """自绘勾选行：圆角方框 + 勾选动画，替代原生 Checkbutton 的凸起外观。"""

    def __init__(self, master, palette, text, variable, command=None, *, height=sp(28)):
        self.palette = palette
        self.text = text
        self.variable = variable
        self.command = command
        self.height = height
        self._hover = False
        self._progress = 0.0
        # Canvas 默认宽 378px，不显式测量会把同一行的勾选项挤出卡片。
        self._width_px = int(measure_text(text, (FONT_UI, 10)) + 40)
        try:
            bg = master.cget("bg")
        except Exception:
            bg = palette["surface"]
        super().__init__(master, highlightthickness=0, bd=0, height=height,
                         width=self._width_px, bg=bg, cursor="hand2")
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Configure>", lambda _e: self._render())
        self._progress = 1.0 if variable.get() else 0.0

    def _toggle(self, _event=None):
        self.variable.set(not self.variable.get())
        self._animate()
        if self.command:
            self.command()

    def _animate(self):
        target = 1.0 if self.variable.get() else 0.0
        start = self._progress
        anim = theme.Animator(self)

        def frame(eased, _raw):
            self._progress = start + (target - start) * eased
            self._render()

        anim.run(140, frame)

    def _set_hover(self, value):
        self._hover = value
        self._render()

    def _render(self):
        p = self.palette
        height = self.height
        self.delete("all")
        size = sp(17)
        x, y = sp(1), (height - size) / 2
        radius = sp(5)
        checked = self._progress
        # 方框：未选中描边，选中渐变为 accent 填充
        if checked > 0.02:
            fill = mix(p["surface_alt"], p["accent"], checked)
            a, b = x + radius, y
            self.create_polygon(
                a, b, x + size - radius, b, x + size, b + radius, x + size, y + size - radius,
                x + size - radius, y + size, a, y + size, x, y + size - radius, x, y + radius,
                smooth=True, fill=fill, outline="")
        outline_color = p["accent"] if (self._hover or checked > 0.2) else p["border"]
        self.create_line(x + radius, y, x + size - radius, y, fill=outline_color, width=1.2)
        self.create_line(x + size, y + radius, x + size, y + size - radius, fill=outline_color, width=1.2)
        self.create_line(x + size - radius, y + size, x + radius, y + size, fill=outline_color, width=1.2)
        self.create_line(x, y + size - radius, x, y + radius, fill=outline_color, width=1.2)
        if checked > 0.35:
            ink = p["on_accent"]
            draw_icon(self, "check", x + 1.5, y + 1.5, size - 3, ink, 2.0)
        self.create_text(x + size + sp(10), height / 2, text=self.text, anchor="w",
                         fill=p["text"] if self._hover else p["text_secondary"],
                         font=(FONT_UI, 10))

    def apply_palette(self, palette):
        self.palette = palette
        try:
            self.configure(bg=self.master.cget("bg"))
        except Exception:
            self.configure(bg=palette["surface"])
        self._render()


# ---------------------------------------------------------------- 指标卡

class MetricChip(tk.Frame):
    """小指标卡：标签 + 等宽数字（带单位），数值变化时滚动插值。"""

    def __init__(self, master, palette, label, *, unit="", digits=0,
                 width=sp(118), height=sp(58), placeholder="—"):
        self.palette = palette
        self.unit = unit
        self.digits = digits
        self._shown = None
        self._text = placeholder
        width, height = sp(width), sp(height)
        super().__init__(master, width=width, height=height, bg=palette["surface"])
        self.pack_propagate(False)
        self.canvas = tk.Canvas(self, bg=palette["surface"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._image = None
        self.body = tk.Frame(self.canvas, bg=palette["surface_alt"])
        self.item = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self._bg_item = self.canvas.create_image(0, 0, anchor="nw", tags="bg")
        self.label = tk.Label(self.body, text=label, font=(FONT_UI, 8),
                              bg=palette["surface_alt"], fg=palette["muted"])
        self.value = tk.Label(self.body, text=self._text, font=(FONT_MONO, 13, "bold"),
                              bg=palette["surface_alt"], fg=palette["text"])
        self.label.pack(anchor="w", padx=sp(12), pady=(sp(9), sp(0)))
        self.value.pack(anchor="w", padx=sp(12), pady=(sp(2), sp(9)))
        self.bind("<Configure>", lambda _e: self._render())

    def _fmt(self, number):
        return f"{number:.{self.digits}f}{self.unit}" if self.digits else f"{int(round(number))}{self.unit}"

    def _render(self):
        p = self.palette
        width = max(self.winfo_width(), 40)
        height = max(self.winfo_height(), 30)
        base = base_color(self, p)
        fill = theme.glass_fill(p, base, alpha=p["glass_alpha"] + 0.04)
        self._fill = fill
        image = GLASS.get(self, width, height, 12, p, base,
                          tint=p["glass_tint"], alpha=p["glass_alpha"] + 0.04,
                          edge_alpha=p["glass_edge_alpha"])
        self._image = image
        self.canvas.configure(bg=base)
        self.body.configure(bg=fill)
        self.label.configure(bg=fill)
        self.value.configure(bg=fill)
        self.canvas.itemconfigure(self._bg_item, image=image)
        self.canvas.coords(self._bg_item, 0, 0)
        self.canvas.coords(self.item, 0, 0)
        self.canvas.itemconfigure(self.item, width=width, height=height)
        self.canvas.tag_lower(self._bg_item)

    def set(self, text=None, *, numeric=None, animate=True):
        if numeric is None:
            if text is not None:
                self._text = text
                self.value.configure(text=text)
                self._shown = None
            return
        if not animate or self._shown is None:
            self._shown = numeric
            self.value.configure(text=self._fmt(numeric))
            return
        start, target = self._shown, numeric
        self._shown = target
        anim = theme.Animator(self)
        self._text = self._fmt(target)

        def frame(eased, _raw):
            shown = start + (target - start) * eased
            self.value.configure(text=self._fmt(shown))

        anim.run(240, frame)

    def apply_palette(self, palette):
        self.palette = palette
        self.configure(bg=palette["bg"])
        self.canvas.configure(bg=palette["bg"])
        self.body.configure(bg=palette["surface_alt"])
        self.label.configure(bg=palette["surface_alt"], fg=palette["muted"])
        self.value.configure(bg=palette["surface_alt"], fg=palette["text"])
        self._render()


# ---------------------------------------------------------------- 空态

class EmptyState(tk.Canvas):
    """预览区空态：虚线圆角框 + 相机图标 + 提示文案，图标呼吸动画。"""

    def __init__(self, master, palette, title="等待图像", hint="连接设备，或导入一张图片",
                 icon="camera"):
        self.palette = palette
        self.title = title
        self.hint = hint
        self.icon = icon
        self._phase = 0.0
        self._job = None
        self._running = False
        self._glow = None
        self._glow_key = None
        super().__init__(master, highlightthickness=0, bd=0, bg=palette["canvas"])
        self.bind("<Configure>", lambda _e: self._render())

    def start(self):
        if self._running:
            return
        self._running = True
        self._tick()

    def stop(self):
        self._running = False
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _tick(self):
        if not self._running:
            return
        self._phase = (self._phase + 0.035) % (2 * math.pi)
        self._render()
        try:
            self._job = self.after(50, self._tick)
        except tk.TclError:
            self._job = None

    def _render(self):
        p = self.palette
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 40 or height < 40:
            return
        self.configure(bg=p["canvas"])
        cx, cy = width / 2, height / 2
        # 玻璃后的柔光：径向光晕贴图（Pillow 可用时）
        radius = int(min(width, height) * 1.15)
        key = (radius, p["name"])
        if key != self._glow_key and theme.PIL_AVAILABLE:
            data = theme.glow_png(radius, p["accent"], 0.10 if p["name"] == "light" else 0.13)
            self._glow = tk.PhotoImage(master=self, data=data, format="png") if data else None
            self._glow_key = key
        if self._glow is not None:
            self.create_image(cx - radius / 2, cy - radius / 2 - sp(18), anchor="nw", image=self._glow)
        pulse = (math.sin(self._phase) + 1) / 2
        # 虚线圆角框包住图标，文案落在框外，避免压线
        half_w, half_h = sp(62), sp(44)
        x0, y0, x1, y1 = cx - half_w, cy - half_h - 26, cx + half_w, cy + half_h - 26
        dash_color = mix(p["canvas"], p["muted"], 0.34 + pulse * 0.10)
        r = 17
        for xa, ya, xb, yb in ((x0 + r, y0, x1 - r, y0), (x1, y0 + r, x1, y1 - r),
                               (x1 - r, y1, x0 + r, y1), (x0, y1 - r, x0, y0 + r)):
            self.create_line(xa, ya, xb, yb, fill=dash_color, width=1.4, dash=(4, 5))
        for ax, ay, start in ((x0 + r, y0 + r, 180), (x1 - r, y0 + r, 270),
                              (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90)):
            self.create_arc(ax - r, ay - r, ax + r, ay + r, start=start, extent=90,
                            style="arc", outline=dash_color, width=1.4, dash=(4, 5))
        icon_color = mix(p["muted"], p["text_secondary"], 0.35 + pulse * 0.45)
        draw_icon(self, self.icon, cx - 20, cy - 26 - 20, 40, icon_color, 1.7)
        self.create_text(cx, cy + half_h - sp(26) + sp(34), text=self.title,
                         fill=p["text_secondary"], font=(FONT_UI, 11, "bold"))
        self.create_text(cx, cy + half_h - sp(26) + sp(58), text=self.hint,
                         fill=p["muted"], font=(FONT_UI, 9))

    def set(self, title=None, hint=None):
        if title is not None:
            self.title = title
        if hint is not None:
            self.hint = hint
        self._render()

    def apply_palette(self, palette):
        self.palette = palette
        self._render()
