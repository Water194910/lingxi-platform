"""平面四点逆透视；仅在上位机实验，不参与车端控制。只依赖标准库。"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def homography(src, dst) -> tuple[float, ...]:
    """求四对点的单应矩阵，最后一项固定为1。"""
    rows = []
    for (x, y), (u, v) in zip(src, dst):
        rows.extend(([x, y, 1., 0., 0., 0., -u*x, -u*y, u],
                     [0., 0., 0., x, y, 1., -v*x, -v*y, v]))
    if len(rows) != 8:
        raise ValueError("需要四对点")
    for col in range(8):
        pivot = max(range(col, 8), key=lambda i: abs(rows[i][col]))
        if abs(rows[pivot][col]) < 1e-10:
            raise ValueError("四点退化，无法计算逆透视")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        s = rows[col][col]
        rows[col] = [v/s for v in rows[col]]
        for i in range(8):
            if i != col:
                s = rows[i][col]
                rows[i] = [a-s*b for a, b in zip(rows[i], rows[col])]
    return tuple(row[8] for row in rows) + (1.,)


def transform(matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f, g, h, i = matrix
    den = g*x+h*y+i
    if abs(den) < 1e-10:
        raise ValueError("映射穿过无穷远，请重新选择地面四角")
    return (a*x+b*y+c)/den, (d*x+e*y+f)/den


class Calibration:
    def __init__(self, source_size, points, width_cm, length_cm, pixels_per_cm):
        self.source_size = tuple(source_size)
        if (len(self.source_size) != 2 or
                any(type(v) is not int or not 2 <= v <= 8192 for v in self.source_size)):
            raise ValueError("原图尺寸无效")
        sw, sh = self.source_size
        self.points = tuple(tuple(float(v) for v in p) for p in points)
        if len(self.points) != 4 or any(len(p) != 2 for p in self.points):
            raise ValueError("请依次点远左、远右、近右、近左四角")
        if any(not math.isfinite(x) or not math.isfinite(y) or
               not (0 <= x <= sw-1 and 0 <= y <= sh-1) for x, y in self.points):
            raise ValueError("角点必须位于原图内")
        # 图像坐标y向下，按上述顺序应为凸四边形，排除重复/交叉/共线点。
        for j in range(4):
            a, b, c = [self.points[(j+k) % 4] for k in range(3)]
            if (b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]) <= 1e-4:
                raise ValueError("四角顺序错误或共线：远左→远右→近右→近左")
        self.width_cm, self.length_cm, self.pixels_per_cm = map(
            float, (width_cm, length_cm, pixels_per_cm))
        dims = self.width_cm, self.length_cm, self.pixels_per_cm
        if any(not math.isfinite(v) or v <= 0 for v in dims):
            raise ValueError("请填写实测宽、长和正的像素/厘米")
        spans = [self.width_cm*self.pixels_per_cm, self.length_cm*self.pixels_per_cm]
        if any(not math.isfinite(v) or not 1 <= v <= 1023 for v in spans):
            raise ValueError("输出每边须为2～1024像素，请调整像素/厘米")
        self.output_size = tuple(round(v)+1 for v in spans)
        w, h = self.output_size
        rect = ((0., 0.), (w-1., 0.), (w-1., h-1.), (0., h-1.))
        self.forward = homography(self.points, rect)
        self.inverse = homography(rect, self.points)
        for matrix, corners in ((self.forward, self.points), (self.inverse, rect)):
            dens = [matrix[6]*x+matrix[7]*y+matrix[8] for x, y in corners]
            if (any(not math.isfinite(v) for v in matrix) or
                    min(dens)*max(dens) <= 0 or min(abs(v) for v in dens) < 1e-9):
                raise ValueError("标定区域跨越地平线")

    def to_dict(self) -> dict:
        return dict(version=1, source_size=self.source_size, points=self.points,
                    point_order="far_left,far_right,near_right,near_left",
                    width_cm=self.width_cm, length_cm=self.length_cm,
                    pixels_per_cm=self.pixels_per_cm, output_size=self.output_size,
                    source_to_bird=self.forward, bird_to_source=self.inverse)

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("不支持的标定版本")
        # 从原始点和尺寸重算，不信任文件里保存的矩阵。
        return cls(*(data[k] for k in ("source_size", "points", "width_cm", "length_cm", "pixels_per_cm")))

    def warp(self, rgb: bytes, source_size, *, binary=False) -> bytes:
        if tuple(source_size) != self.source_size or len(rgb) != math.prod(self.source_size)*3:
            raise ValueError("标定与原图尺寸不一致，摄像头姿态或分辨率变化后须重新标定")
        sw, sh = self.source_size
        w, h = self.output_size
        out = bytearray(w*h*3)
        for y in range(h):
            for x in range(w):
                sx, sy = transform(self.inverse, x, y)
                if not (-1e-6 <= sx <= sw-1+1e-6 and -1e-6 <= sy <= sh-1+1e-6):
                    continue
                sx, sy = min(sw-1., max(0., sx)), min(sh-1., max(0., sy))
                target = (y*w+x)*3
                if binary:
                    offset = (round(sy)*sw+round(sx))*3
                    out[target:target+3] = rgb[offset:offset+3]
                else:
                    ix, iy = int(sx), int(sy)
                    dx, dy = sx-ix, sy-iy
                    for ch in range(3):
                        def at(xx, yy):
                            return rgb[(min(sh-1, yy)*sw+min(sw-1, xx))*3+ch]
                        value = ((1-dx)*(1-dy)*at(ix, iy) + dx*(1-dy)*at(ix+1, iy)
                                 + (1-dx)*dy*at(ix, iy+1) + dx*dy*at(ix+1, iy+1))
                        out[target+ch] = round(value)
        return bytes(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="用已有四点标定变换一张干净PNG")
    parser.add_argument("--input", required=True)
    parser.add_argument("--calibration", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--binary", action="store_true", help="二值图用最近邻，保留0/255")
    args = parser.parse_args()
    from .extras import load_png_rgb
    from .image import write_png
    rgb, w, h = load_png_rgb(Path(args.input))
    cal = Calibration.load(args.calibration)
    output = cal.warp(rgb, (w, h), binary=args.binary)
    write_png(args.out, *cal.output_size, output)


if __name__ == "__main__":
    main()
