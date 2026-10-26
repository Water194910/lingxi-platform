"""Advanced, non-destructive ground calibration workspace for LingXi."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import theme, widgets
from .theme import sp
from .image import write_png
from .ipm import GroundMap, ORDER, SIZE, fit, matrix, number, transform


class NumberControl(tk.Frame):
    """滑条粗调 + 数值框精调，二者双向联动；输入非法时不钳制。"""

    def __init__(self, parent, label, unit, low, high, value, callback, *, step=1):
        self.palette = theme.find_palette(parent)
        p = self.palette
        super().__init__(parent, bg=p["surface"])
        self.low, self.high, self.step, self.label = low, high, step, label
        self.callback, self._syncing = callback, False
        head = tk.Frame(self, bg=p["surface"])
        head.pack(fill="x")
        tk.Label(head, text=label, bg=p["surface"], fg=p["text_secondary"],
                 font=(theme.FONT_UI, 9)).pack(side="left")
        tk.Label(head, text=unit, bg=p["surface"], fg=p["muted"],
                 font=(theme.FONT_UI, 8)).pack(side="left", padx=(sp(6), sp(0)))
        row = tk.Frame(self, bg=p["surface"])
        row.pack(fill="x", pady=(sp(5), sp(0)))
        self.text = tk.StringVar(value=str(value))
        start = float(value) if value not in ("", None) else float(low)
        self.slider = widgets.Slider(row, p, low, high, start, self._slide,
                                     width=sp(168), height=sp(24), step=step)
        self.slider.pack(side="left", fill="x", expand=True, padx=(sp(0), sp(8)))
        self.entry = ttk.Entry(row, width=sp(9), textvariable=self.text)
        self.entry.pack(side="right")
        self.text.trace_add("write", self._typed)

    def _slide(self, value):
        if not self._syncing:
            self.text.set(f"{value:.3f}".rstrip("0").rstrip("."))

    def _typed(self, *_):
        try:
            value = self.get()
        except ValueError:
            return
        self._syncing = True
        self.slider.set(value)
        self._syncing = False
        self.callback()

    def set_enabled(self, enabled):
        self.entry.configure(state="normal" if enabled else "disabled")
        self.slider.set_enabled(enabled)

    def get(self):
        value = number(self.text.get(), self.label)
        if not self.low <= value <= self.high:
            raise ValueError(f"{self.label}范围：{self.low}～{self.high}")
        return value

    def set(self, value):
        self.text.set('' if value is None else str(value))

    def apply_palette(self, palette):
        self.palette = palette
        self.configure(bg=palette["surface"])
        for child in self.winfo_children():
            if isinstance(child, tk.Frame):
                child.configure(bg=palette["surface"])
                for sub in child.winfo_children():
                    if isinstance(sub, tk.Label):
                        sub.configure(bg=palette["surface"])
        self.slider.apply_palette(palette)


class AdvancedPerspectiveWindow:
    def __init__(self, parent, rgb, width, height, camera_type, out_dir, *, frame_provider=None, palette=None, capture_provider=None):
        if (width, height) != SIZE or len(rgb) != 188*120*3:
            raise ValueError("高级标定使用188×120原图。其他尺寸请使用四点标定；不会自动拉伸图片。")
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.frame_provider = frame_provider
        self.capture_provider = capture_provider or frame_provider
        self.palette = palette or theme.LIGHT
        self.samples = [dict(distance_mm='', points=[], rgb=None, camera_type=2, enabled=i == 0) for i in range(3)]
        self.samples[0].update(rgb=bytes(rgb), camera_type=camera_type)
        self.result = None
        self._active, self._revision, self._loading = 0, 0, True
        self._closed, self._drag = False, None
        self._future, self._pending, self._debounce = None, None, None
        self._live_key = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lingxi-ipm')
        self.win = tk.Toplevel(parent)
        self.win.title("灵犀 · 高级逆透视标定")
        self.win.geometry(f'{sp(1240)}x{sp(820)}')
        self.win.minsize(1080, 720)
        self.win.configure(bg=self.palette['bg'])
        self.win.palette = self.palette
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        self.win.bind('<Destroy>', self._destroyed, add='+')
        self._build()
        self.apply_palette(self.palette)
        self._loading = False
        self._show_sample()
        self._poll_job = self.win.after(80, self._poll)

    def _build(self):
        p = self.palette
        self.win.configure(bg=p['bg'])
        self.win.palette = p
        shell = tk.Frame(self.win, bg=p['bg'])
        shell.pack(fill='both', expand=True, padx=sp(20), pady=(sp(16), sp(12)))

        header = tk.Frame(shell, bg=p['bg'])
        header.pack(fill='x')
        titles = tk.Frame(header, bg=p['bg'])
        titles.pack(side='left')
        tk.Label(titles, text='高级逆透视', bg=p['bg'], fg=p['text'],
                 font=(theme.FONT_UI, 18, 'bold')).pack(anchor='w')
        tk.Label(titles, text='固定相机 · 实测矩形 · 多组样本拟合 · 导出 C 查表',
                 bg=p['bg'], fg=p['muted'], font=(theme.FONT_UI, 9)).pack(anchor='w', pady=(sp(3), sp(0)))
        actions = tk.Frame(header, bg=p['bg'])
        actions.pack(side='right', pady=(sp(4), sp(0)))
        for label, command in (("重置视图参数", self.reset_view), ("导出结果…", self.export),
                               ("保存标定…", self.save), ("载入标定…", self.load)):
            widgets.RoundedButton(actions, label, command, p, height=sp(32),
                                  bg=p['bg']).pack(side='right', padx=(sp(6), sp(0)))

        body = tk.Frame(shell, bg=p['bg'])
        body.pack(fill='both', expand=True, pady=(sp(14), sp(0)))

        left_card = widgets.Card(body, p, padding=(sp(14), sp(12)), radius=sp(14))
        left_card.pack(side='left', fill='y')
        left_card.configure(width=sp(420))
        left_card.pack_propagate(False)
        settings = left_card.body
        self.tabs = widgets.SegmentedControl(settings, p, ('实测与样本', '鸟瞰视图', '矩阵'),
                                             self._select_tab, height=sp(32), bg=p['surface'])
        self.tabs.pack(fill='x', pady=(sp(0), sp(10)))
        host = tk.Frame(settings, bg=p['surface'])
        host.pack(fill='both', expand=True)
        page_frames = []
        self.scrollers = []
        panels = []
        for title in ('实测与样本', '鸟瞰视图', '矩阵'):
            page = tk.Frame(host, bg=p['surface'])
            page_frames.append(page)
            scroller = tk.Canvas(page, highlightthickness=0, width=sp(340), bg=p['surface'])
            self.scrollers.append(scroller)
            scrollbar = ttk.Scrollbar(page, orient='vertical', command=scroller.yview)
            scroller.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side='right', fill='y')
            scroller.pack(side='left', fill='both', expand=True)
            panel = tk.Frame(scroller, bg=p['surface'])
            item = scroller.create_window(0, 0, anchor='nw', window=panel)
            panel.bind('<Configure>', lambda _, c=scroller: c.configure(scrollregion=c.bbox('all')))
            scroller.bind('<Configure>', lambda event, c=scroller, i=item: c.itemconfigure(i, width=event.width))
            panels.append(panel)
        self._panel_pages = page_frames
        page_frames[0].pack(fill='both', expand=True)
        measure, view, advanced = panels
        tk.Label(measure, text='所有实测尺寸单位为毫米；不预填其他车辆参数。', bg=p['surface'],
                 fg=p['muted'], font=(theme.FONT_UI, 8), wraplength=sp(330),
                 justify='left').pack(anchor='w')
        self.width = NumberControl(measure, '矩形实测宽度', 'mm', 1, 3000, '', self.changed)
        self.width.pack(fill='x', pady=sp(9))
        self.length = NumberControl(measure, '矩形实测长度', 'mm', 1, 5000, '', self.changed)
        self.length.pack(fill='x', pady=sp(9))
        self.estimate = tk.BooleanVar(value=False)
        ttk.Checkbutton(measure, text='不知道长度：用多组距离估算', variable=self.estimate, command=self._estimate_changed).pack(anchor='w')
        tk.Label(measure, text='多组样本：相机不动，同一个矩形沿同一中心线前后移动。\n距离取同一固定参考点到矩形近边的地面距离。', bg=p['surface'],
                 fg=p['muted'], font=(theme.FONT_UI, 8), wraplength=sp(330),
                 justify='left').pack(anchor='w', pady=sp(8))
        self.sample_choice = ttk.Combobox(measure, state='readonly', values=('样本 1', '样本 2', '样本 3'))
        self.sample_choice.current(0)
        self.sample_choice.pack(fill='x')
        self.sample_choice.bind('<<ComboboxSelected>>', self._select_sample)
        self.enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(measure, text='此样本参与拟合', variable=self.enabled, command=self.changed).pack(anchor='w', pady=sp(5))
        self.distance = NumberControl(measure, '参考点到近边距离', 'mm', 0, 6000, '', self.changed)
        self.distance.pack(fill='x', pady=sp(7))
        row = tk.Frame(measure, bg=p['surface'])
        row.pack(fill='x', pady=sp(7))
        widgets.RoundedButton(row, '取当前帧', self.capture, p, height=sp(32),
                              bg=p['surface']).pack(side='left', fill='x', expand=True, padx=(sp(0), sp(4)))
        widgets.RoundedButton(row, '导入原图…', self.import_image, p, height=sp(32),
                              bg=p['surface']).pack(side='left', fill='x', expand=True)
        row = tk.Frame(measure, bg=p['surface'])
        row.pack(fill='x', pady=(sp(0), sp(5)))
        tk.Label(row, text='下一点', bg=p['surface'], fg=p['muted'],
                 font=(theme.FONT_UI, 9)).pack(side='left')
        self.corner = ttk.Combobox(row, values=ORDER, state='readonly', width=sp(8))
        self.corner.current(0)
        self.corner.pack(side='left', padx=sp(6))
        widgets.RoundedButton(row, '清除本组角点', self.clear_points, p, height=sp(30),
                              bg=p['surface']).pack(side='right')
        self.coords = []
        table = tk.Frame(measure, bg=p['surface'])
        table.pack(fill='x')
        for i, label in enumerate(ORDER):
            tk.Label(table, text=label, bg=p['surface'], fg=p['text_secondary'],
                     font=(theme.FONT_UI, 9)).grid(row=i, column=0, padx=(sp(0), sp(8)), pady=sp(2), sticky='w')
            pair = []
            for j, high in enumerate((187, 119)):
                var = tk.StringVar()
                ttk.Entry(table, textvariable=var, width=sp(8)).grid(row=i, column=j+1, padx=sp(3))
                var.trace_add('write', self.changed)
                pair.append(var)
            self.coords.append(pair)
        tk.Label(measure, text='坐标为原图 x、y；点击选四角，拖动圆点微调。\n可用数值框精调到小数像素。', bg=p['surface'],
                 fg=p['muted'], font=(theme.FONT_UI, 8), wraplength=sp(330),
                 justify='left').pack(anchor='w', pady=sp(7))

        tk.Label(view, text='以下参数调整鸟瞰画幅，不会改变实测数据。', bg=p['surface'],
                 fg=p['muted'], font=(theme.FONT_UI, 8), wraplength=sp(330),
                 justify='left').pack(anchor='w', pady=(sp(0), sp(8)))
        self.target = NumberControl(view, '矩形显示宽度', 'px', 10, 180, 120, self.changed, step=.5)
        self.bottom = NumberControl(view, '底行对应距离', 'mm', 0, 3000, 400, self.changed, step=5)
        self.center = NumberControl(view, '横向中心', 'px', 0, 187, 94, self.changed, step=.5)
        for control in (self.target, self.bottom, self.center):
            control.pack(fill='x', pady=sp(11))
        self.grid = tk.BooleanVar(value=True)
        ttk.Checkbutton(view, text='显示参考网格（不写入导出图片）', variable=self.grid, command=self.draw).pack(anchor='w', pady=sp(8))
        self.live = tk.BooleanVar(value=False)
        ttk.Checkbutton(view, text='用最新实时原图预览鸟瞰', variable=self.live, command=self._live_changed,
                        state='normal' if self.frame_provider else 'disabled').pack(anchor='w', pady=sp(5))
        tk.Label(view, text='标定样本始终冻结；实时预览只更新右图。\n无效区域在预览中以粉色区分，导出时另附有效掩码。\n原图、算法二值图均采用最近邻，保持像素值。',
                 bg=p['surface'], fg=p['muted'], font=(theme.FONT_UI, 8), wraplength=sp(330),
                 justify='left').pack(anchor='w', pady=sp(8))
        widgets.RoundedButton(view, '恢复显示默认值', self.reset_view, p, height=sp(32),
                              bg=p['surface']).pack(anchor='w', pady=sp(8))

        self.mode = tk.StringVar(value='samples')
        ttk.Radiobutton(advanced, text='由实测样本计算（推荐）', variable=self.mode, value='samples', command=self.changed).pack(anchor='w')
        ttk.Radiobutton(advanced, text='使用手动 H_inv 矩阵', variable=self.mode, value='matrix', command=self.changed).pack(anchor='w', pady=sp(6))
        tk.Label(advanced, text='H_inv：鸟瞰坐标 → 原图坐标。\n粘贴9个数字或C矩阵，支持科学计数法。\n手动矩阵的物理尺度由提供者确认；视图滑条不改写它。',
                 bg=p['surface'], fg=p['muted'], font=(theme.FONT_UI, 8), wraplength=sp(330),
                 justify='left').pack(anchor='w', pady=sp(8))
        self.matrix_box = tk.Text(advanced, height=sp(7), width=sp(30), undo=True, relief='flat',
                                  borderwidth=0, highlightthickness=1,
                                  bg=p['surface_alt'], fg=p['text'],
                                  insertbackground=p['text'], font=(theme.FONT_MONO, 9))
        self.matrix_box.pack(fill='x')
        self.matrix_box.bind('<<Modified>>', self._matrix_changed)
        widgets.RoundedButton(advanced, '复制当前 H_inv', self.copy_matrix, p, height=sp(32),
                              bg=p['surface']).pack(anchor='w', pady=sp(8))
        tk.Label(advanced, text='导出的两张C查找表合计约88.1 KiB。\n这里只导出文件，不覆盖或烧录车辆工程。\n镜头畸变需要另外标定；拟合误差不是实车精度保证。',
                 bg=p['surface'], fg=p['muted'], font=(theme.FONT_UI, 8), wraplength=sp(330),
                 justify='left').pack(anchor='w', pady=sp(8))

        right_card = widgets.Card(body, p, padding=(sp(14), sp(12)), radius=sp(14))
        right_card.pack(side='left', fill='both', expand=True, padx=(sp(14), sp(0)))
        right = right_card.body
        self.sample_status = tk.StringVar(value='样本1 · 已冻结原图')
        tk.Label(right, textvariable=self.sample_status, bg=p['surface'], fg=p['text_secondary'],
                 font=(theme.FONT_UI, 10), wraplength=sp(650),
                 justify='left').pack(anchor='w', pady=(sp(0), sp(8)))
        images = tk.Frame(right, bg=p['surface'])
        images.pack(fill='both', expand=True)
        images.columnconfigure((0, 1), weight=1)
        images.rowconfigure(1, weight=1)
        tk.Label(images, text='标定原图 · 选点不会污染像素', bg=p['surface'], fg=p['muted'],
                 font=(theme.FONT_UI, 9)).grid(row=0, column=0, sticky='w')
        self.bird_title = tk.StringVar(value='鸟瞰图 · 等待标定')
        tk.Label(images, textvariable=self.bird_title, bg=p['surface'], fg=p['muted'],
                 font=(theme.FONT_UI, 9)).grid(row=0, column=1, sticky='w')
        self.raw = tk.Canvas(images, highlightthickness=0, bd=0, bg=p['canvas'])
        self.bird = tk.Canvas(images, highlightthickness=0, bd=0, bg=p['canvas'])
        for col, canvas in enumerate((self.raw, self.bird)):
            shell = tk.Frame(images, bg=p['border_soft'])
            shell.grid(row=1, column=col, sticky='nsew', padx=(sp(0), sp(8)) if col == 0 else 0, pady=(sp(6), sp(0)))
            canvas.configure(highlightthickness=0)
            canvas.place(in_=shell, x=1, y=1, relwidth=1, relheight=1, width=-2, height=-2)
            canvas.bind('<Configure>', lambda _: self.draw())
        self.raw.bind('<Button-1>', self.click)
        self.raw.bind('<B1-Motion>', self.drag)
        self.raw.bind('<ButtonRelease-1>', lambda _: setattr(self, '_drag', None))
        self.metrics = tk.StringVar(value='请填写实测宽度、长度、近边距离，并选择四角。')
        tk.Label(right, textvariable=self.metrics, bg=p['surface'], fg=p['text_secondary'],
                 font=(theme.FONT_UI, 9), wraplength=sp(650),
                 justify='left').pack(fill='x', pady=(sp(10), sp(0)))
        self.status = tk.StringVar(value='实时接收继续运行；本窗口只使用干净原始像素。')
        tk.Label(shell, textvariable=self.status, bg=p['bg'], fg=p['muted'],
                 font=(theme.FONT_UI, 9), wraplength=sp(1100),
                 justify='left').pack(fill='x', pady=(sp(12), sp(0)))

        def bind_scroll(widget, canvas):
            def scroll(event):
                canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
                return 'break'
            widget.bind('<MouseWheel>', scroll)
            for child in widget.winfo_children():
                bind_scroll(child, canvas)
        for panel, canvas in zip(panels, self.scrollers):
            bind_scroll(panel, canvas)
        self._restyle_cards(shell)

    def _restyle_cards(self, widget):
        """卡片内的 ttk 勾选框要换成卡片底色样式，否则会有色差块。"""
        for child in widget.winfo_children():
            if child.winfo_class() == 'TCheckbutton':
                child.configure(style='Card.TCheckbutton')
            self._restyle_cards(child)

    def _select_tab(self, index):
        for i, page in enumerate(self._panel_pages):
            if i == index:
                page.pack(fill='both', expand=True)
            else:
                page.pack_forget()
        self.draw()

    def _estimate_changed(self):
        self.length.set_enabled(not self.estimate.get())
        self.changed()

    def _matrix_changed(self, _):
        if self.matrix_box.edit_modified():
            self.matrix_box.edit_modified(False)
            if self.mode.get() == 'matrix':
                self.changed()

    def _store_sample(self):
        sample = self.samples[self._active]
        sample['enabled'] = self.enabled.get()
        sample['distance_mm'] = self.distance.text.get()
        sample['points'] = [[v.get() for v in pair] for pair in self.coords]

    def _show_sample(self):
        self._loading = True
        sample = self.samples[self._active]
        self.enabled.set(sample['enabled'])
        self.distance.set(sample['distance_mm'])
        points = sample['points']
        for i, pair in enumerate(self.coords):
            for j, var in enumerate(pair):
                var.set(str(points[i][j]) if i < len(points) else '')
        self.corner.current(next((i for i, p in enumerate(points) if '' in p), min(len(points), 3)))
        self.sample_status.set(f'样本{self._active+1} · '+('已冻结188×120原图' if sample['rgb'] else '尚未取图，请先取当前帧或导入'))
        self._loading = False
        self.draw()

    def _select_sample(self, _=None):
        self._store_sample()
        self._active = self.sample_choice.current()
        self._show_sample()
        self._live_key = None
        self._refresh_bird()

    def changed(self, *_):
        if self._loading or self._closed:
            return
        self._revision += 1
        self.result = None
        self._bird_rgb = None
        self._store_sample()
        self.metrics.set('参数已修改，正在等待有效输入 / 重新计算…')
        self.draw()
        if self._debounce:
            self.win.after_cancel(self._debounce)
        self._debounce = self.win.after(220, self._queue)

    def _configuration(self):
        self._store_sample()
        return dict(schema='lingxi.ipm/1', source_size=list(SIZE), mode=self.mode.get(),
                    width_mm=self.width.text.get(), length_mm='' if self.estimate.get() else self.length.text.get(),
                    estimate_length=self.estimate.get(), target_px=self.target.get(), bottom_mm=self.bottom.get(),
                    center_px=self.center.get(), inverse_text=self.matrix_box.get('1.0', 'end').strip(),
                    samples=[{k: copy.deepcopy(v) for k, v in sample.items() if k != 'rgb'} for sample in self.samples])

    @staticmethod
    def _compute(config):
        if config['mode'] == 'matrix':
            return GroundMap(matrix(config['inverse_text']))
        samples = [s for s in config['samples'] if s['enabled']]
        return fit(samples, config['width_mm'], config['length_mm'], config['target_px'], config['bottom_mm'], config['center_px'])

    def _queue(self):
        if self._debounce:
            self.win.after_cancel(self._debounce)
        self._debounce = None
        try:
            config = self._configuration()
            if config['mode'] == 'samples':
                self.width.get()
                if not self.estimate.get():
                    self.length.get()
                for sample in self.samples:
                    if sample['enabled'] and sample['rgb'] is None:
                        raise ValueError('参与拟合的每组样本都需要原图，请先取图')
        except (ValueError, TypeError, KeyError) as exc:
            self._pending = None
            self.metrics.set(str(exc))
            return
        self._pending = (self._revision, config)
        self._submit()

    def _submit(self):
        if self._future is None and self._pending:
            revision, config = self._pending
            self._pending = None
            self._future = (revision, self.executor.submit(self._compute, config))

    def _poll(self):
        if self._closed:
            return
        if self._future and self._future[1].done():
            revision, future = self._future
            self._future = None
            try:
                result = future.result()
            except Exception as exc:
                if revision == self._revision:
                    self.metrics.set(f'暂不能标定：{exc}')
            else:
                if revision == self._revision:
                    self.result = result
                    self._live_key = None
                    metrics = [f'有效区域 {result.coverage:.1%}']
                    if result.rms is not None:
                        metrics += [f'重投影RMS {result.rms:.3f} px', f'矩形长度 {result.length_mm:.1f} mm',
                                    '各组误差 '+ ' / '.join(f'{e:.2f}px' for e in result.errors)]
                        if result.rms > 2:
                            metrics += ['误差较大，请复核角点、距离、镜头畸变和相机是否移动。']
                    else:
                        metrics += ['手动矩阵：未验证实测尺度。']
                    self.metrics.set('  ·  '.join(metrics))
                    self._refresh_bird()
            self._submit()
        if self.live.get() and self.result:
            self._refresh_bird()
        self._poll_job = self.win.after(120, self._poll)

    def _live_changed(self):
        self._live_key = None
        self._refresh_bird()

    def _frame(self, *, capture=False):
        provider = self.capture_provider if capture else self.frame_provider
        frame = provider() if provider else None
        if not frame:
            raise ValueError('尚无当前帧，请先接收图传或导入图片')
        kind, w, h, rgb = frame
        if (w, h) != SIZE:
            raise ValueError('需要188×120原图，不会自动缩放')
        return dict(rgb=bytes(rgb), camera_type=kind)

    def capture(self):
        try:
            self.samples[self._active].update(self._frame(capture=True), points=[])
            self._show_sample()
            self.changed()
            self.status.set('已冻结当前原图；请重新选四角。其它样本保持不变。')
        except ValueError as exc:
            self.status.set(str(exc))

    def import_image(self):
        path = filedialog.askopenfilename(parent=self.win, title='导入188×120干净原图', filetypes=[('原图', '*.png *.bmp *.jpg *.jpeg')])
        if not path:
            return
        try:
            with Image.open(path) as image:
                if image.size != SIZE:
                    raise ValueError('图片须为188×120原图；截图或不同尺寸图片不会自动拉伸')
                rgb = image.convert('RGB').tobytes()
            self.samples[self._active].update(rgb=rgb, camera_type=1 if set(rgb) <= {0, 255} else 2, points=[])
            self._show_sample()
            self.changed()
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法导入', str(exc), parent=self.win)

    def clear_points(self):
        self.samples[self._active]['points'] = []
        self._show_sample()
        self.changed()

    def reset_view(self):
        self._loading = True
        for control, value in ((self.target, 120), (self.bottom, 400), (self.center, 94)):
            control.set(value)
        self._loading = False
        self.changed()

    def _position(self, event):
        scale, ox, oy = self._raw_geometry
        x, y = (event.x-ox)/scale-.5, (event.y-oy)/scale-.5
        return (min(187., max(0., x)), min(119., max(0., y))) if -.5 <= x <= 187.5 and -.5 <= y <= 119.5 else None

    def _set_point(self, i, point):
        self._loading = True
        for var, value in zip(self.coords[i], point):
            var.set(f'{value:.2f}')
        self._loading = False
        self.changed()

    def click(self, event):
        if not self.samples[self._active]['rgb']:
            self.status.set('请先为当前样本取图，再选择角点。')
            return
        point = self._position(event)
        if point is None:
            return
        for i, coords in self._valid_points():
            if sum((a-b)**2 for a, b in zip(coords, point))**.5*self._raw_geometry[0] < 12:
                self._drag = i
                self.corner.current(i)
                return
        i = self.corner.current()
        self._set_point(i, point)
        self.corner.current((i+1) % 4)
        self._drag = i

    def drag(self, event):
        point = self._position(event)
        if point is not None and self._drag is not None:
            self._set_point(self._drag, point)

    def _valid_points(self):
        for i, pair in enumerate(self.coords):
            try:
                x, y = [number(v.get(), '角点') for v in pair]
                if 0 <= x <= 187 and 0 <= y <= 119:
                    yield i, (x, y)
            except ValueError:
                continue

    def _paint(self, canvas, rgb, *, mask=None):
        canvas.delete('all')
        cw, ch = max(10, canvas.winfo_width()), max(10, canvas.winfo_height())
        scale = min((cw-10)/188, (ch-10)/120)
        w, h = max(1, round(188*scale)), max(1, round(120*scale))
        ox, oy = (cw-w)/2, (ch-h)/2
        if rgb is not None:
            image = Image.frombytes('RGB', SIZE, rgb)
            if mask:
                invalid = Image.frombytes('L', SIZE, bytes(255-v for v in mask))
                image.paste((229, 172, 187), mask=invalid)
            canvas._photo = ImageTk.PhotoImage(image.resize((w, h), Image.Resampling.NEAREST), master=self.win)
            canvas.create_image(ox, oy, anchor='nw', image=canvas._photo)
            if self.grid.get():
                for x in range(0, 188, 20):
                    canvas.create_line(ox+x*scale, oy, ox+x*scale, oy+h, fill='#7793a4', dash=(2, 4))
                for y in range(0, 120, 20):
                    canvas.create_line(ox, oy+y*scale, ox+w, oy+y*scale, fill='#7793a4', dash=(2, 4))
        else:
            canvas.create_text(cw/2, ch/2, text='等待原图' if canvas is self.raw else '等待有效标定', fill=self.palette['muted'])
        return scale, ox, oy

    def draw(self):
        if self._closed or not hasattr(self, 'raw'):
            return
        self._raw_geometry = self._paint(self.raw, self.samples[self._active]['rgb'])
        scale, ox, oy = self._raw_geometry
        for i, (x, y) in self._valid_points():
            x, y = ox+(x+.5)*scale, oy+(y+.5)*scale
            self.raw.create_oval(x-5, y-5, x+5, y+5, outline='#ef5350', width=sp(2))
            self.raw.create_text(x+8, y-9, text=ORDER[i], fill='#d23c3c', anchor='w')
        self._paint(self.bird, getattr(self, '_bird_rgb', None), mask=self.result.mask if self.result else None)

    def _refresh_bird(self):
        if not self.result:
            return
        try:
            sample = self._frame() if self.live.get() else self.samples[self._active]
            rgb = sample['rgb']
            key = (self._revision, rgb)
            if rgb is None:
                self._bird_rgb = None
            elif key != self._live_key:
                self._bird_rgb = self.result.warp(rgb)
            changed = key != self._live_key
            self._live_key = key
            self.bird_title.set('鸟瞰图 · 最新实时原图' if self.live.get() else f'鸟瞰图 · 冻结样本{self._active+1}')
            if changed:
                self.draw()
        except ValueError as exc:
            self.status.set(str(exc))

    def _ready(self):
        if self.result is None:
            self.status.set('请先完成有效标定，等待计算结束后再保存或导出。')
            return False
        return True

    def copy_matrix(self):
        if self._ready():
            text = 'const float H_inv[3][3] = {\n'+''.join('    {'+', '.join(f'{v:.12e}f' for v in self.result.inverse[i:i+3])+'},\n' for i in range(0, 9, 3))+'};\n'
            self.win.clipboard_clear()
            self.win.clipboard_append(text)
            self.status.set('已复制鸟瞰→原图矩阵。')

    def _session(self, dest):
        config = self._configuration()
        folder = dest.parent/(dest.stem+'.frames')
        folder.mkdir(exist_ok=True)
        for i, (stored, sample) in enumerate(zip(config['samples'], self.samples)):
            stored['image'] = ''
            if sample['rgb']:
                path = folder/f'sample-{i+1}.png'
                write_png(path, *SIZE, sample['rgb'])
                stored['image'] = path.relative_to(dest.parent).as_posix()
        config['result'] = dict(inverse=self.result.inverse, rms_px=self.result.rms, length_mm=self.result.length_mm)
        dest.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

    def save(self):
        if not self._ready():
            return
        filename = filedialog.asksaveasfilename(parent=self.win, title='保存标定与原始样本', initialdir=self.out_dir,
                                               initialfile='ground-calibration.local.json', defaultextension='.json')
        if filename:
            try:
                self._session(Path(filename))
                self.status.set('已保存JSON及同名.frames原图文件夹，请一起保留。')
            except (OSError, ValueError) as exc:
                messagebox.showerror('保存失败', str(exc), parent=self.win)

    def load(self):
        filename = filedialog.askopenfilename(parent=self.win, title='载入灵犀 / IPM工具标定', filetypes=[('标定JSON', '*.json')])
        if not filename:
            return
        try:
            path = Path(filename)
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            if config.get('schema') != 'lingxi.ipm/1':
                if config.get('version') != 1 or config.get('mode') not in ('matrix', 'points') or len(config.get('samples', [])) != 3:
                    raise ValueError('这是不同格式的标定。旧四点标定请用“四点逆透视”载入。')
                # Explicit adapter for the supplied offline IPM tool; no baked-in presets.
                config = dict(schema='lingxi.ipm/1', source_size=list(SIZE), mode='matrix' if config['mode'] == 'matrix' else 'samples',
                              width_mm=config['white_width_mm'], length_mm=config.get('known_length_mm') or '',
                              estimate_length=config.get('known_length_mm') is None,
                              target_px=config['target_width_px'], bottom_mm=config['bottom_distance_mm'], center_px=config.get('center_x_px', 94),
                              inverse_text='\n'.join(' '.join(str(v) for v in row) for row in config['matrix']),
                              samples=[dict(sample, enabled=config.get('enabled', [True]*3)[i], image=config.get('image_paths', ['']*3)[i])
                                       for i, sample in enumerate(config['samples'])])
            if config.get('source_size') != list(SIZE) or len(config['samples']) != 3:
                raise ValueError('需要188×120、三组样本槽位的标定文件')
            if config['mode'] not in ('samples', 'matrix'):
                raise ValueError('不支持的标定模式')
            samples = copy.deepcopy(config['samples'])
            for sample in samples:
                points = sample['points']
                if len(points) not in (0, 4) or any(len(point) != 2 for point in points):
                    raise ValueError('每组角点需要为空或四对坐标')
                sample['enabled'] = bool(sample['enabled'])
                sample['rgb'] = None
                if sample.get('image'):
                    image_path = Path(sample['image'])
                    if not image_path.is_absolute():
                        image_path = path.parent/image_path
                    if image_path.exists():
                        with Image.open(image_path) as image:
                            if image.size != SIZE:
                                raise ValueError('标定样本尺寸不是188×120，禁止自动缩放')
                            sample['rgb'] = image.convert('RGB').tobytes()
            self._loading = True
            self.samples = samples
            for key, control in (('width_mm', self.width), ('length_mm', self.length), ('target_px', self.target),
                                 ('bottom_mm', self.bottom), ('center_px', self.center)):
                control.set(config[key])
            self.estimate.set(config['estimate_length'])
            self.mode.set(config['mode'])
            self.matrix_box.delete('1.0', 'end')
            self.matrix_box.insert('1.0', config['inverse_text'])
            self.matrix_box.edit_modified(False)
            self._active = 0
            self.sample_choice.current(0)
            self._show_sample()
            self._estimate_changed()
            self.status.set('已载入。缺少图片的样本需要重新导入；请确认摄像头安装与标定时一致。')
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            self._loading = False
            messagebox.showerror('无法载入', str(exc), parent=self.win)

    def export(self):
        if not self._ready():
            return
        folder = filedialog.askdirectory(parent=self.win, title='选择结果导出目录', initialdir=self.out_dir)
        if not folder:
            return
        try:
            dest = Path(folder)/f'lingxi-ipm-{time.time_ns()}'
            dest.mkdir()
            self._session(dest/'calibration.json')
            (dest/'ipm_lut.h').write_text(self.result.header(), encoding='ascii')
            mask_rgb = bytes(v for value in self.result.mask for v in (value, value, value))
            write_png(dest/'valid-mask.png', *SIZE, mask_rgb)
            for i, sample in enumerate(self.samples):
                if sample['rgb']:
                    write_png(dest/f'bird-sample-{i+1}.png', *SIZE, self.result.warp(sample['rgb']))
            self.status.set(f'已导出：{dest}；掩码白=有效，黑=无效。未修改车辆工程。')
        except (OSError, ValueError) as exc:
            messagebox.showerror('导出失败', str(exc), parent=self.win)

    def _destroyed(self, event):
        if event.widget is self.win:
            self._shutdown()

    def apply_palette(self, palette):
        old = getattr(self, 'palette', None)
        self.palette = palette
        self.win.palette = palette
        self.win.configure(bg=palette['bg'])
        theme.restyle_tree(self.win, old, palette)
        for scroller in self.scrollers:
            scroller.configure(bg=palette['surface'])
        for canvas in (self.raw, self.bird):
            canvas.configure(bg=palette['canvas'])
        self.matrix_box.configure(bg=palette['surface_alt'], fg=palette['text'],
                                  insertbackground=palette['text'])
        self.draw()

    def _shutdown(self):
        if not self._closed:
            self._closed = True
            for job in (self._debounce, getattr(self, '_poll_job', None)):
                if job:
                    try:
                        self.win.after_cancel(job)
                    except tk.TclError:
                        pass
            self.executor.shutdown(wait=False, cancel_futures=True)

    def close(self):
        self._shutdown()
        self.win.destroy()

