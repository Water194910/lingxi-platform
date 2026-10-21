"""zf.telemetry/v1 本机桥接服务（对齐 ek.telemetry/v1）。

服务端逐行输出 JSON；客户端可发 serial.write（P2 前默认拒绝写）。
只用标准库。
"""
from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

SCHEMA = "zf.telemetry/v1"


@dataclass
class BridgeConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    allow_write: bool = False
    source_name: str = "zf-oscilloscope"


class AiBridge:
    """NDJSON over TCP。多客户端订阅同一遥测流。"""

    def __init__(self, config: Optional[BridgeConfig] = None) -> None:
        self.config = config or BridgeConfig()
        self._server: Optional[socket.socket] = None
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._running = False
        self._write_handler: Optional[Callable[[str, str], tuple[bool, str]]] = None
        self.stats = {"clients": 0, "droppedBatches": 0, "published": 0}

    # ---- lifecycle -------------------------------------------------

    def start(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.config.host, self.config.port))
        self._server.listen(8)
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False
        with self._lock:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass
            self._clients.clear()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass

    def set_write_handler(self, handler: Callable[[str, str], tuple[bool, str]]) -> None:
        """handler(text, line_ending) -> (ok, message)。"""
        self._write_handler = handler

    def set_allow_write(self, enabled: bool) -> None:
        self.config.allow_write = enabled

    # ---- publish ---------------------------------------------------

    def publish_samples(
        self,
        channels: list[dict[str, Any]],
        samples: list[dict[str, Any]],
        sample_rate_hz: float = 0,
        source: Optional[str] = None,
    ) -> None:
        self._seq += 1
        msg = {
            "schema": SCHEMA,
            "type": "samples",
            "seq": self._seq,
            "source": source or self.config.source_name,
            "sampleRateHz": sample_rate_hz,
            "channels": channels,
            "samples": samples,
        }
        self.stats["published"] += 1
        self._broadcast(msg)

    def publish_event(self, kind: str, payload: dict[str, Any]) -> None:
        msg = {"schema": SCHEMA, "type": "event", "event": kind, **payload}
        self._broadcast(msg)

    def status(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "type": "status",
            "allowWrite": self.config.allow_write,
            "clients": len(self._clients),
            "droppedBatches": self.stats["droppedBatches"],
            "published": self.stats["published"],
            "port": self.config.port,
        }

    # ---- internals -------------------------------------------------

    def _accept_loop(self) -> None:
        assert self._server is not None
        while self._running:
            try:
                conn, addr = self._server.accept()
            except OSError:
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                self._clients.append(conn)
            self._send(conn, {
                "schema": SCHEMA,
                "type": "hello",
                "server": "zf-ai-bridge",
                "allowWrite": self.config.allow_write,
                "sampleRateHint": 0,
            })
            threading.Thread(target=self._client_loop, args=(conn, addr), daemon=True).start()

    def _client_loop(self, conn: socket.socket, addr) -> None:
        buf = b""
        try:
            while self._running:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    self._handle_command(conn, line)
        except OSError:
            pass
        finally:
            with self._lock:
                if conn in self._clients:
                    self._clients.remove(conn)
            try:
                conn.close()
            except OSError:
                pass

    def _handle_command(self, conn: socket.socket, raw: bytes) -> None:
        try:
            cmd = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(conn, {"schema": SCHEMA, "type": "ack", "id": "", "ok": False, "error": "bad json"})
            return

        ctype = cmd.get("type")
        cid = cmd.get("id", "")

        if ctype == "ping":
            self._send(conn, {"schema": SCHEMA, "type": "ack", "id": cid, "ok": True, "pong": True})
            return

        if ctype == "status":
            self._send(conn, self.status())
            return

        if ctype == "serial.write":
            if not self.config.allow_write:
                self._send(conn, {
                    "schema": SCHEMA, "type": "ack", "id": cid, "ok": False,
                    "error": "write disabled; enable in bridge UI/config",
                })
                return
            text = cmd.get("text", "")
            line_ending = cmd.get("lineEnding", "lf")
            if not isinstance(text, str) or not text:
                self._send(conn, {"schema": SCHEMA, "type": "ack", "id": cid, "ok": False, "error": "empty text"})
                return
            if len(text.encode("utf-8")) > 1024:
                self._send(conn, {"schema": SCHEMA, "type": "ack", "id": cid, "ok": False, "error": "text too long"})
                return
            if line_ending not in ("none", "lf", "crlf", "cr"):
                self._send(conn, {"schema": SCHEMA, "type": "ack", "id": cid, "ok": False, "error": "bad lineEnding"})
                return
            if self._write_handler is None:
                self._send(conn, {"schema": SCHEMA, "type": "ack", "id": cid, "ok": False, "error": "no write handler"})
                return
            ok, message = self._write_handler(text, line_ending)
            self._send(conn, {"schema": SCHEMA, "type": "ack", "id": cid, "ok": ok, "message": message})
            return

        self._send(conn, {"schema": SCHEMA, "type": "ack", "id": cid, "ok": False, "error": f"unknown type {ctype!r}"})

    def _broadcast(self, msg: dict[str, Any]) -> None:
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            dead = []
            for c in self._clients:
                try:
                    c.sendall(data)
                except OSError:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)
                self.stats["droppedBatches"] += 1
                try:
                    c.close()
                except OSError:
                    pass

    @staticmethod
    def _send(conn: socket.socket, msg: dict[str, Any]) -> None:
        try:
            conn.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass
