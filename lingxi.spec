# Windows x64 便携桌面预览版；使用 build_windows.ps1 构建。
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / 'desktop.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'LICENSE'), '.'), (str(root / 'assets' / 'lingxi.ico'), 'assets')],
    hiddenimports=['serial', 'PIL.Image', 'PIL.ImageTk', 'zf_ai_bridge.desktop_smoke', 'zf_ai_bridge.ipm_smoke'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    # 源码只用标准库 + Pillow + pyserial；Pillow 的 hook 会把构建机上装了但运行用不到的
    # numpy/scipy 等一起收进来（实测多占 26MB），这里显式排除。
    excludes=['numpy', 'scipy', 'pandas', 'matplotlib', 'setuptools', 'pip', 'wheel',
              'pkg_resources', 'IPython', 'pytest', 'tkinter.test', 'test'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='LingXi',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    version=str(root / 'windows_version.txt'),
    icon=str(root / 'assets' / 'lingxi.ico'),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='LingXi')
