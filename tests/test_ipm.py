"""Geometric ground truth, not fitted presets or implementation-shaped assertions."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zf_ai_bridge.ipm import GroundMap, INVALID, fit, transform


def samples(noise=0):
    camera = (160., 94., 28200., 0., 0., 60000., 0., 1., 300.)
    result = []
    for i, distance in enumerate((300., 450., 600.)):
        points = [transform(camera, x, y) for x, y in ((-224, distance), (224, distance),
                                                      (-224, distance+500), (224, distance+500))]
        points = [[u+noise*((i+j) % 3-1), v+noise*((i-j) % 3-1)] for j, (u, v) in enumerate(points)]
        result.append(dict(distance_mm=distance, points=points))
    return result


class CalibrationTests(unittest.TestCase):
    def test_measured_rectangle_multiple_distances(self):
        result = fit(samples(), 448, 500, 80, 250, 94)
        self.assertLess(result.rms, 1e-7)
        for bx, by in ((0, 0), (94, 50), (187, 119)):
            expected = transform((160., 94., 28200., 0., 0., 60000., 0., 1., 300.),
                                 (bx-94)*448/80, 250+(119-by)*448/80)
            actual = transform(result.inverse, bx, by)
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b, places=6)

    def test_unknown_length_and_noisy_measurements(self):
        inferred = fit(samples(), 448, None, 80, 250, 94)
        self.assertAlmostEqual(inferred.length_mm, 500, places=5)
        self.assertLess(inferred.rms, 1e-7)
        noisy = fit(samples(.3), 448, None, 80, 250, 94)
        self.assertLess(noisy.rms, .6)
        self.assertLess(abs(noisy.length_mm-500), 20)

    def test_one_sample_requires_length(self):
        self.assertLess(fit(samples()[:1], 448, 500).rms, 1e-7)
        with self.assertRaises(ValueError):
            fit(samples()[:1], 448, None)

    def test_invalid_geometry_rejected(self):
        for invalid in (0, -5, float('nan'), float('inf'), ''):
            with self.assertRaises(ValueError):
                fit(samples(), invalid, 500)
        repeated = samples()[:2]
        repeated[1]['distance_mm'] = repeated[0]['distance_mm']
        with self.assertRaises(ValueError):
            fit(repeated, 448, None)
        crossed = samples()
        crossed[0]['points'][0], crossed[0]['points'][1] = crossed[0]['points'][1], crossed[0]['points'][0]
        with self.assertRaises(ValueError):
            fit(crossed, 448, 500)

    def test_lookup_masks_raw_pixels_and_point_directions(self):
        identity = GroundMap((1, 0, 0, 0, 1, 0, 0, 0, 1))
        self.assertEqual(list(identity.image_lut), list(range(22560)))
        for x, y in ((0, 0), (94, 60), (187, 119)):
            self.assertEqual(identity.point_lut[y*188+x], (y << 8) | x)
        translated = GroundMap((1, 0, 10, 0, 1, 5, 0, 0, 1))
        rgb = bytes(v for y in range(120) for x in range(188) for v in ((255 if x % 2 else 0),)*3)
        frozen = bytes(rgb)
        bird = translated.warp(rgb)
        self.assertEqual(rgb, frozen)
        self.assertLessEqual(set(bird), {0, 255})
        self.assertEqual(bird[:3], rgb[(5*188+10)*3:(5*188+10)*3+3])
        self.assertEqual(translated.image_lut[-1], INVALID)
        self.assertEqual(translated.mask[-1], 0)
        self.assertEqual(translated.point_lut[5*188+10], 0)
        self.assertEqual(len(translated.mask), 22560)
        self.assertIn('UINT16_MAX', translated.header())
        self.assertEqual(len(translated.image_lut)*2+len(translated.point_lut)*2, 90240)
        with self.assertRaises(ValueError):
            translated.warp(bytes(100))

    def test_matrix_input_and_horizon(self):
        result = GroundMap('const float H_inv[3][3] = {{1.0f,0,0},{0,1e0f,0},{0,0,1}};')
        self.assertEqual(result.coverage, 1)
        for invalid in ((0,)*9, (1, 0, 0, 0, 1, 0, 0, -.02, 1)):
            with self.assertRaises(ValueError):
                GroundMap(invalid)


if __name__ == '__main__':
    unittest.main()
