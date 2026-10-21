"""输入源：串口 / TCP / 文件回放 / 内置演示波形。

每个源都支持 write()，供 AI 调参帧回写。
"""
from __future__ import annotations

import math
import socket
import struct
import threading
import time
from typing import Callable, Optional

from .protocol import (
    CMD_OSCILLOSCOPE,
    HEAD_SEND,
    build_debug_param_frame,
)

OnBytes = Callable[[bytes], None]


def build_oscilloscope_frame(values: list[float]) -> bytes:
    """构造一帧 V2 示波器数据（测试/演示用）。"""
    n = len(values)
    hdr = bytearray(8)
    hdr[0] = HEAD_SEND
    hdr[2] = CMD_OSCILLOSCOPE
    hdr[3] = n
    hdr[4] = 0  # 小端 float
    payload = struct.pack("<" + "f" * n, *values)
    hdr[1] = (HEAD_SEND + sum(hdr[2:8]) + sum(payload)) & 0xFF
    return bytes(hdr) + payload


class BaseSource:
    def start(self) -> None: ...
    def stop(self) -> None: ...

    def write(self, data: bytes) -> bool:
        """回写字节到设备。默认不支持。"""
        return False

    @property
    def writable(self) -> bool:
        return False


class FileSource(BaseSource):
    """把文件里的原始字节按块喂给解析器（可循环）。只读。"""

    def __init__(self, path: str, on_bytes: OnBytes, loop: bool = False, chunk: int = 256) -> None:
        self.path = path
        self.on_bytes = on_bytes
        self.loop = loop
        self.chunk = chunk
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        while self._running:
            with open(self.path, "rb") as f:
                while self._running:
                    data = f.read(self.chunk)
                    if not data:
                        break
                    self.on_bytes(data)
                    time.sleep(0.002)
            if not self.loop:
                break


class TcpInputSource(BaseSource):
    """连接 MCU / WiFi 模块的 TCP 输出，可双向回写。"""

    def __init__(self, host: str, port: int, on_bytes: OnBytes) -> None:
        self.host = host
        self.port = port
        self.on_bytes = on_bytes
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._sock: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        with self._sock_lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    @property
    def writable(self) -> bool:
        return self._sock is not None

    def write(self, data: bytes) -> bool:
        with self._sock_lock:
            sock = self._sock
        if sock is None:
            return False
        try:
            sock.sendall(data)
            return True
        except OSError:
            return False

    def _run(self) -> None:
        while self._running:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=3)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._sock_lock:
                    self._sock = sock
                while self._running:
                    data = sock.recv(4096)
                    if not data:
                        break
                    self.on_bytes(data)
            except OSError:
                time.sleep(1.0)
            finally:
                with self._sock_lock:
                    self._sock = None
                try:
                    sock.close()  # type: ignore[name-defined]
                except Exception:
                    pass


class TcpListenSource(BaseSource):
    """TCP 服务端：等待 WiFi SPI 模块 / MCU 主动连进来（逐飞图传常用）。

    模块侧典型配置：
      wifi_spi_wifi_connect("SSID", "password");
      wifi_spi_socket_connect("TCP", "<电脑IP>", "8080", "6666");
    """

    def __init__(self, host: str, port: int, on_bytes: OnBytes) -> None:
        self.host = host
        self.port = port
        self.on_bytes = on_bytes
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._server: Optional[socket.socket] = None
        self._sock: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()
        self.peer: str = ""

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        with self._sock_lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass

    @property
    def writable(self) -> bool:
        return self._sock is not None

    def write(self, data: bytes) -> bool:
        with self._sock_lock:
            sock = self._sock
        if sock is None:
            return False
        try:
            sock.sendall(data)
            return True
        except OSError:
            return False

    def _run(self) -> None:
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(4)
            self._server = server
        except OSError:
            return
        while self._running:
            try:
                conn, addr = server.accept()
            except OSError:
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.peer = f"{addr[0]}:{addr[1]}"
            with self._sock_lock:
                self._sock = conn
            try:
                while self._running:
                    data = conn.recv(4096)
                    if not data:
                        break
                    self.on_bytes(data)
            except OSError:
                pass
            finally:
                with self._sock_lock:
                    self._sock = None
                try:
                    conn.close()
                except OSError:
                    pass
        try:
            server.close()
        except OSError:
            pass


class SerialSource(BaseSource):
    """可选依赖 pyserial。可双向回写。"""

    def __init__(self, port: str, baud: int, on_bytes: OnBytes) -> None:
        self.port = port
        self.baud = baud
        self.on_bytes = on_bytes
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._ser = None
        self._ser_lock = threading.Lock()

    def start(self) -> None:
        try:
            import serial  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("串口输入需要 pyserial：pip install pyserial") from exc
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        with self._ser_lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None

    @property
    def writable(self) -> bool:
        return self._ser is not None

    def write(self, data: bytes) -> bool:
        with self._ser_lock:
            ser = self._ser
        if ser is None:
            return False
        try:
            ser.write(data)
            return True
        except Exception:
            return False

    def _run(self) -> None:
        import serial  # type: ignore

        while self._running:
            try:
                ser = serial.Serial(self.port, self.baud, timeout=0.2)
                with self._ser_lock:
                    self._ser = ser
                while self._running:
                    data = ser.read(4096)
                    if data:
                        self.on_bytes(data)
            except Exception:
                time.sleep(1.0)
            finally:
                with self._ser_lock:
                    self._ser = None
                try:
                    ser.close()  # type: ignore[name-defined]
                except Exception:
                    pass


class DemoSource(BaseSource):
    """内置演示：4 通道波形。write() 仅记录，用于离线验证链路。"""

    def __init__(self, on_bytes: OnBytes, rate_hz: float = 50.0) -> None:
        self.on_bytes = on_bytes
        self.rate_hz = rate_hz
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._t = 0.0
        self.written: list[bytes] = []

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    @property
    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> bool:
        self.written.append(data)
        return True

    def _run(self) -> None:
        period = 1.0 / self.rate_hz
        while self._running:
            t = self._t
            values = [
                math.sin(t * 2.0),
                0.5 * math.sin(t * 5.0 + 1.0),
                2.0 if int(t * 2) % 2 == 0 else -2.0,
                0.1 * math.sin(t * 0.5),
            ]
            self.on_bytes(build_oscilloscope_frame(values))
            self._t += period
            time.sleep(period)


class LoopbackSource(BaseSource):
    """测试用：写入的字节会立即从 on_bytes 回环，用于端到端写测试。"""

    def __init__(self, on_bytes: OnBytes) -> None:
        self.on_bytes = on_bytes
        self.written: list[bytes] = []

    @property
    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> bool:
        self.written.append(data)
        self.on_bytes(data)  # 回环给解析器
        return True


def parse_input_url(url: str, on_bytes: OnBytes) -> BaseSource:
    """
    支持：
      demo://
      file:///path/to/capture.bin[?loop=1]
      tcp://host:port              （本机主动连 MCU）
      tcp-listen://host:port       （等 WiFi 模块连进来；host 可用 0.0.0.0）
      serial://COM3:115200
      loop://                      （测试回环）
    """
    if url.startswith("demo://"):
        return DemoSource(on_bytes)
    if url.startswith("loop://"):
        return LoopbackSource(on_bytes)
    if url.startswith("file://"):
        raw = url[len("file://"):]
        loop = "loop=1" in raw
        path = raw.split("?")[0]
        return FileSource(path, on_bytes, loop=loop)
    if url.startswith("tcp-listen://"):
        rest = url[len("tcp-listen://"):]
        host, _, port_s = rest.partition(":")
        return TcpListenSource(host or "0.0.0.0", int(port_s or "8080"), on_bytes)
    if url.startswith("tcp://"):
        rest = url[len("tcp://"):]
        host, _, port_s = rest.partition(":")
        return TcpInputSource(host or "127.0.0.1", int(port_s or "0"), on_bytes)
    if url.startswith("serial://"):
        rest = url[len("serial://"):]
        port, _, baud_s = rest.partition(":")
        return SerialSource(port, int(baud_s or "115200"), on_bytes)
    raise ValueError(f"不支持的输入源: {url}")


__all__ = [
    "BaseSource",
    "FileSource",
    "TcpInputSource",
    "TcpListenSource",
    "SerialSource",
    "DemoSource",
    "LoopbackSource",
    "parse_input_url",
    "build_oscilloscope_frame",
]
