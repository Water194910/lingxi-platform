"""从同一几何标记生成Windows多尺寸图标，无网络依赖。"""
from pathlib import Path
from PIL import Image, ImageDraw


def main():
    assets = Path(__file__).resolve().parent/'assets'
    factor = 4
    image = Image.new('RGBA', (256*factor, 256*factor))
    d = ImageDraw.Draw(image)
    def box(values):
        return tuple(v*factor for v in values)
    d.rounded_rectangle(box((8, 8, 248, 248)), radius=64*factor, fill='#242424')
    for points in (((72, 156), (72, 101), (76, 84), (84, 76), (101, 72), (156, 72)),
                   ((100, 184), (155, 184), (172, 180), (180, 172), (184, 155), (184, 100))):
        d.line([(x*factor, y*factor) for x, y in points], fill='#f2f2f2', width=17*factor, joint='curve')
        for x, y in (points[0], points[-1]):
            d.ellipse(box((x-8.5, y-8.5, x+8.5, y+8.5)), fill='#f2f2f2')
    d.ellipse(box((114, 114, 142, 142)), fill='#a6d4ba')
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(assets/'lingxi.ico', sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])


if __name__ == '__main__':
    main()
