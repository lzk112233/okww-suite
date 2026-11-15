"""
net_hid.py — 网络 HID 桥客户端（虚拟机侧）

为什么要有它
------------
USB 设备只能插在一台机器上。上一轮的物理隔离方案是"游戏机 + 副机 + 板子"，
一旦把 ok-ww 挪进虚拟机，板子的 USB 插在游戏机上，虚拟机就摸不到串口了。
这个模块把同一份 HID 帧封装进 TCP，虚拟机通过局域网发给"桥"那台小机器
（树莓派 / ESP32 / 还在跑板子的副机），由它落地成真正的 USB HID 报告。

协议（和 arduino/okww_hid.ino 的串口帧完全一致，只是换了承载）
    AA | CMD | LEN | PAYLOAD(LEN) | XOR
    CMD 0x01 MOUSE  : int16 dx, int16 dy, int8 wheel, uint8 buttons
    CMD 0x02 KEY    : uint8 modifier, uint8 k0..k5
    CMD 0x03 PING   : 无 payload，回 0x83 + b"OK"
    CMD 0x04 RELEASE: 全键位释放

对外接口与 human_input.Backend 对齐：
    name / rel(dx,dy) / button(mask,down) / key(vk,down) / wheel(n)
    / release_all() / close()

自检：
    python net_hid.py --probe 192.168.1.50
"""
from __future__ import annotations

import os
import random
import socket
import struct
import threading
import time

HEAD = 0xAA
CMD_MOUSE = 0x01
CMD_KEY = 0x02
CMD_PING = 0x03
CMD_RELEASE = 0x04
RESP_OK = 0x83

MAX_PAYLOAD = 16          # 与固件 body[16] 对齐
DEFAULT_PORT = 8765

MOUSE_LEFT, MOUSE_RIGHT, MOUSE_MIDDLE = 0x01, 0x02, 0x04

# ── 帧间隔抖动 ──
# 真 HID 设备的报告间隔由硬件定时器产生，天然带方差；固定不变的间隔在
# 内核侧是可测的周期特征。ACE-BASE.sys 静态导入 HIDPARSE.SYS（内核 HID
# 报告解析），所以分片间隔不能是常数。
JITTER = 0.25             # ±25%
_rng = random.Random()


def _gap(base: float) -> None:
    """按 ±JITTER 抖动休眠，避免 HID 报告间隔呈现固定周期。"""
    time.sleep(base * (1.0 + _rng.uniform(-JITTER, JITTER)))

# ── Windows VK -> USB HID Usage ID ──
VK_TO_HID = {
    0x08: 0x2A, 0x09: 0x2B, 0x0D: 0x28, 0x1B: 0x29, 0x20: 0x2C,
    0x25: 0x50, 0x26: 0x52, 0x27: 0x4F, 0x28: 0x51,
    0x2D: 0x49, 0x2E: 0x4C, 0x24: 0x4A, 0x23: 0x4D,
    0x21: 0x4B, 0x22: 0x4E, 0x2C: 0x46, 0x2F: 0x48,
    0x30: 0x27, 0x31: 0x1E, 0x32: 0x1F, 0x33: 0x20, 0x34: 0x21,
    0x35: 0x22, 0x36: 0x23, 0x37: 0x24, 0x38: 0x25, 0x39: 0x26,
    0x41: 0x04, 0x42: 0x05, 0x43: 0x06, 0x44: 0x07, 0x45: 0x08,
    0x46: 0x09, 0x47: 0x0A, 0x48: 0x0B, 0x49: 0x0C, 0x4A: 0x0D,
    0x4B: 0x0E, 0x4C: 0x0F, 0x4D: 0x10, 0x4E: 0x11, 0x4F: 0x12,
    0x50: 0x13, 0x51: 0x14, 0x52: 0x15, 0x53: 0x16, 0x54: 0x17,
    0x55: 0x18, 0x56: 0x19, 0x57: 0x1A, 0x58: 0x1B, 0x59: 0x1C,
    0x5A: 0x1D,
    0x60: 0x62, 0x61: 0x59, 0x62: 0x5A, 0x63: 0x5B, 0x64: 0x5C,
    0x65: 0x5D, 0x66: 0x5E, 0x67: 0x5F, 0x68: 0x60, 0x69: 0x61,
    0x6A: 0x55, 0x6B: 0x56, 0x6C: 0x57, 0x6D: 0x58, 0x6E: 0x63,
    0x6F: 0x54, 0x70: 0x46,
    0xBA: 0x33, 0xBB: 0x2E, 0xBC: 0x36, 0xBD: 0x2D, 0xBE: 0x37,
    0xBF: 0x38, 0xC0: 0x35, 0xDB: 0x2F, 0xDC: 0x31, 0xDD: 0x30,
    0xDE: 0x34,
}
for _i in range(12):                       # F1..F12
    VK_TO_HID[0x70 + _i] = 0x3A + _i

# 修饰键 VK -> 报告字节位
VK_MOD_BIT = {
    0xA2: 0x01, 0xA0: 0x02, 0xA4: 0x04, 0x5B: 0x08,
    0xA3: 0x10, 0xA1: 0x20, 0xA5: 0x40, 0x5C: 0x80,
}


def frame(cmd: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload 超长: {len(payload)} > {MAX_PAYLOAD}")
    body = bytes([HEAD, cmd, len(payload)]) + payload
    chk = 0
    for b in body:
        chk ^= b
    return body + bytes([chk])


class NetHidError(RuntimeError):
    pass


class NetHidBackend:
    """把 HID 帧走 TCP 送到桥设备。接口与 okww_shim.human_input.Backend 一致。

    线程安全：内部一把锁，ok-ww 多线程打输入也不会把帧拆错。
    断线自愈：send 失败一次就标记，下次调用前重连（不抛给调用方，避免脚本崩）。
    """

    name = "net_hid"

    def __init__(self, host: str | None = None, port: int | None = None,
                 timeout: float = 0.6, reconnect: bool = True):
        self.host = host or os.environ.get("OKWW_HID_HOST", "")
        self.port = int(port or os.environ.get("OKWW_HID_PORT", DEFAULT_PORT))
        self.timeout = timeout
        self.reconnect = reconnect
        self._sock: socket.socket | None = None
        self._lock = threading.RLock()
        self._buttons = 0
        self._mod = 0
        self._keys: list[int] = []
        self.stats = {"frames": 0, "reconnects": 0, "errors": 0}
        if not self.host:
            raise NetHidError("没给 host：设 OKWW_HID_HOST 或直接传参")
        self._connect()
        if not self.ping():
            # 桥在但没回 OK —— 多半是端口对、程序不对
            raise NetHidError(f"{self.host}:{self.port} 连上了但不回应 PING")

    # ---------- 连接 ----------
    def _connect(self) -> None:
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        try:  # 1ms 内发出去；Wi-Fi 下这两个值影响手感
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
        except Exception:
            pass
        self._sock = s

    def _ensure(self) -> socket.socket:
        if self._sock is None:
            self._connect()
            self.stats["reconnects"] += 1
        return self._sock

    # ---------- 帧 ----------
    def _send(self, cmd: int, payload: bytes = b"") -> None:
        data = frame(cmd, payload)
        with self._lock:
            try:
                sock = self._ensure()
                sock.sendall(data)
                self.stats["frames"] += 1
            except Exception:
                self.stats["errors"] += 1
                self._drop()
                if not self.reconnect:
                    raise
                try:                       # 立刻重试一次
                    sock = self._ensure()
                    sock.sendall(data)
                    self.stats["frames"] += 1
                except Exception as e:
                    self._drop()
                    raise NetHidError(f"HID 桥不可达: {e}") from e

    def _drop(self) -> None:
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None

    # ---------- 鼠标 ----------
    def rel(self, dx: int, dy: int) -> None:
        dx, dy = int(dx), int(dy)
        if not dx and not dy:
            return
        while dx or dy:
            sx = max(-127, min(127, dx))
            sy = max(-127, min(127, dy))
            self._send(CMD_MOUSE, struct.pack("<hhbB", sx, sy, 0, self._buttons))
            dx -= sx
            dy -= sy
            if dx or dy:                   # 大位移分片间留空隙，别一整帧瞬移
                _gap(0.0007)

    def button(self, mask: int, down: bool) -> None:
        if down:
            self._buttons |= mask
        else:
            self._buttons &= ~mask & 0xFF
        self._send(CMD_MOUSE, struct.pack("<hhbB", 0, 0, 0, self._buttons))

    def wheel(self, n: int) -> None:
        step = 1 if n > 0 else -1
        for _ in range(abs(int(n))):
            self._send(CMD_MOUSE, struct.pack("<hhbB", 0, 0, step, self._buttons))
            _gap(0.012)

    # ---------- 键盘 ----------
    def _flush_keys(self) -> None:
        keys = (self._keys[:6] + [0] * 6)[:6]
        self._send(CMD_KEY, bytes([self._mod] + keys))

    def key(self, vk: int, down: bool) -> None:
        if vk in VK_MOD_BIT:
            bit = VK_MOD_BIT[vk]
            self._mod = (self._mod | bit) if down else (self._mod & ~bit & 0xFF)
            self._flush_keys()
            return
        hid = VK_TO_HID.get(vk)
        if hid is None:
            return
        if down:
            if hid not in self._keys:
                self._keys.append(hid)
                if len(self._keys) > 6:    # 6KRO
                    self._keys = self._keys[-6:]
        else:
            if hid in self._keys:
                self._keys.remove(hid)
        self._flush_keys()

    # ---------- 健康 ----------
    def ping(self, timeout: float | None = None) -> bool:
        to = self.timeout if timeout is None else timeout
        with self._lock:
            try:
                sock = self._ensure()
                sock.settimeout(to)
                sock.sendall(frame(CMD_PING))
                deadline = time.time() + to
                buf = b""
                while time.time() < deadline:
                    chunk = sock.recv(16)
                    if not chunk:
                        break
                    buf += chunk
                    if RESP_OK in buf and b"OK" in buf:
                        return True
                return False
            except Exception:
                self._drop()
                return False
            finally:
                try:
                    self._sock.settimeout(self.timeout)
                except Exception:
                    pass

    def release_all(self) -> None:
        self._buttons = 0
        self._mod = 0
        self._keys = []
        try:
            self._send(CMD_RELEASE)
        except Exception:
            pass

    def close(self) -> None:
        self.release_all()
        with self._lock:
            self._drop()


# ── 探针：直接对桥喊一声 ──
def _probe(argv: list[str]) -> int:
    host = argv[1] if len(argv) > 1 else os.environ.get("OKWW_HID_HOST", "")
    port = int(argv[2]) if len(argv) > 2 else int(os.environ.get("OKWW_HID_PORT", DEFAULT_PORT))
    if not host:
        print("用法: python net_hid.py --probe <host> [port]")
        return 2
    print(f"连接 {host}:{port} ...")
    try:
        b = NetHidBackend(host, port)
    except Exception as e:
        print("失败:", e)
        return 1
    print("PING OK")
    print("回环测试：鼠标右移 40px、按下左键 120ms、释放")
    b.rel(40, 0)
    time.sleep(0.05)
    b.button(MOUSE_LEFT, True)
    time.sleep(0.12)
    b.button(MOUSE_LEFT, False)
    print("stats =", b.stats)
    b.close()
    return 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--probe":
        raise SystemExit(_probe(sys.argv[1:]))
    print("用法: python net_hid.py --probe <host> [port]")
    print("或者被 install_vm.py 导入使用")
