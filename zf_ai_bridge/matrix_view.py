"""像素矩阵视图：每格印灰度数字，虚拟化渲染 + 大津法二值化 + 拖拽/指针缩放。"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import theme, widgets


def otsu_threshold(gray: bytes) -> int:
    """大津法求最佳阈值（区间中点）。"""
    if not gray:
        return 128
    hist = [0] * 256
    for v in gray:
        hist[v] += 1
    total = len(gray)
    sum_total = sum(i * hist[i] for i in range(256))
    sum_b = 0
    w_b = 0
    vars_ = [0.0] * 256
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        vars_[t] = w_b * w_f * (m_b - m_f) ** 2
    max_var = max(vars_)
    if max_var <= 0:
        return 128
    best = [t for t in range(256) if vars_[t] == max_var]
    return best[len(best) // 2]


def gray_color(v: int) -> str:
    c = max(0, min(255, v))
    return f"#{c:02x}{c:02x}{c:02x}"


def false_color(v: int) -> str:
    if v < 128:
        t = v / 127.0
        r, g, b = 0, int(t * 255), int((1 - t) * 255)
    else:
        t = (v - 128) / 127.0
        r, g, b = int(t * 255), int((1 - t) * 255), 0
    return f"#{r:02x}{g:02x}{b:02x}"


class MatrixView:
    """可缩放像素矩阵。左键拖拽平移，滚轮以鼠标为中心缩放。"""

    def __init__(
        self,
        parent: tk.Misc,
        gray: bytes,
        width: int,
        height: int,
        title: str = "像素矩阵",
        cell: int = 12,
        palette=None,
    ) -> None:
        self.gray = gray
        self.w = width
        self.h = height
        self.cell = cell
        self.show_numbers = True
        self.use_false = False
        self.use_binary = False
        self.threshold = otsu_threshold(gray)
        self._redraw_pending = False
        self._pil_img = None
        self.palette = palette or theme.find_palette(parent)
        p = self.palette

        self.win = tk.Toplevel(parent)
        self.win.title(f"{title}  {width}x{height}")
        self.win.geometry("1000x740")
        self.win.minsize(560, 420)
        self.win.configure(bg=p["bg"])
        self.win.palette = p

        shell = tk.Frame(self.win, bg=p["bg"])
        shell.pack(fill="both", expand=True, padx=18, pady=(14, 12))

        head = tk.Frame(shell, bg=p["bg"])
        head.pack(fill="x")
        titles = tk.Frame(head, bg=p["bg"])
        titles.pack(side="left")
        tk.Label(titles, text="像素矩阵", bg=p["bg"], fg=p["text"],
                 font=(theme.FONT_UI, 18, "bold")).pack(anchor="w")
        tk.Label(titles, text=f"{width}×{height} · 灰度数值、阈值与二值化", bg=p["bg"],
                 fg=p["muted"], font=(theme.FONT_UI, 9)).pack(anchor="w", pady=(3, 0))
        tools = tk.Frame(head, bg=p["bg"])
        tools.pack(side="right", pady=(4, 0))
        widgets.RoundedButton(tools, "关闭", self.win.destroy, p, height=32,
                              bg=p["bg"]).pack(side="right", padx=(6, 0))
        widgets.RoundedButton(tools, "适应", self._fit, p, height=32,
                              bg=p["bg"]).pack(side="right", padx=(6, 0))

        controls = widgets.Card(shell, p, padding=(16, 12), radius=14)
        controls.pack(fill="x", pady=(12, 0))
        row = tk.Frame(controls.body, bg=p["surface"])
        row.pack(fill="x")
        tk.Label(row, text="放大", bg=p["surface"], fg=p["muted"],
                 font=(theme.FONT_UI, 9)).pack(side="left")
        self.zoom_var = tk.IntVar(value=cell)
        self.zoom_scale = widgets.Slider(row, p, 3, 48, cell, self._on_zoom,
                                         width=150, height=24)
        self.zoom_scale.pack(side="left", padx=(8, 10))
        self.zoom_label = tk.Label(row, text=f"{cell}px", bg=p["surface"], fg=p["text"],
                                   font=(theme.FONT_MONO, 9), width=5)
        self.zoom_label.pack(side="left", padx=(0, 16))
        self.num_var = tk.BooleanVar(value=True)
        widgets.CheckRow(row, p, "数值", self.num_var, self._redraw).pack(side="left", padx=(0, 10))
        self.false_var = tk.BooleanVar(value=False)
        widgets.CheckRow(row, p, "伪彩色", self.false_var, self._redraw).pack(side="left", padx=(0, 10))
        self.bin_var = tk.BooleanVar(value=False)
        widgets.CheckRow(row, p, "二值化", self.bin_var, self._redraw).pack(side="left")

        row2 = tk.Frame(controls.body, bg=p["surface"])
        row2.pack(fill="x", pady=(10, 0))
        tk.Label(row2, text="阈值", bg=p["surface"], fg=p["muted"],
                 font=(theme.FONT_UI, 9)).pack(side="left")
        self.thr_var = tk.IntVar(value=self.threshold)
        self.thr_scale = widgets.Slider(row2, p, 0, 255, self.threshold, self._on_thr,
                                        width=220, height=24)
        self.thr_scale.pack(side="left", padx=(8, 10))
        self.thr_label = tk.Label(row2, text=str(self.threshold), bg=p["surface"], fg=p["text"],
                                  font=(theme.FONT_MONO, 9), width=4)
        self.thr_label.pack(side="left")
        widgets.RoundedButton(row2, "大津自动", self._otsu, p, height=30,
                              bg=p["surface"]).pack(side="left", padx=(10, 0))
        self.stat_var = tk.StringVar(value="")
        tk.Label(row2, textvariable=self.stat_var, bg=p["surface"], fg=p["muted"],
                 font=(theme.FONT_UI, 9)).pack(side="left", padx=12)

        self.coord_var = tk.StringVar(value="拖拽平移 · 滚轮缩放（以鼠标为中心）")
        tk.Label(shell, textvariable=self.coord_var, bg=p["bg"], fg=p["muted"],
                 font=(theme.FONT_UI, 9)).pack(anchor="w", pady=(10, 0))

        canvas_card = widgets.Card(shell, p, padding=(12, 12), radius=14)
        canvas_card.pack(fill="both", expand=True, pady=(10, 0))
        wrap = canvas_card.body
        self.canvas = tk.Canvas(wrap, background=p["canvas"], highlightthickness=1,
                                highlightbackground=p["border"], highlightcolor=p["accent"], bd=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        hsb = ttk.Scrollbar(wrap, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._wheel_by(1, e))
        self.canvas.bind("<Button-5>", lambda e: self._wheel_by(-1, e))
        self.canvas.bind("<Configure>", lambda e: self._schedule_redraw())
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._schedule_redraw())
        vsb.bind("<ButtonRelease-1>", lambda e: self._schedule_redraw())
        hsb.bind("<ButtonRelease-1>", lambda e: self._schedule_redraw())

        self._redraw()
        self._update_stat()

    # ---- 绘制 ----

    def _visible_range(self) -> tuple[int, int, int, int]:
        cw = self.cell
        x0 = int(self.canvas.canvasx(0) // cw) - 1
        y0 = int(self.canvas.canvasy(0) // cw) - 1
        x1 = int(self.canvas.canvasx(self.canvas.winfo_width()) // cw) + 1
        y1 = int(self.canvas.canvasy(self.canvas.winfo_height()) // cw) + 1
        return max(0, x0), max(0, y0), min(self.w, x1), min(self.h, y1)

    def _cell_color(self, v: int) -> str:
        if self.bin_var.get():
            thr = int(self.threshold)
            return "#ffffff" if v >= thr else "#000000"
        if self.use_false or self.false_var.get():
            return false_color(v)
        return gray_color(v)

    def _schedule_redraw(self) -> None:
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.win.after(30, self._redraw)

    def _draw_image_mode(self, x0: int, y0: int, x1: int, y1: int) -> None:
        cw = self.cell
        vw = max(1, x1 - x0)
        vh = max(1, y1 - y0)
        rgb = bytearray(vw * vh * 3)
        thr = int(self.threshold)
        use_bin = self.bin_var.get()
        use_false = self.false_var.get()
        for j in range(vh):
            gy = y0 + j
            if gy < 0 or gy >= self.h:
                continue
            row = gy * self.w
            for i in range(vw):
                gx = x0 + i
                if gx < 0 or gx >= self.w:
                    continue
                v = self.gray[row + gx]
                if use_bin:
                    r = g = b = 255 if v >= thr else 0
                elif use_false:
                    if v < 128:
                        t = v / 127.0
                        r, g, b = 0, int(t * 255), int((1 - t) * 255)
                    else:
                        t = (v - 128) / 127.0
                        r, g, b = int(t * 255), int((1 - t) * 255), 0
                else:
                    r = g = b = v
                k = (j * vw + i) * 3
                rgb[k] = r
                rgb[k + 1] = g
                rgb[k + 2] = b
        try:
            from PIL import Image, ImageTk
            im = Image.frombytes("RGB", (vw, vh), bytes(rgb))
            im = im.resize((vw * cw, vh * cw), Image.NEAREST)
            self._pil_img = ImageTk.PhotoImage(im)
            self.canvas.create_image(x0 * cw, y0 * cw, anchor="nw", image=self._pil_img)
        except Exception:
            step = max(1, min(8, cw))
            for j in range(0, vh, step):
                for i in range(0, vw, step):
                    v = self.gray[(y0 + j) * self.w + (x0 + i)]
                    self.canvas.create_rectangle(
                        (x0 + i) * cw, (y0 + j) * cw,
                        (x0 + i + step) * cw, (y0 + j + step) * cw,
                        fill=self._cell_color(v), outline="",
                    )

    def _redraw(self) -> None:
        self._redraw_pending = False
        if not hasattr(self, "canvas"):
            return
        self.use_false = self.false_var.get()
        self.use_numbers = self.num_var.get()
        self.use_binary = self.bin_var.get()
        cw = self.cell
        c = self.canvas
        c.delete("all")

        x0, y0, x1, y1 = self._visible_range()
        vw = max(0, x1 - x0)
        vh = max(0, y1 - y0)
        visible_cells = vw * vh

        c.create_rectangle(0, 0, self.w * cw, self.h * cw, fill="#1e1e1e", outline="")

        # 小格子/大量像素 → 位图模式
        if not self.use_numbers or self.use_binary or cw < 8 or visible_cells > 2500:
            self._draw_image_mode(x0, y0, x1, y1)
            if self.use_binary and cw >= 10 and visible_cells <= 2500:
                font = ("Consolas", max(5, min(14, cw - 4)))
                thr = int(self.threshold)
                for y in range(y0, y1):
                    for x in range(x0, x1):
                        v = self.gray[y * self.w + x]
                        bit = 1 if v >= thr else 0
                        c.create_text(
                            x * cw + cw / 2, y * cw + cw / 2, text=str(bit),
                            fill="#000000" if bit else "#ffffff", font=font,
                        )
            c.configure(scrollregion=(0, 0, self.w * cw, self.h * cw))
            return

        # 放大视野：格子 + 数字
        font = ("Consolas", max(5, min(14, cw - 4)))
        for y in range(y0, y1):
            base = y * self.w
            cy0 = y * cw
            cy1 = cy0 + cw
            for x in range(x0, x1):
                v = self.gray[base + x]
                cx0 = x * cw
                cx1 = cx0 + cw
                c.create_rectangle(cx0, cy0, cx1, cy1, fill=self._cell_color(v), outline="#3a3a3a")
                if self.use_numbers:
                    fill = "#000000" if v > 128 else "#ffffff"
                    c.create_text(
                        cx0 + cw / 2, cy0 + cw / 2, text=str(v), fill=fill, font=font,
                    )
        c.configure(scrollregion=(0, 0, self.w * cw, self.h * cw))

    # ---- 阈值 ----

    def _on_thr(self, val) -> None:
        # 优先用回调参数（Scale 拖动值），并同步 thr_var
        try:
            self.threshold = int(float(val))
        except (TypeError, ValueError):
            self.threshold = int(float(self.thr_var.get()))
        self.thr_var.set(self.threshold)
        if hasattr(self, "thr_label"):
            self.thr_label.configure(text=str(self.threshold))
        self._update_stat()
        self._schedule_redraw()

    def _otsu(self) -> None:
        t = otsu_threshold(self.gray)
        self.threshold = t
        self.thr_var.set(t)
        self.thr_label.configure(text=str(t))
        self.bin_var.set(True)
        self._update_stat()
        self._schedule_redraw()

    def _update_stat(self) -> None:
        if not hasattr(self, "stat_var"):
            return
        thr = int(self.threshold)
        above = sum(1 for v in self.gray if v >= thr)
        total = len(self.gray) or 1
        self.stat_var.set(f">={thr}: {above} ({above * 100 // total}%)  <{thr}: {total - above}")

    # ---- 缩放（以鼠标为中心）----

    def _keep_centered(self, ix: float, iy: float, vx: float, vy: float) -> None:
        total_w = self.w * self.cell
        total_h = self.h * self.cell
        if total_w <= 0 or total_h <= 0:
            return
        self.canvas.xview_moveto(max(0.0, min(1.0, (ix * self.cell - vx) / total_w)))
        self.canvas.yview_moveto(max(0.0, min(1.0, (iy * self.cell - vy) / total_h)))
        self._schedule_redraw()

    def _zoom_to_cursor(self, mx: int, my: int, new_cell: int) -> None:
        ix = self.canvas.canvasx(mx) / max(self.cell, 1)
        iy = self.canvas.canvasy(my) / max(self.cell, 1)
        self.cell = new_cell
        self.zoom_label.configure(text=f"{new_cell}px")
        self.zoom_scale.set(new_cell)
        self._redraw()
        self._keep_centered(ix, iy, mx, my)

    def _on_zoom(self, _val) -> None:
        if not hasattr(self, "canvas"):
            return
        new_cell = max(3, int(float(self.zoom_scale.get())))
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        ix = self.canvas.canvasx(cw / 2) / max(self.cell, 1)
        iy = self.canvas.canvasy(ch / 2) / max(self.cell, 1)
        self.cell = new_cell
        self.zoom_label.configure(text=f"{new_cell}px")
        self._redraw()
        self._keep_centered(ix, iy, cw / 2, ch / 2)

    def _wheel_by(self, delta: int, event=None) -> None:
        new = max(3, min(48, self.cell + delta))
        if new == self.cell:
            return
        if event is not None:
            self._zoom_to_cursor(event.x, event.y, new)
        else:
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            ix = self.canvas.canvasx(cw / 2) / max(self.cell, 1)
            iy = self.canvas.canvasy(ch / 2) / max(self.cell, 1)
            self.cell = new
            self.zoom_label.configure(text=f"{new}px")
            self.zoom_scale.set(new)
            self._redraw()
            self._keep_centered(ix, iy, cw / 2, ch / 2)

    def _on_wheel(self, event) -> None:
        self._wheel_by(1 if event.delta > 0 else -1, event)

    def _fit(self) -> None:
        cw_x = self.canvas.winfo_width() / max(self.w, 1)
        cw_y = self.canvas.winfo_height() / max(self.h, 1)
        self.cell = max(3, int(min(cw_x, cw_y)))
        self.zoom_scale.set(self.cell)
        self.zoom_label.configure(text=f"{self.cell}px")
        self._redraw()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    # ---- 拖拽平移 ----

    def _drag_start(self, event) -> None:
        self.canvas.scan_mark(event.x, event.y)

    def _drag_move(self, event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        self._schedule_redraw()

    # ---- 坐标提示 ----

    def _event_to_pixel(self, event) -> tuple[int, int] | None:
        x = self.canvas.canvasx(event.x) / self.cell
        y = self.canvas.canvasy(event.y) / self.cell
        px, py = int(x), int(y)
        if 0 <= px < self.w and 0 <= py < self.h:
            return px, py
        return None

    def _on_motion(self, event) -> None:
        p = self._event_to_pixel(event)
        if p is None:
            self.coord_var.set("拖拽平移 · 滚轮缩放（以鼠标为中心）")
            return
        px, py = p
        v = self.gray[py * self.w + px]
        thr = int(self.threshold)
        tag = "1" if v >= thr else "0"
        self.coord_var.set(f"({px},{py}) = {v}   bin={tag} thr={thr}")


def open_matrix_window(
    parent: tk.Misc,
    path: str | Path,
    title: str | None = None,
    palette=None,
) -> MatrixView | None:
    """从 PNG/JPG/BMP 打开矩阵窗口。"""
    from .extras import load_png_rgb, rgb_to_gray

    path = Path(path)
    try:
        if path.suffix.lower() in (".jpg", ".jpeg", ".bmp", ".gif"):
            from PIL import Image
            im = Image.open(path).convert("RGB")
            w, h = im.size
            rgb = im.tobytes()
        else:
            rgb, w, h = load_png_rgb(path)
        gray = rgb_to_gray(rgb, w, h)
    except Exception as exc:  # noqa: BLE001
        try:
            from tkinter import messagebox
            messagebox.showerror("像素矩阵", f"读取失败: {exc}")
        except Exception:  # noqa: BLE001
            pass
        return None
    return MatrixView(parent, gray, w, h, title=title or path.name, palette=palette)


def open_matrix_from_gray(
    parent: tk.Misc,
    gray: bytes,
    width: int,
    height: int,
    title: str = "像素矩阵",
    palette=None,
) -> MatrixView:
    return MatrixView(parent, gray, width, height, title=title, palette=palette)

