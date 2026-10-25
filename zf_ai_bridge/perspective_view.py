"""四点逆透视：冻结干净原图后选四角；选择标记只在 Canvas 上，不写入原图。"""
from __future__ import annotations

import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import theme, widgets
from .image import write_png
from .perspective import Calibration


class PerspectiveWindow:
    ORDER = ("远左", "远右", "近右", "近左")

    def __init__(self, parent, rgb: bytes, width: int, height: int, camera_type: int,
                 out_dir, palette=None):
        self.rgb, self.size, self.camera_type = bytes(rgb), (width, height), camera_type
        self.out_dir = Path(out_dir)
        self.points = []
        self.calibration = None
        self.result = None
        self.palette = palette or theme.find_palette(parent)
        p = self.palette
        self.win = tk.Toplevel(parent)
        self.win.title("逆透视实验 · 已冻结干净原图")
        self.win.geometry("1080x760")
        self.win.minsize(920, 640)
        self.win.configure(bg=p["bg"])
        self.win.palette = p

        shell = tk.Frame(self.win, bg=p["bg"])
        shell.pack(fill="both", expand=True, padx=20, pady=(16, 12))

        head = tk.Frame(shell, bg=p["bg"])
        head.pack(fill="x")
        titles = tk.Frame(head, bg=p["bg"])
        titles.pack(side="left")
        tk.Label(titles, text="四点逆透视", bg=p["bg"], fg=p["text"],
                 font=(theme.FONT_UI, 18, "bold")).pack(anchor="w")
        tk.Label(titles, text="固定相机，地面放已知尺寸的矩形；依次点击 远左 → 远右 → 近右 → 近左。",
                 bg=p["bg"], fg=p["muted"], font=(theme.FONT_UI, 9)).pack(anchor="w", pady=(3, 0))
        tools = tk.Frame(head, bg=p["bg"])
        tools.pack(side="right", pady=(4, 0))
        for label, command in (("重选四点", self.reset), ("载入标定", self.load),
                               ("保存标定", self.save), ("导出原图+俯视图", self.export)):
            widgets.RoundedButton(tools, label, command, p, height=32,
                                  bg=p["bg"]).pack(side="right", padx=(6, 0))

        params_card = widgets.Card(shell, p, padding=(16, 12), radius=14)
        params_card.pack(fill="x", pady=(14, 0))
        row = params_card.body
        tk.Label(row, text="实测尺寸", bg=p["surface"], fg=p["text"],
                 font=(theme.FONT_UI, 10, "bold")).pack(side="left", padx=(0, 14))
        self.width = tk.StringVar()
        self.length = tk.StringVar()
        self.scale = tk.StringVar(value="2")
        for label, var in (("宽 cm", self.width), ("前后长 cm", self.length), ("像素/cm", self.scale)):
            tk.Label(row, text=label, bg=p["surface"], fg=p["muted"],
                     font=(theme.FONT_UI, 9)).pack(side="left", padx=(0, 6))
            ttk.Entry(row, textvariable=var, width=8).pack(side="left", padx=(0, 14))
        self.binary = tk.BooleanVar(value=camera_type == 1 or set(rgb).issubset({0, 255}))
        widgets.CheckRow(row, self.palette, "二值图（最近邻）", self.binary).pack(side="left", padx=(0, 16))
        widgets.RoundedButton(row, "生成俯视图", self.preview, p, kind="primary",
                              height=32, bg=p["surface"]).pack(side="left")

        self.status = tk.StringVar(value="请选择远左角；原图已冻结，实时接收继续运行。")
        tk.Label(shell, textvariable=self.status, bg=p["bg"], fg=p["muted"],
                 font=(theme.FONT_UI, 9)).pack(anchor="w", pady=(12, 0))

        body = tk.Frame(shell, bg=p["bg"])
        body.pack(fill="both", expand=True, pady=(10, 0))
        source_card = widgets.Card(body, p, padding=(12, 12), radius=14)
        source_card.pack(side="left", fill="both", expand=True, padx=(0, 14))
        left = source_card.body
        tk.Label(left, text="冻结原图 · 点击选角", bg=p["surface"], fg=p["muted"],
                 font=(theme.FONT_UI, 9)).pack(anchor="w", pady=(0, 8))
        self.factor = max(1, min(4, 460 // width, 480 // height))
        self.canvas = tk.Canvas(left, width=width * self.factor, height=height * self.factor,
                                background=p["canvas"], highlightthickness=1,
                                highlightbackground=p["border"], bd=0)
        self.canvas.pack()
        self.photo = tk.PhotoImage(
            data=b"P6\n%d %d\n255\n" % self.size + self.rgb, format="ppm").zoom(self.factor)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.bind("<Button-1>", self.click)

        output_card = widgets.Card(body, p, padding=(12, 12), radius=14)
        output_card.pack(side="left", fill="both", expand=True)
        right = output_card.body
        tk.Label(right, text="俯视图 · 仅变换四角内地面", bg=p["surface"], fg=p["muted"],
                 font=(theme.FONT_UI, 9)).pack(anchor="w", pady=(0, 8))
        self.output = tk.Canvas(right, background=p["canvas"], highlightthickness=0, bd=0)
        xs = ttk.Scrollbar(right, orient="horizontal", command=self.output.xview)
        ys = ttk.Scrollbar(right, command=self.output.yview)
        self.output.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
        xs.pack(side="bottom", fill="x")
        ys.pack(side="right", fill="y")
        self.output.pack(fill="both", expand=True)

    def invalidate(self):
        self.calibration = self.result = None
        self.output.delete("all")

    def reset(self):
        self.points.clear()
        self.canvas.delete("point")
        self.invalidate()
        self.status.set("请选择远左角")

    def draw_points(self):
        self.canvas.delete("point")
        p = self.palette
        for i, (x, y) in enumerate(self.points):
            x, y = (x + .5) * self.factor, (y + .5) * self.factor
            self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5, outline=p["accent"],
                                    width=2, tags="point")
            self.canvas.create_text(x + 9, y + 9, anchor="nw", text=f"{i + 1} {self.ORDER[i]}",
                                    fill=p["accent"], font=(theme.FONT_UI, 9, "bold"),
                                    tags="point")

    def click(self, event):
        x, y = event.x // self.factor, event.y // self.factor
        if len(self.points) >= 4 or not (0 <= x < self.size[0] and 0 <= y < self.size[1]):
            return
        self.points.append((x, y))
        self.invalidate()
        self.draw_points()
        self.status.set(f"请选择{self.ORDER[len(self.points)]}角" if len(self.points) < 4
                        else "四点已选，请填实测尺寸并生成俯视图")

    def preview(self):
        try:
            cal = Calibration(self.size, self.points, self.width.get(), self.length.get(), self.scale.get())
            result = cal.warp(self.rgb, self.size, binary=self.binary.get())
            self.calibration, self.result = cal, result
            w, h = cal.output_size
            self.bird_photo = tk.PhotoImage(data=b"P6\n%d %d\n255\n" % (w, h) + result, format="ppm")
            self.output.delete("all")
            self.output.create_image(0, 0, anchor="nw", image=self.bird_photo)
            self.output.configure(scrollregion=(0, 0, w, h))
            self.status.set(f"俯视图 {w}×{h}；范围仅限四角内。摄像头移动后须重新标定。")
            return True
        except (ValueError, TypeError) as exc:
            self.invalidate()
            messagebox.showerror("无法逆透视", str(exc), parent=self.win)
            return False

    def save(self):
        if not self.preview():
            return
        path = filedialog.asksaveasfilename(parent=self.win, title="保存四点标定", defaultextension=".json",
                                            initialdir=self.out_dir, initialfile="perspective.local.json")
        if path:
            try:
                self.calibration.save(path)
            except OSError as exc:
                messagebox.showerror("保存失败", str(exc), parent=self.win)

    def load(self):
        path = filedialog.askopenfilename(parent=self.win, title="载入标定", filetypes=[("标定JSON", "*.json")])
        if not path:
            return
        try:
            cal = Calibration.load(path)
            if cal.source_size != self.size:
                raise ValueError("标定与当前原图尺寸不同")
            self.points = list(cal.points)
            self.width.set(str(cal.width_cm))
            self.length.set(str(cal.length_cm))
            self.scale.set(str(cal.pixels_per_cm))
            self.draw_points()
            self.preview()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            messagebox.showerror("载入失败", str(exc), parent=self.win)

    def export(self):
        if not self.preview():
            return
        folder = filedialog.askdirectory(parent=self.win, title="选择导出目录", initialdir=self.out_dir)
        if not folder:
            return
        try:
            dest = Path(folder) / f"ipm_{time.time_ns()}"
            dest.mkdir()
            write_png(dest / "raw.png", *self.size, self.rgb)
            write_png(dest / "bird.png", *self.calibration.output_size, self.result)
            self.calibration.save(dest / "calibration.json")
            (dest / "frame.json").write_text(json.dumps(dict(cameraType=self.camera_type,
                binary_nearest=self.binary.get(), frozen=True, raw=True), indent=2), encoding="utf-8")
            self.status.set(f"已导出：{dest}")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.win)
