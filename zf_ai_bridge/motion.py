"""Tk事件循环驱动的轻量动效，不延迟按钮命令或阻塞图传线程。"""
from __future__ import annotations

import time
from tkinter import ttk


def mix(start, end, progress):
    a = [int(start[i:i+2], 16) for i in (1, 3, 5)]
    b = [int(end[i:i+2], 16) for i in (1, 3, 5)]
    return "#"+"".join(f"{round(x+(y-x)*progress):02x}" for x, y in zip(a, b))


class MotionButton(ttk.Button):
    """保留ttk键盘/禁用行为，独立样式避免一颗按钮带动整组变色。"""

    def __init__(self, parent, *, palette, enabled, **kwargs):
        self._palette, self._enabled = palette, enabled
        self._base_style = kwargs.pop("style", "TButton")
        self._job = None
        self._hover = self._pressed = False
        self._color = None
        self._offset = 0.
        self._motion_style = "Motion"+str(id(self))+"."+self._base_style
        command = kwargs.pop("command", None)

        def activate():
            self._pressed = False
            self._offset = 1.5 if self._enabled() else 0.
            self.animate()
            if command:
                return command()

        super().__init__(parent, style=self._motion_style, command=activate, **kwargs)
        self.bind("<Enter>", lambda e: self._pointer(True), add="+")
        self.bind("<Leave>", lambda e: self._pointer(False), add="+")
        self.bind("<ButtonPress-1>", lambda e: self._press(True), add="+")
        self.bind("<ButtonRelease-1>", lambda e: self._press(False), add="+")
        self.bind("<KeyPress-space>", lambda e: self._press(True), add="+")
        self.bind("<KeyRelease-space>", lambda e: self._press(False), add="+")
        self.bind("<Destroy>", self._destroyed, add="+")
        self.refresh()

    def configure(self, cnf=None, **kwargs):
        if "style" in kwargs:
            self._base_style = kwargs.pop("style")
            self._motion_style = "Motion"+str(id(self))+"."+self._base_style
            kwargs["style"] = self._motion_style
            result = super().configure(cnf, **kwargs)
            self.refresh()
            return result
        return super().configure(cnf, **kwargs)

    config = configure

    def _pointer(self, inside):
        self._hover = inside
        if not inside:
            self._pressed = False
        self.animate()

    def _press(self, pressed):
        if not self.instate(["disabled"]):
            self._pressed = pressed
            self.animate()

    def _target(self):
        c = self._palette()
        primary = self._base_style == "Primary.TButton"
        base = c["accent"] if primary else c["hover"] if self._base_style == "Selected.TButton" else c["card"]
        hover = mix(base, c["muted"] if primary else c["text"], .16 if primary else .07)
        pressed = mix(base, c["muted"] if primary else c["text"], .28 if primary else .14)
        return (pressed if self._pressed else hover if self._hover else base), (1.5 if self._pressed else 0.)

    def _draw(self, color, offset):
        self._color, self._offset = color, offset
        c = self._palette()
        style = ttk.Style(self)
        style.configure(self._motion_style, background=color, padding=(10, 8+round(offset), 10, 8-round(offset)),
                        foreground=c["on_accent"] if self._base_style == "Primary.TButton" else c["text"])
        # active/pressed不再跳变；disabled仍由原生状态处理。
        style.map(self._motion_style, background=[("disabled", c["side"])],
                  foreground=[("disabled", c["muted"])])

    def cancel(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def refresh(self):
        self.cancel()
        self._draw(*self._target())

    def animate(self):
        self.cancel()
        target, offset = self._target()
        if not self._enabled() or self.instate(["disabled"]):
            self._draw(target, offset)
            return
        color, initial_offset = self._color or target, self._offset
        started = time.monotonic()

        def tick():
            self._job = None
            progress = min(1., (time.monotonic()-started)/.14)
            eased = 1-(1-progress)**3
            self._draw(mix(color, target, eased), initial_offset+(offset-initial_offset)*eased)
            if progress < 1:
                self._job = self.after(16, tick)

        tick()

    def _destroyed(self, event):
        if event.widget is self:
            self.cancel()
