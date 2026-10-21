"""原始像素隔离及四点几何的回归验证，不依赖摄像头。"""
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zf_ai_bridge.extras import load_png_rgb
from zf_ai_bridge.image import FrameSink, decode_camera_payload
from zf_ai_bridge.perspective import Calibration, transform


class RawFrameTests(unittest.TestCase):
    def test_gray_binary_stay_exact_with_overlays(self):
        # 188列跨字节连续打包；边线/矩形不得污染latest、历史或冻结快照。
        for kind in (1, 2):
            w, h = 188, 120
            payload = (bytes((i*37) % 256 for i in range(w*h//8)) if kind == 1
                       else bytes(i % 256 for i in range(w*h)))
            expected = decode_camera_payload(kind, w, h, payload)
            with tempfile.TemporaryDirectory() as folder:
                sink = FrameSink(folder)
                sink.save_annotated = True
                sink.show_edges = True
                sink.show_center = True
                sink.add_boundary([(4, 4), (170, 100)], (255, 0, 0))
                sink.add_rect(20, 30, 40, 50, (0, 255, 0))
                meta = sink.on_camera(kind, w, h, payload)
                frozen = sink.last_frame
                self.assertEqual(sink.last_rgb, expected)
                self.assertNotEqual(sink.last_annotated_rgb, expected)
                for key in ("latestPath", "path"):
                    self.assertEqual(load_png_rgb(meta[key]), (expected, w, h))
                self.assertEqual(load_png_rgb(meta["annotatedPath"])[0], sink.last_annotated_rgb)
                self.assertTrue(meta["rawSaved"])
                self.assertFalse(meta["overlaysAppliedToRaw"])
                self.assertFalse(list(Path(folder).glob("*.tmp")))
                sink.on_camera(kind, w, h, bytes(len(payload)))
                self.assertEqual(frozen[3], expected)
                self.assertEqual(sink.last_rgb, sink.last_annotated_rgb)

    def test_annotation_disk_output_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            sink = FrameSink(folder)
            sink.add_boundary([(1, 1), (4, 4)])
            meta = sink.on_camera(2, 8, 8, bytes(64))
            self.assertIsNone(meta["annotatedPath"])
            self.assertFalse((Path(folder)/"latest_annotated.png").exists())
            self.assertEqual(sink.last_rgb, bytes(192))


class PerspectiveTests(unittest.TestCase):
    def test_identity_preserves_every_gray_pixel(self):
        cal = Calibration((9, 7), ((0, 0), (8, 0), (8, 6), (0, 6)), 8, 6, 1)
        rgb = bytes(v for i in range(63) for v in (i*4,)*3)
        self.assertEqual(cal.output_size, (9, 7))
        self.assertEqual(cal.warp(rgb, (9, 7)), rgb)

    def test_trapezoid_known_mapping_and_roundtrip(self):
        corners = ((2, 0), (6, 0), (8, 6), (0, 6))
        cal = Calibration((9, 7), corners, 4, 6, 1)
        for source, target in zip(corners, ((0, 0), (4, 0), (4, 6), (0, 6))):
            for actual, expected in zip(transform(cal.forward, *source), target):
                self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(transform(cal.forward, 4, 3), (2, 4)):
            self.assertAlmostEqual(actual, expected)
        for point in ((3, 1), (4, 3), (5, 4)):
            restored = transform(cal.inverse, *transform(cal.forward, *point))
            for a, b in zip(restored, point):
                self.assertAlmostEqual(a, b)

    def test_binary_nearest_keeps_binary_and_gray_interpolates(self):
        cal = Calibration((3, 3), ((0, 0), (2, 0), (2, 2), (0, 2)), 2, 2, 2)
        rgb = bytes(v for y in range(3) for x in range(3) for v in ((255 if x % 2 else 0),)*3)
        self.assertEqual(set(cal.warp(rgb, (3, 3), binary=True)), {0, 255})
        gray = cal.warp(rgb, (3, 3))
        self.assertEqual(gray[3:6], bytes((128, 128, 128)))

    def test_reject_bad_geometry_and_dimensions(self):
        valid = ((0, 0), (8, 0), (8, 6), (0, 6))
        cases = (valid[::-1], ((0, 0), (8, 6), (8, 0), (0, 6)),
                 ((0, 0), (4, 0), (8, 0), (0, 6)),
                 ((-1, 0), (8, 0), (8, 6), (0, 6)),
                 ((math.nan, 0), (8, 0), (8, 6), (0, 6)))
        for points in cases:
            with self.assertRaises(ValueError):
                Calibration((9, 7), points, 8, 6, 1)
        for dims in ((0, 6, 1), (8, math.inf, 1), (8, 6, 1000), (8, 6, math.nan)):
            with self.assertRaises(ValueError):
                Calibration((9, 7), valid, *dims)
        cal = Calibration((9, 7), valid, 8, 6, 1)
        with self.assertRaises(ValueError):
            cal.warp(bytes(189), (7, 9))

    def test_save_load_recomputes_geometry(self):
        cal = Calibration((9, 7), ((2, 0), (6, 0), (8, 6), (0, 6)), 4, 6, 1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"calibration.json"
            cal.save(path)
            data = json.loads(path.read_text())
            data["source_to_bird"] = [0]*9
            path.write_text(json.dumps(data))
            loaded = Calibration.load(path)
            self.assertEqual(loaded.forward, cal.forward)


if __name__ == "__main__":
    unittest.main()
