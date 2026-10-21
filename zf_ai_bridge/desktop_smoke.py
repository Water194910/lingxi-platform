"""源代码和冻结EXE共用的离线自检，不连接车、不占用生产端口。"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import traceback


def run(report_path: Path) -> int:
    report = {"ok": False, "frozen": bool(getattr(sys, "frozen", False)), "checks": []}
    if report["frozen"]:
        report["executableSHA256"] = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    previous_data_dir = os.environ.get("LINGXI_DATA_DIR")
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog
        from types import SimpleNamespace
        from unittest.mock import patch
        import socket
        from PIL import Image
        import serial
        from .extras import load_png_rgb
        from .perspective_view import PerspectiveWindow
        from .desktop_runtime import VERSION
        from .desktop_runtime import choose_writable_directory
        # frozen入口模块以__main__加载；直接使用已加载定义，避免重复加载入口。
        main_module = sys.modules["__main__"]
        if hasattr(main_module, "LingXiApp"):
            LingXiApp = main_module.LingXiApp
        else:
            from desktop import LingXiApp
        with tempfile.TemporaryDirectory(prefix="lingxi-smoke-") as folder:
            os.environ["LINGXI_DATA_DIR"] = folder
            blocked = Path(folder)/"not-a-directory"
            blocked.write_text("file", encoding="utf-8")
            fallback = Path(folder)/"fallback"
            assert choose_writable_directory([blocked, fallback]) == fallback
            assert not list(fallback.glob(".lingxi-write-*"))
            with patch.object(Path, "mkdir", side_effect=PermissionError("simulated denied")):
                try:
                    choose_writable_directory([fallback])
                except OSError as exc:
                    assert "找不到可写" in str(exc)
                else:
                    raise AssertionError("expected error when no directory writable")
            report["checks"].append("unwritable-directory-fallback")
            root = tk.Tk()
            callback_errors = []
            root.report_callback_exception = lambda *args: callback_errors.append(str(args))
            ui = LingXiApp(root)
            root.update()
            root.withdraw()
            report["version"] = VERSION
            report["checks"].append("window-created")
            assert not ui.write_var.get() and not ui.edge_var.get() and not ui.center_var.get()
            for theme in ("dark", "light"):
                if ui.theme != theme:
                    ui.toggle_theme()
                assert ui.theme == theme
                assert json.loads(ui.pref_path.read_text(encoding="utf-8"))["theme"] == theme
            report["checks"].append("light-dark-persistence")
            for page in ("analysis", "events", "vision"):
                ui.show_page(page)
                assert ui._page == page
            report["checks"].append("navigation")
            # 真实事件循环验证：快速导航/主题切换只保留最后目标，按钮执行不等动画。
            def settle_motion():
                deadline = time.monotonic()+.3
                while time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)

            calls = []
            animated = ui._button(ui.sidebar, "动效测试", lambda: calls.append("click"))
            animated.event_generate("<Enter>")
            animated.event_generate("<ButtonPress-1>", x=8, y=8)
            animated.invoke()
            assert calls == ["click"]
            animated.state(["disabled"])
            animated.invoke()
            assert calls == ["click"]
            animated.state(["!disabled"])
            for page in ("analysis", "events", "vision", "events"):
                ui.nav_buttons[page].invoke()
            assert ui._page == "events" and ui._page_job is not None
            ui.toggle_theme()
            settle_motion()
            assert ui._page_job is None
            assert ui.pages["events"].winfo_x() == 0
            assert all(not page.winfo_ismapped() for key, page in ui.pages.items() if key != "events")
            animated.event_generate("<Leave>")
            ui.motion_var.set(False)
            ui._toggle_motion()
            ui.show_page("analysis")
            assert ui._page_job is None and animated._job is None
            assert json.loads(ui.pref_path.read_text(encoding="utf-8"))["motion"] is False
            ui.motion_var.set(True)
            ui._toggle_motion()
            animated.event_generate("<Enter>")
            animated.destroy()
            ui.show_page("vision")
            settle_motion()
            assert not callback_errors, callback_errors
            report["checks"].append("motion-rapid-navigation-immediate-command-disabled-theme-cancel")
            # 188列连续位流，与车端两种模式一致；经协议解析入口送入，而非直接改预览。
            for kind in (2, 1):
                w, h = 188, 120
                payload = bytes(i % 256 for i in range(w*h if kind == 2 else w*h//8))
                header = bytearray(12)
                header[0], header[2], header[3] = 0xAA, 2, kind
                struct.pack_into("<HH", header, 4, w, h)
                header[1] = (header[0]+sum(header[2:])) & 255
                packet = bytes(header)+payload
                for start in range(0, len(packet), 197):
                    ui._on_bytes(packet[start:start+197])
                ui._refresh_preview()
                assert ui.frame_sink.last_frame[0] == kind
                assert load_png_rgb(Path(folder)/"frames"/"latest.png")[0] == ui.frame_sink.last_frame[3]
                assert all(ui.frame_sink.last_rgb[i] == ui.frame_sink.last_rgb[i+1] == ui.frame_sink.last_rgb[i+2]
                           for i in range(0, len(ui.frame_sink.last_rgb), 3))
            report["checks"].append("fragmented-gray-binary-clean-png")
            ui.frame_sink.add_boundary([(10, 10), (100, 80)], (248, 0, 248))
            ui.edge_var.set(True)
            ui._on_overlay_toggle()
            ui.frame_sink.on_camera(2, 188, 120, bytes(188*120))
            ui._refresh_preview()
            assert ui.frame_sink.last_rgb == bytes(188*120*3)
            assert ui.frame_sink.last_annotated_rgb != ui.frame_sink.last_rgb
            assert ui._display_raw[2] == ui.frame_sink.last_rgb
            ui._on_preview_click(SimpleNamespace(x=10000, y=10000))
            assert "图外" in ui.probe_var.get()
            ui._fit_view()
            report["checks"].append("optional-edge-overlay-probe-zoom")
            # 真实历史窗口/按钮流程：原文件被后台清理后仍可保存和锁定。
            def button(window, title):
                pending = list(window.winfo_children())
                while pending:
                    widget = pending.pop()
                    if widget.winfo_class() == "TButton" and widget.cget("text") == title:
                        return widget
                    pending.extend(widget.winfo_children())
                raise AssertionError(f"button missing: {title}")

            for level in range(10):
                ui.frame_sink.on_camera(2, 188, 120, bytes([level])*(188*120))
            snapshots = {p.name: p.read_bytes() for p in (Path(folder)/"frames").glob("frame_*.png")}
            assert len(snapshots) >= 2
            ui._pick_frame()
            history = ui._history_window
            root.update()
            tree = history.frame_list
            first_name = tree.item("0", "text")
            for path in (Path(folder)/"frames").glob("frame_*.png"):
                path.unlink()
            saved = Path(folder)/"saved-history.png"
            with patch.object(filedialog, "asksaveasfilename", return_value=str(saved)):
                button(history, "保存选中…").invoke()
            assert saved.read_bytes() == snapshots[first_name]
            export_dir = Path(folder)/"history-export"
            export_dir.mkdir()
            (export_dir/first_name).write_bytes(b"keep existing file")
            tree.selection_set(tree.get_children())
            with patch.object(filedialog, "askdirectory", return_value=str(export_dir)):
                button(history, "保存选中…").invoke()
            assert (export_dir/first_name).read_bytes() == b"keep existing file"
            assert (export_dir/(Path(first_name).stem+"_1.png")).read_bytes() == snapshots[first_name]
            assert len(list(export_dir.glob("*.png"))) == len(snapshots)+1
            with patch.object(filedialog, "askdirectory", return_value=""):
                button(history, "保存全部…").invoke()
            assert len(list(export_dir.glob("*.png"))) == len(snapshots)+1
            tree.selection_set("0")
            tree.focus("0")
            button(history, "锁定查看").invoke()
            assert ui.mode == "history" and not history.winfo_exists()
            locked = ui._display_raw
            ui._on_bytes(packet)
            ui._poll_ui()
            assert ui.mode == "history" and ui._display_raw == locked
            assert not ui.live_btn.instate(["disabled"])
            saved_locked = Path(folder)/"locked.png"
            with patch.object(filedialog, "asksaveasfilename", return_value=str(saved_locked)):
                ui.save_image_btn.invoke()
            assert saved_locked.read_bytes() == snapshots[first_name]
            ui.live_btn.invoke()
            assert ui.mode == "live" and ui._display_raw[2] == ui.frame_sink.last_frame[3]
            ui.frame_sink.on_camera(2, 188, 120, bytes([70])*(188*120))
            ui.ui_queue.put(("frame", {}))
            ui._poll_ui()
            assert ui._display_raw[2] == bytes([70])*(188*120*3)
            report["checks"].append("history-eviction-single-batch-export-return-live")
            saved_live = Path(folder)/"live.png"
            raw_at_click = ui.frame_sink.last_frame[3]
            def save_while_receiving(**kwargs):
                ui.frame_sink.on_camera(2, 188, 120, bytes([160])*(188*120))
                return str(saved_live)
            with patch.object(filedialog, "asksaveasfilename", side_effect=save_while_receiving):
                ui.save_image_btn.invoke()
            assert load_png_rgb(saved_live)[0] == raw_at_click
            assert ui.frame_sink.last_frame[3] != raw_at_click
            report["checks"].append("save-raw-snapshot-during-live-receive")
            ui.frame_sink.last_frame = None
            (Path(folder)/"frames"/"latest.png").unlink()
            ui._back_to_live()
            assert ui.mode == "live" and ui._photo is None and ui._display_raw is None
            ui.frame_sink.on_camera(2, 188, 120, bytes(188*120))
            ui._refresh_preview()
            assert ui._photo is not None
            report["checks"].append("return-live-without-frame-clears-locked-preview")
            # UI几何检查：在最小支持尺寸内，主按钮与容器不越界。
            root.geometry("1080x720")
            root.deiconify()
            root.update()
            assert ui.preview.winfo_width() > 300
            assert ui.start_btn.winfo_rooty()+ui.start_btn.winfo_height() < root.winfo_rooty()+root.winfo_height()
            ui.control_tabs.select(2)
            root.update()
            assert ui.rec_btn.winfo_rooty()+ui.rec_btn.winfo_height() < root.winfo_rooty()+root.winfo_height()
            ui.control_tabs.select(0)
            root.withdraw()
            report["checks"].append("minimum-window-layout")
            frozen = ui.frame_sink.last_frame[3]
            win = PerspectiveWindow(root, frozen, 188, 120, 2, Path(folder)/"frames")
            win.win.withdraw()
            for x, y in ((40, 10), (145, 10), (180, 110), (5, 110)):
                win.click(SimpleNamespace(x=x*win.factor, y=y*win.factor))
            win.width.set("40")
            win.length.set("60")
            win.scale.set("1")
            assert win.preview() and win.calibration.output_size == (41, 61)
            with patch.object(filedialog, "askdirectory", return_value=folder):
                win.export()
            exported = next(Path(folder).glob("ipm_*"))
            assert load_png_rgb(exported/"raw.png")[0] == frozen
            assert (exported/"calibration.json").is_file()
            win.win.destroy()
            report["checks"].append("four-point-warp-export")
            photo = Path(folder)/"test.jpg"
            Image.new("RGB", (16, 12), (25, 25, 25)).save(photo)
            with patch.object(filedialog, "askopenfilename", return_value=str(photo)):
                ui._upload_image()
            assert ui.mode == "uploaded"
            assert load_png_rgb(ui.work_path)[1:] == (16, 12)
            ui._back_to_live()
            report["checks"].append("pillow-jpeg-import")
            ui._toggle_record()
            assert ui._rec is not None
            ui._rec_tick()
            ui._toggle_record()
            assert ui._rec_path.stat().st_size > 1024
            report["checks"].append("raw-avi-recording")
            assert serial.VERSION
            report["checks"].append("serial-dependency")
            # 真正启动桥和监听线程；系统分配端口，避免占用车端8081/AI8765。
            desktop_module = sys.modules[LingXiApp.__module__]
            real_config = desktop_module.BridgeConfig
            with patch.object(desktop_module, "BridgeConfig", lambda **kwargs: real_config(port=0, allow_write=False)):
                ui.input_var.set("tcp-listen://127.0.0.1:0")
                ui.tap_var.set(False)
                ui._start_bridge()
            assert ui.running and ui.bridge.config.allow_write is False
            deadline = time.monotonic()+3
            while ui.source._server is None and time.monotonic() < deadline:
                time.sleep(.02)
            port = ui.source._server.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
                count = ui.frame_sink.stats()["frames"]
                conn.sendall(packet)
                while ui.frame_sink.stats()["frames"] <= count and time.monotonic() < deadline:
                    time.sleep(.02)
                assert ui.frame_sink.stats()["frames"] == count+1
            ui._stop_bridge()
            assert not ui.running
            report["checks"].append("tcp-camera-start-receive-stop")
            ui._log("smoke check")
            ui._clear_log()
            assert not ui.data_text.get("1.0", "end").strip()
            report["ok"] = True
            assert not callback_errors, callback_errors
            report["checks"].append("readonly-events-clear")
            root.destroy()
            root = None
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if root:
            root.destroy()
        if previous_data_dir is None:
            os.environ.pop("LINGXI_DATA_DIR", None)
        else:
            os.environ["LINGXI_DATA_DIR"] = previous_data_dir
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
