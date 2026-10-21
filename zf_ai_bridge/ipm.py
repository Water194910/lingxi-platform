"""Ground-plane fitting and reusable nearest-neighbour maps, using the standard library.

This is a LingXi implementation of planar calibration, not a vehicle calibration preset.
Samples use near-left, near-right, far-left, far-right; distances are millimetres.
"""
from __future__ import annotations

from array import array
import math
import re

from .perspective import homography, transform

SIZE = (188, 120)
INVALID = 65535
ORDER = ("近左", "近右", "远左", "远右")


def number(value, label, positive=False, nonnegative=False):
    try:
        value = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"请填写{label}") from exc
    if not math.isfinite(value) or positive and value <= 0 or nonnegative and value < 0:
        raise ValueError(f"{label}须为{'正数' if positive else '非负有限数' if nonnegative else '有限数'}")
    return value


def matrix(value):
    if isinstance(value, str):
        # Accept a C initializer, excluding dimensions in 'float H_inv[3][3]'.
        value = value[value.index('{'):] if '{' in value else value
        value = re.findall(r'[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?', value)
    elif len(value) == 3 and all(isinstance(row, (tuple, list)) for row in value):
        value = [v for row in value for v in row]
    if len(value) != 9:
        raise ValueError("矩阵需要9个数值，可粘贴完整的C矩阵")
    values = tuple(number(v, "矩阵元素") for v in value)
    scale = max(abs(v) for v in values)
    if scale == 0:
        raise ValueError("矩阵不可逆")
    values = tuple(v / scale for v in values)
    divisor = values[8] if abs(values[8]) > 1e-12 else max(values, key=abs)
    return tuple(v / divisor for v in values)


def inverse(m):
    a, b, c, d, e, f, g, h, i = m
    cofactors = (e*i-f*h, c*h-b*i, b*f-c*e, f*g-d*i, a*i-c*g, c*d-a*f,
                 d*h-e*g, b*g-a*h, a*e-b*d)
    determinant = a*cofactors[0] + b*cofactors[3] + c*cofactors[6]
    if abs(determinant) < 1e-12 or not math.isfinite(determinant):
        raise ValueError("矩阵不可逆或过于退化")
    return matrix(tuple(v/determinant for v in cofactors))


def multiply(a, b):
    return tuple(sum(a[r*3+k]*b[k*3+c] for k in range(3)) for r in range(3) for c in range(3))


def least_squares(rows, rhs):
    """Reorthogonalized QR; reject underdetermined or degenerate observations."""
    count = len(rows[0])
    if len(rows) < count:
        raise ValueError("标定约束不足，请增加不同距离的样本")
    columns = [list(col) for col in zip(*rows)]
    upper = [[0.] * count for _ in range(count)]
    basis = []
    for j, column in enumerate(columns):
        original = math.sqrt(sum(v*v for v in column))
        for _ in range(2):
            for k, unit in enumerate(basis):
                dot = sum(a*b for a, b in zip(unit, column))
                upper[k][j] += dot
                column = [a-dot*b for a, b in zip(column, unit)]
        norm = math.sqrt(sum(v*v for v in column))
        if norm < max(1., original)*1e-10:
            raise ValueError("角点或距离退化，请选分布更开的真实样本")
        upper[j][j] = norm
        basis.append([v/norm for v in column])
    result = [0.] * count
    projected = [sum(a*b for a, b in zip(unit, rhs)) for unit in basis]
    for j in range(count-1, -1, -1):
        result[j] = (projected[j]-sum(upper[j][k]*result[k] for k in range(j+1, count)))/upper[j][j]
    return result


def _fit_matrix(src, dst):
    rows, values = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows += [[x, y, 1., 0., 0., 0., -u*x, -u*y],
                 [0., 0., 0., x, y, 1., -v*x, -v*y]]
        values += [u, v]
    return tuple(least_squares(rows, values))+(1.,)


class GroundMap:
    def __init__(self, inverse_matrix, *, rms=None, errors=(), length_mm=None, geometry=None):
        self.inverse = matrix(inverse_matrix)
        self.forward = inverse(self.inverse)
        w, h = SIZE
        denominators = [self.inverse[6]*x+self.inverse[7]*y+self.inverse[8]
                        for x, y in ((0, 0), (w-1, 0), (w-1, h-1), (0, h-1))]
        if min(denominators)*max(denominators) <= 0 or min(map(abs, denominators)) < 1e-9:
            raise ValueError("输出区域跨越地平线，请缩小范围或调整底行距离")
        self.image_lut, self.point_lut = array('H'), array('H')
        self.mask = bytearray()
        for y in range(h):
            for x in range(w):
                sx, sy = transform(self.inverse, x, y)
                valid = math.isfinite(sx) and math.isfinite(sy) and 0 <= sx <= w-1 and 0 <= sy <= h-1
                self.image_lut.append(int(sy+.5)*w+int(sx+.5) if valid else INVALID)
                self.mask.append(255 if valid else 0)
                try:
                    u, v = transform(self.forward, x, y)
                    valid = math.isfinite(u) and math.isfinite(v) and 0 <= u <= w-1 and 0 <= v <= h-1
                except ValueError:
                    valid = False
                self.point_lut.append((int(v+.5) << 8) | int(u+.5) if valid else INVALID)
        self.rms, self.errors, self.length_mm = rms, list(errors), length_mm
        self.geometry = geometry
        self.coverage = sum(v != INVALID for v in self.image_lut)/len(self.image_lut)

    def warp(self, rgb, *, fill=255):
        if len(rgb) != SIZE[0]*SIZE[1]*3:
            raise ValueError("高级标定需要188×120原图，不会自动缩放")
        fill = int(fill)
        if not 0 <= fill <= 255:
            raise ValueError("填充值须为0～255")
        output = bytearray(bytes([fill])*len(rgb))
        for i, source in enumerate(self.image_lut):
            if source != INVALID:
                output[i*3:i*3+3] = rgb[source*3:source*3+3]
        return bytes(output)

    def header(self):
        lines = ['#ifndef LINGXI_IPM_LUT_H', '#define LINGXI_IPM_LUT_H', '#include <stdint.h>',
                 '/* LingXi 188x120 nearest-neighbour maps. UINT16_MAX = invalid.',
                 ' * Check validity before indexing; white fill is NOT measured road.',
                 ' * Point table: source (y*188+x) -> (bird_y<<8)|bird_x.',
                 ' * Two uint16 tables: 90240 bytes; keep const in read-only storage. */']
        if self.geometry:
            lines += [f'/* Geometry (mm): width={self.geometry["width_mm"]:.9g};',
                      f' * width pixels={self.geometry["target_px"]:.9g}; bottom={self.geometry["bottom_mm"]:.9g}. */']
        for name, table in (("ipm_lut", self.image_lut), ("ipm_point_lut", self.point_lut)):
            lines.append(f'static const uint16_t {name}[188 * 120] = {{')
            lines += ['    '+', '.join(str(v) for v in table[i:i+16])+',' for i in range(0, len(table), 16)]
            lines += ['};']
        return '\n'.join(lines+['#endif', ''])


def fit(samples, width_mm, length_mm, target_px=120, bottom_mm=400, center_px=94):
    """Fit a fixed camera and the same ground rectangle at known longitudinal offsets.

    Unknown length needs at least two different distances. Samples must share a
    centerline, width and length; camera and reference origin stay fixed.
    """
    width = number(width_mm, "矩形实测宽度（mm）", positive=True)
    target = number(target_px, "矩形显示宽度（px）", positive=True)
    bottom = number(bottom_mm, "底行距离（mm）", nonnegative=True)
    center = number(center_px, "横向中心（px）")
    known = length_mm not in (None, '')
    length = number(length_mm, "矩形实测长度（mm）", positive=True) if known else None
    if not samples or not known and len(samples) < 2:
        raise ValueError("请提供实测长宽；估算长度时至少需要两组不同距离的样本")
    observations, distances = [], []
    for sample in samples:
        distance = number(sample['distance_mm'], "近边距离（mm）", nonnegative=True)
        points = tuple(tuple(number(v, "角点坐标") for v in p) for p in sample['points'])
        if len(points) != 4 or any(len(p) != 2 or not 0 <= p[0] <= 187 or not 0 <= p[1] <= 119 for p in points):
            raise ValueError("每组需要四个188×120图像内的角点")
        polygon = [points[i] for i in (2, 3, 1, 0)]
        for j in range(4):
            a, b, c = [polygon[(j+k) % 4] for k in range(3)]
            if (b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]) <= 1e-4:
                raise ValueError("角点顺序或形状错误：近左→近右→远左→远右；四边形须凸且不交叉")
        distances.append(distance)
        observations += list(points)
    if not known and max(distances)-min(distances) < 1e-3:
        raise ValueError("估算长度需要不同的实测距离")
    reference = sum(distances)/len(distances)
    ground = [(x, (distance-reference)/width) for distance in distances for x in (-.5, .5, -.5, .5)]
    # Source normalization makes residuals dimensionless and avoids raw-pixel conditioning.
    observed = [((x-94)/188, (y-60)/120) for x, y in observations]
    if known:
        full = [(x, y+(length/width if i % 4 >= 2 else 0)) for i, (x, y) in enumerate(ground)]
        initial = _fit_matrix(full, observed)
    else:
        near_src = [p for i, p in enumerate(ground) if i % 4 < 2]
        near_dst = [p for i, p in enumerate(observed) if i % 4 < 2]
        initial = _fit_matrix(near_src, near_dst)
        inv = inverse(initial)
        estimates = [transform(inv, *p)[1]-ground[i][1] for i, p in enumerate(observed) if i % 4 >= 2]
        positive = sorted(v for v in estimates if math.isfinite(v) and v > 1e-5)
        if not positive:
            raise ValueError("无法估算矩形长度，请提供实测长度或检查距离和角点")
        length = positive[len(positive)//2]*width
    params = list(initial[:8])+([] if known else [math.log(length/width)])

    def residual(parameters):
        ratio = length/width if known else math.exp(max(-20, min(20, parameters[8])))
        m = tuple(parameters[:8])+(1.,)
        errors = []
        for i, ((x, y), (u, v)) in enumerate(zip(ground, observed)):
            pu, pv = transform(m, x, y+(ratio if i % 4 >= 2 else 0))
            errors += [(pu-u)*188, (pv-v)*120]
        return errors

    damping, converged = 1e-4, False
    errors = residual(params)
    cost = sum(e*e for e in errors)
    for _ in range(100):
        if cost < 1e-20:
            converged = True
            break
        columns = []
        for j in range(len(params)):
            step = 1e-6*max(1., abs(params[j]))
            changed = params.copy()
            changed[j] += step
            columns.append([(a-b)/step for a, b in zip(residual(changed), errors)])
        jacobian = list(map(list, zip(*columns)))
        gradient = [sum(a*b for a, b in zip(col, errors)) for col in columns]
        if max(map(abs, gradient)) < 1e-6:
            converged = True
            break
        rows = jacobian+[[math.sqrt(damping) if i == j else 0. for j in range(len(params))]
                         for i in range(len(params))]
        delta = least_squares(rows, [-v for v in errors]+[0.]*len(params))
        candidate = [a+b for a, b in zip(params, delta)]
        try:
            updated = residual(candidate)
            updated_cost = sum(v*v for v in updated)
        except (ValueError, OverflowError):
            updated_cost = math.inf
        if updated_cost < cost:
            improvement = cost-updated_cost
            params, errors, cost = candidate, updated, updated_cost
            damping = max(1e-12, damping/3)
            if improvement < 1e-10*(1+cost) or max(map(abs, delta)) < 1e-8:
                converged = True
                break
        else:
            damping *= 10
            if damping > 1e12:
                break
    if not converged:
        raise ValueError("标定未收敛，请检查角点、距离和相机是否固定")
    length = length if known else math.exp(params[8])*width
    if not math.isfinite(length) or length < .001:
        raise ValueError("拟合长度无效，请检查样本")
    ground_to_source = multiply((188., 0., 94., 0., 120., 60., 0., 0., 1.), tuple(params[:8])+(1.,))
    bird_to_ground = (1/target, 0., -center/target, 0., -1/target,
                      (bottom-reference)/width+119/target, 0., 0., 1.)
    pixel_errors = [math.hypot(errors[i], errors[i+1]) for i in range(0, len(errors), 2)]
    sample_errors = [math.sqrt(sum(v*v for v in pixel_errors[i:i+4])/4) for i in range(0, len(pixel_errors), 4)]
    return GroundMap(multiply(ground_to_source, bird_to_ground),
                     rms=math.sqrt(sum(v*v for v in pixel_errors)/len(pixel_errors)), errors=sample_errors,
                     length_mm=length, geometry=dict(width_mm=width, target_px=target, bottom_mm=bottom, center_px=center))
