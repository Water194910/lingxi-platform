# 灵犀平台 · LingXi Platform

智能车视觉与设备工作台：逐飞 V2 实时图传、原图保存、历史帧分析、四点逆透视。支持浅色/深色和可关闭的轻量动效。

这是基于组内 **zf-ai-bridge** 的开源衍生工具，非逐飞官方产品。沿用 GPLv3，保留原有来源说明。软件处于预览阶段，车辆标定和算法效果仍需现场验证。

## 下载与运行

Windows 10/11 x64 用户请到 **[Releases 下载](https://github.com/Water194910/lingxi-platform/releases)**，选择 `LingXi-版本-windows-x64.zip`。解压整个包，双击 `LingXi/LingXi.exe`，无需安装 Python；保留相邻的 `_internal`，不要在 ZIP 中直接运行。

当前源码版本：`0.2.0-preview.7`，公开下载版本以Releases为准。应用**未签名**，可能显示 SmartScreen“Windows已保护你的电脑”；签名申请尚未完成。下载页提供匹配源码 ZIP 和 `SHA256SUMS.txt`。若决定运行，请先核对来源及哈希，无需关闭系统防护。

## 功能

- **视觉工作台**：WiFi TCP/串口接收，灰度、二值、RGB565；边线/路径独立预览，原始图像保持干净。
- **历史图像**：单击预览、双击锁定、顶部“回到实时”；支持单张、多选和全部保存，不降分辨率。
- **图像分析**：像素探针、像素矩阵、四点逆透视；PNG/JPEG/BMP导入、原始AVI录像。
- **高级逆透视标定**：滑条与精确数值联动、角点拖动、多组实测拟合、误差检查、实时鸟瞰、有效掩码及图像/边线点C查表导出。
- **设备与事件**：通道数据、可选官方助手旁路、供AI客户端使用的本机接口。
- **显示**：浅色/深色、按钮渐变与回弹、页面滑入；“显示 → 界面动效”可关闭动画。

## 接收图像

1. 电脑和模块加入同一网络，或模块连接电脑的2.4GHz热点。
2. 输入源选择 `tcp-listen://0.0.0.0:8081`，点击“连接设备”。同一时刻只运行一个接收程序。
3. 板端TCP连接**电脑在该网络中的IPv4地址**和端口 `8081`，发送逐飞 **V2** 协议（MCU帧头 `0xAA`、PC帧头 `0x55`）。模块本地端口依固件配置。
4. Windows提示联网许可时，按实际网络类型允许程序接收。模块连热点不代表TCP已连接；无帧时核对输入源、电脑IP、端口、防火墙程序许可及板端发送状态。

串口示例：`serial://COM5:115200`；`demo://` 演示波形，不模拟摄像头。灰度/算法二值切换取决于板端固件，各车按键和引脚可能不同。实际帧率与长稳需现场测试，不保证固定帧率。

默认原图保存不含边线、路径或伪彩色。官方助手旁路转发完整协议，其标注开关独立。官方EXE单独安装，通过“设备与事件 → 打开官方助手”选择，不随本工具捆绑。

## 历史图与导出

“采集 → 历史帧”或“图像分析 → 选择历史帧”：单击预览，双击或“锁定查看”固定图像。后台继续接收，顶部“回到实时”恢复最新图；无新图时显示等待。

“保存选中…”与“保存全部…”支持Ctrl/Shift多选；单张选PNG路径，多张选文件夹，批量重名自动加序号。历史窗口保留打开时的快照，后台清理源文件也不影响本次导出。缓存只保留最近历史帧，长期留存请及时保存或录制。

“采集 → 保存当前原图…”保存实时点击瞬间或锁定/导入的原图，保存对话框期间的新帧不会替换冻结的导出图。

## 数据与隐私

优先使用 `%LOCALAPPDATA%\LingXi`，不可写时回退到EXE旁 `userdata`，再尝试用户目录 `LingXiData`；会实际检测可写性。“打开数据目录”可访问图像。

`LINGXI_DATA_DIR` 可指定数据根目录，显式路径不可写时报告错误。源码运行可先执行：

```powershell
$env:LINGXI_DATA_DIR = "$PWD\userdata"
```

无内置账户、云端图像上传或使用统计。图像、标定、录像及设置保存在本机。设备连接、官方助手旁路和AI接口按操作者配置建立；AI接口默认 `127.0.0.1:8765`，设备输入源默认监听所有网卡8081。每次启动默认关闭“允许AI写设备”，写前需确认固件支持和参数范围。

不要提交个人设置、热点密码、图像录像或签名私钥。

## 四点逆透视

固定相机，将棋盘格或已知尺寸矩形平放地面。冻结原图，按**远左 → 远右 → 近右 → 近左**选四角，填实际厘米尺寸，再生成俯视图；灰度双线性，二值最近邻。

这是地面平面变换，不自动校正镜头畸变，不代替巡线/补线；相机位置或图像尺寸改变需重标。详见 [标定说明](docs/逆透视标定使用说明.md)。

“图像分析 → 高级逆透视”适用于188×120原图。实测宽/长/距离单位统一为毫米，支持三组冻结样本；常调视图参数用滑条粗调、数值框精调。导出包含原始样本、鸟瞰图、有效掩码及两张C查表，不直接覆盖车辆工程。详见[高级标定操作](docs/高级逆透视标定.md)。

## 源码运行

Windows Python 3.12 x64（含Tcl/Tk），运行依赖Pillow/pyserial；可直接安装固定构建依赖：

```powershell
git clone https://github.com/Water194910/lingxi-platform.git
cd lingxi-platform
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe desktop.py
# 自动启动接收：
.\.venv\Scripts\python.exe desktop.py --autostart
```

桌面启动器为 `Start-LingXi.bat`，命令行桥入口 `python -m zf_ai_bridge --help`。AI客户端：

```powershell
.\.venv\Scripts\python.exe client.py snapshot --port 8765 --seconds 5
.\.venv\Scripts\python.exe client.py frame --port 8765 --frames-dir .\userdata\frames
.\.venv\Scripts\python.exe client.py self-test
```

离线命令的 `--frames-dir` 应与应用实际数据目录一致。

## 测试与构建

```powershell
.\.venv\Scripts\python.exe tests/test_p0.py
.\.venv\Scripts\python.exe tests/test_raw_perspective.py
.\.venv\Scripts\python.exe tests/test_ipm.py
.\.venv\Scripts\python.exe desktop.py --self-test-report test-results/source.json
.\build_windows.ps1 -PythonPath .\.venv\Scripts\python.exe
.\dist\LingXi\LingXi.exe --self-test-report test-results/frozen.json
.\.venv\Scripts\python.exe tools/package_windows.py --binary dist/LingXi --report test-results/frozen.json --output release
```

冻结自检实际运行EXE，使用临时数据/动态回环端口，不连接车辆。打包核对EXE与报告哈希、要求源码已提交，附匹配源码ZIP/第三方许可并生成SHA256清单；打包前提交源码变化。GitHub Actions **Windows build** 会测试、构建和上传运行附件，支持手动执行和版本标签触发；正式下载包通过Releases提供。

## Windows签名

当前无可信代码签名证书，免费开源签名尚未申请/获批。建立仓库或包含签名脚本不代表EXE已签名。正式签名显示可验证发布者，但新程序仍可能因信誉不足显示SmartScreen。

`sign_windows.ps1` 适用于已安装在 `CurrentUser/My`、私钥可用、Windows可信的代码签名证书及Windows SDK SignTool。不生成自签名、不修改根证书信任；云服务按提供方工具配置，凭据不入库：

```powershell
.\build_windows.ps1 -PythonPath .\.venv\Scripts\python.exe -SigningCertificateThumbprint '<证书指纹>'
```

顺序为构建 → 签名 → 冻结EXE自检 → 重打包并重算SHA256。每次构建都需重签。

## 许可与来源

沿用 [GPLv3](LICENSE)，来源/第三方依赖见 [NOTICE](NOTICE.md)。[原始导入清单](docs/IMPORT_MANIFEST.json)仅记录导入时字节，不是当前源码校验清单。发布源码以对应Git标签和附带源码包为准。

[问题反馈](https://github.com/Water194910/lingxi-platform/issues)请附版本、输入源类型、复现步骤及必要日志，隐藏个人配置。
