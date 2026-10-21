"""冻结干净原图后选四角；选择标记仅存在Canvas，不写入原图。"""
from __future__ import annotations

import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .image import write_png
from .perspective import Calibration


class PerspectiveWindow:
    ORDER = ("远左", "远右", "近右", "近左")

    def __init__(self, parent, rgb: bytes, width: int, height: int, camera_type: int, out_dir):
        self.rgb, self.size, self.camera_type = bytes(rgb), (width, height), camera_type
        self.out_dir = Path(out_dir)
        self.points = []
        self.calibration = None
        self.result = None
        self.win = tk.Toplevel(parent)
        self.win.title("逆透视实验 · 已冻结干净原图")
        self.win.geometry("1000x720")
        ttk.Label(self.win, text="固定摄像头，地面放已知尺寸的矩形。依次点击：远左 → 远右 → 近右 → 近左。\n"
                  "填写实测宽/长（厘米）。仅变换这块地面区域；本窗口不修改车端算法。").pack(anchor="w", padx=10, pady=8)
        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=8)
        self.width = tk.StringVar()
        self.length = tk.StringVar()
        self.scale = tk.StringVar(value="2")
        for label, var in (("实测宽cm", self.width), ("前后长cm", self.length), ("像素/cm", self.scale)):
            ttk.Label(bar, text=label).pack(side="left", padx=4)
            ttk.Entry(bar, textvariable=var, width=8).pack(side="left")
        self.binary = tk.BooleanVar(value=camera_type == 1 or set(rgb).issubset({0, 255}))
        ttk.Checkbutton(bar, text="二值图（最近邻）", variable=self.binary).pack(side="left", padx=8)
        ttk.Button(bar, text="生成俯视图", command=self.preview).pack(side="left", padx=4)
        buttons = ttk.Frame(self.win)
        buttons.pack(fill="x", padx=8, pady=6)
        for label, command in (("重选四点", self.reset), ("载入标定", self.load),
                               ("保存标定", self.save), ("导出原图+俯视图", self.export)):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=4)
        self.status = tk.StringVar(value="请选择远左角；原图已冻结，实时接收继续运行。")
        ttk.Label(self.win, textvariable=self.status).pack(anchor="w", padx=12)
        body = ttk.Frame(self.win)
        body.pack(fill="both", expand=True, padx=10, pady=8)
        self.factor = max(1, min(4, 460//width, 480//height))
        self.canvas = tk.Canvas(body, width=width*self.factor, height=height*self.factor,
                                background="#222", highlightthickness=0)
        self.canvas.pack(side="left", anchor="n")
        self.photo = tk.PhotoImage(data=b"P6\n%d %d\n255\n" % self.size + self.rgb, format="ppm").zoom(self.factor)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.bind("<Button-1>", self.click)
        output = ttk.Frame(body)
        output.pack(side="left", fill="both", expand=True, padx=12)
        self.output = tk.Canvas(output, background="#222", highlightthickness=0)
        xs, ys = ttk.Scrollbar(output, orient="horizontal", command=self.output.xview), ttk.Scrollbar(output, command=self.output.yview)
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
        for i, (x, y) in enumerate(self.points):
            x, y = (x+.5)*self.factor, (y+.5)*self.factor
            self.canvas.create_oval(x-4, y-4, x+4, y+4, outline="#ff4444", width=2, tags="point")
            self.canvas.create_text(x+6, y+8, anchor="nw", text=f"{i+1} {self.ORDER[i]}", fill="#ffcc44", tags="point")

    def click(self, event):
        x, y = event.x//self.factor, event.y//self.factor
        if len(self.points) >= 4 or not (0 <= x < self.size[0] and 0 <= y < self.size[1]):
            return
        self.points.append((x, y))
        self.invalidate()
        self.draw_points()
        self.status.set(f"请选择{self.ORDER[len(self.points)]}角" if len(self.points) < 4 else "四点已选，请填实测尺寸并生成俯视图")

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
            dest = Path(folder)/f"ipm_{time.time_ns()}"
            dest.mkdir()
            write_png(dest/"raw.png", *self.size, self.rgb)
            write_png(dest/"bird.png", *self.calibration.output_size, self.result)
            self.calibration.save(dest/"calibration.json")
            (dest/"frame.json").write_text(json.dumps(dict(cameraType=self.camera_type,
                binary_nearest=self.binary.get(), frozen=True, raw=True), indent=2), encoding="utf-8")
            self.status.set(f"已导出：{dest}")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.win)
