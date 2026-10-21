# 来源与第三方说明

灵犀平台由智能车项目中的 `zf-ai-bridge` 演进而来，桌面入口复用桥接、逐飞V2协议解析、图像解码、矩阵、录像和标定模块，后续加入双主题、数据目录回退、历史帧导出及轻量动效。

初始集成来源：<https://github.com/zby123581-maker/ZhiNengCar> 中的 `tools/zf-ai-bridge/`。原始字节和父提交记录在 `docs/IMPORT_MANIFEST.json`。原模块来源说明保留，沿用GPLv3；不主张对逐飞协议、官方工具或第三方库的所有权。

这是团队维护的开源衍生软件，非逐飞科技官方产品。官方逐飞助手EXE和固件不在本仓库/便携包中。

Windows便携包包含Python、Tcl/Tk、Pillow、pyserial及PyInstaller生成的运行组件。依赖版本见 `requirements-build.txt`，运行时收集的许可保留；Python、Pillow、pyserial及构建依赖的许可证另放在 `ThirdPartyLicenses/`。PyInstaller引导程序采用其许可和相应例外。

签名服务尚未开通，不声称由SignPath Foundation背书或提供签名。
