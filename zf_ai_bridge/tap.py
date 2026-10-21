"""数据旁路转发：把原始字节流同时送给官方逐飞助手等旁路客户端。

典型用法：
  · 模块 → 桥:8080（AI 解析）
  · 桥 → 逐飞助手 TCP 服务端 127.0.0.1:8086（官方 UI 同屏看波形/图传）
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Optional


class TapForwarder:
    """TCP 客户端旁路：把收到的原始字节转发到指定地址。

    target 形如 "127.0.0.1:8086"（逐飞助手当服务端时的本地端口）。
    连接断开会自动重连；转发失败不影响主链路。
    """

    def __init__(self, target: str, reconnect_interval: float = 2.0) -> None:
        host, _, port = target.partition(":")
        self.host = host or "127.0.0.1"
        self.port = int(port or "8086")
        self.reconnect_interval = reconnect_interval
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.sent_bytes = 0
        self.connected = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
        self.connected = False

    def forward(self, data: bytes) -> bool:
        """转发一段原始字节；失败返回 False（静默，不打断主链路）。"""
        with self._lock:
            sock = self._sock
        if sock is None:
            return False
        try:
            sock.sendall(data)
            self.sent_bytes += len(data)
            return True
        except OSError:
            with self._lock:
                self._sock = None
            self.connected = False
            return False

    def _run(self) -> None:
        while self._running:
            try:
                sock = socket.create_connection((self.host, self.port), timeout=3)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._lock:
                    self._sock = sock
                self.connected = True
                # 保持连接直到出错
                while self._running:
                    try:
                        sock.sendall(b"")  # 探测
                    except OSError:
                        break
                    time.sleep(1.0)
            except OSError:
                self.connected = False
                time.sleep(self.reconnect_interval)
            finally:
                with self._lock:
                    if self._sock is not None:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
                self.connected = False
