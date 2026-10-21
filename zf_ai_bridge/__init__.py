"""zf-ai-bridge：把逐飞助手 V2 协议暴露为 zf.telemetry/v1。"""
from .bridge import AiBridge, BridgeConfig
from .protocol import ProtocolParser, build_debug_param_frame

__all__ = ["AiBridge", "BridgeConfig", "ProtocolParser", "build_debug_param_frame"]
__version__ = "0.1.0"
