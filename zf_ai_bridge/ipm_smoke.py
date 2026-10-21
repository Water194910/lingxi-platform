"""Real Tk event-loop regression checks; also runs inside the packaged EXE."""
from pathlib import Path
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from .extras import load_png_rgb
from .ipm_view import AdvancedPerspectiveWindow


def exercise(root, ui, folder):
    frame = ui.frame_sink.last_frame
    frozen = frame[3]
    ui._open_advanced_perspective()
    window = ui._ipm_window
    window.win.geometry('1000x680')
    root.update()
    assert window.raw.winfo_width() > 200
    assert window.bird.winfo_width() > 200
    assert window.width.text.get() == '' and window.length.text.get() == ''
    assert window.samples[0]['rgb'] == frozen

    def settle(predicate, timeout=4):
        deadline = time.monotonic()+timeout
        while not predicate() and time.monotonic() < deadline:
            root.update()
            time.sleep(.015)
        assert predicate(), window.metrics.get()

    window._loading = True
    window.width.set(448)
    window.length.set(500)
    window.distance.set(300)
    # Real camera geometry, keeping the horizon outside the output viewport.
    points = ((34.2666667, 100), (153.7333333, 100), (61.4181818, 54.5454545), (126.5818182, 54.5454545))
    for pair, values in zip(window.coords, points):
        for var, value in zip(pair, values):
            var.set(str(value))
    window._loading = False
    window.changed()
    settle(lambda: window.result is not None)
    assert window.result.rms < .001
    assert window.samples[0]['rgb'] == frozen
    # Bidirectional coupling; invalid typing must disable stale exports without clamping.
    window.target.slider.set(100.5)
    window.target._slide(None)
    assert window.target.text.get() == '100.5'
    window.target.text.set('90.5')
    assert window.target.slider.get() == 90.5
    window.target.text.set('-')
    assert window.result is None
    settle(lambda: '矩形显示宽度' in window.metrics.get())
    window.target.text.set('120')
    settle(lambda: window.result is not None)
    scale, ox, oy = window._raw_geometry
    first = window.samples[0]['points'][0]
    window.click(SimpleNamespace(x=ox+(float(first[0])+.5)*scale, y=oy+(float(first[1])+.5)*scale))
    window.drag(SimpleNamespace(x=ox+(float(first[0])+.7)*scale, y=oy+(float(first[1])+.5)*scale))
    settle(lambda: window.result is not None)
    # Quick changes cannot publish a previous worker's stale matrix.
    for value in (100, 120, 90, 110):
        window.target.text.set(str(value))
        window._queue()
    settle(lambda: window.result is not None and window.result.geometry['target_px'] == 110)
    assert window.result.geometry['target_px'] == 110
    window._session(Path(folder)/'advanced-session.json')
    assert load_png_rgb(Path(folder)/'advanced-session.frames/sample-1.png')[0] == frozen
    from tkinter import filedialog
    with patch.object(filedialog, 'askdirectory', return_value=str(folder)):
        window.export()
    exported = next(Path(folder).glob('lingxi-ipm-*'))
    assert (exported/'ipm_lut.h').is_file()
    assert load_png_rgb(exported/'valid-mask.png')[1:] == (188, 120)
    assert load_png_rgb(exported/'calibration.frames/sample-1.png')[0] == frozen
    with patch.object(filedialog, 'askopenfilename', return_value=str(Path(folder)/'advanced-session.json')):
        window.load()
    settle(lambda: window.result is not None)
    assert window.samples[0]['rgb'] == frozen
    # Interoperate with the offline tool's session schema without copying presets.
    foreign = dict(version=1, mode='matrix', white_width_mm=448, known_length_mm=500,
                   target_width_px=120, bottom_distance_mm=400, center_x_px=94,
                   matrix=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                   samples=[dict(distance_mm=300+i*150, points=[list(p) for p in points]) for i in range(3)],
                   enabled=[True, False, False],
                   image_paths=['advanced-session.frames/sample-1.png', '', ''])
    foreign_path = Path(folder)/'offline-tool.json'
    foreign_path.write_text(json.dumps(foreign), encoding='utf-8')
    with patch.object(filedialog, 'askopenfilename', return_value=str(foreign_path)):
        window.load()
    settle(lambda: window.result is not None)
    assert window.result.coverage == 1 and window.result.geometry is None
    assert window.samples[0]['rgb'] == frozen
    window.live.set(True)
    window._live_changed()
    ui.frame_sink.on_camera(1, 188, 120, bytes([255])*(188*120//8))
    window._live_key = None
    window._refresh_bird()
    assert set(window._bird_rgb) == {255}
    assert window.samples[0]['rgb'] == frozen
    for theme in ('dark', 'light'):
        if ui.theme != theme:
            ui.toggle_theme()
        root.update()
        assert window.raw.cget('background') == window.palette['canvas']
    # Starting a worker then closing must not dispatch Tk calls from that worker.
    window.target.text.set('130')
    window._queue()
    window.close()
    root.update()
    assert window._closed
    return 'advanced-ipm-fit-sliders-drag-stale-worker-session-export-live-themes-close'
