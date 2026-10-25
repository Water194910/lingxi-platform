"""桌面发行版的用户数据位置，不向EXE解包目录写配置或图像。"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

PRODUCT_NAME = "灵犀平台"
VERSION = "0.2.0-preview.7"


def choose_writable_directory(candidates) -> Path:
    failures = []
    for candidate in candidates:
        root = Path(candidate).expanduser().resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
            # 目录存在也不代表能写；实际创建/删除小文件后再决定。
            with tempfile.NamedTemporaryFile(prefix=".lingxi-write-", dir=root):
                pass
            return root
        except OSError as exc:
            failures.append(f"{root}: {exc}")
    raise OSError("找不到可写的数据目录。请将便携包解压到可写文件夹后启动，"
                  "或通过LINGXI_DATA_DIR指定目录。\n"+"\n".join(failures))


def data_directory() -> Path:
    override = os.environ.get("LINGXI_DATA_DIR")
    if override:
        return choose_writable_directory([override])
    else:
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home()/".local"/"share")))/"LingXi"
        portable = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                    else Path(__file__).resolve().parents[1])/'userdata'
        return choose_writable_directory([local, portable, Path.home()/'LingXiData'])
