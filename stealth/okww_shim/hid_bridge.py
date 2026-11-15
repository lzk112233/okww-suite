"""
hid_bridge.py — 硬件 HID 桥（RP2040 / ATmega32u4）串口协议客户端

为什么要走硬件：
  反作弊判断"是不是脚本"最硬的一条证据是输入事件的来源标志。
  SendInput 发出来的键鼠在 Raw Input / 低级钩子里一定带
  LLKHF_INJECTED / LLMHF_INJECTED 标志，且同一 tick 内多点连发，
  时间戳间隔为 0。硬件 HID 桥在游戏看来就是一只真实的 USB 键鼠，
  没有注入标志、没有 pid、没有窗口焦点依赖。

协议（与 arduino/okww_hid.ino 对应）
  AA | CMD | LEN | PAYLOAD(LEN) | XOR 校验
  CMD 0x01 MOUSE  : int16 dx, int16 dy, int8 wheel, uint8 buttons
  CMD 0x02 KEY    : uint8 modifier, uint8 k0..k5
  CMD 0x03 PING   : 无 payload，回 0x83 + b"OK"
  CMD 0x04 RELEASE: 全键全键位释放
"""
from __future__ import annotations

import struct
import time

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pyserial 没装
    serial = None
    list_ports = None

HEAD = 0xAA

CMD_MOUSE = 0x01
CMD_KEY = 0x02
CMD_PING = 0x03
CMD_RELEASE = 0x04

# 已知的 HID 桥设备 VID（RP2040 / Arduino / CH340 桥）
KNOWN_VIDS = {
    0x2E8A,  # Raspberry Pi (Pico)
    0x2341,  # Arduino
    0x1B4F,  # SparkFun
    0x239A,  # Adafruit
    0x1209,  # pid.codes
    0x16C0,  # Van Ooijen (V-USB)
    0x1A86,  # WCH CH340/CH343
    0x0483,  # STM32
}

# ---- Windows VK -> USB HID Usage ID ----
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
    0xA0: None, 0xA1: None, 0xA2: None, 0xA3: None,
    0xA4: None, 0xA5: None, 0x5B: None, 0x5C: None,
}
for _i in range(12):  # F1..F12
    VK_TO_HID[0x70 + _i] = 0x3A + _i

# 修饰键 VK -> 报告字节位
VK_MOD_BIT = {
    0xA2: 0x01, 0xA0: 0x02, 0xA4: 0x04, 0x5B: 0x08,
    0xA3: 0x10, 0xA1: 0x20, 0xA5: 0x40, 0x5C: 0x80,
}


class HidBridge:
    """串口 HID 桥。所有移动/按键都从这里出海，游戏侧看不到软件注入。"""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 0.02):
        if serial is None:
            raise RuntimeError("pyserial 没装：pip install pyserial")
        self.port = port
        self._ser = serial.Serial(port, baud, timeout=timeout, write_timeout=0.05)
        self._buttons = 0
        self._mod = 0
        self._keys: list[int] = []
        self._lock_free = True
        time.sleep(0.35)  # 等 CDC 就绪（RP2040 复位后枚举慢）
        self.ping()

    # ---------- 发现 ----------
    @classmethod
    def candidates(cls) -> list[str]:
        if list_ports is None:
            return []
        out = []
        for p in list_ports.comports():
            vid = getattr(p, "vid", None)
            if vid in KNOWN_VIDS:
                out.append(p.device)
        return out

    @classmethod
    def open(cls, port: str | None = None, baud: int = 115200) -> "HidBridge":
        if port:
            return cls(port, baud)
        cands = cls.candidates()
        last = None
        for c in cands:
            try:
                return cls(c, baud)
            except Exception as e:  # 串口被别的程序占着
                last = e
        raise RuntimeError(f"没找到可用的 HID 桥（候选: {cands}）；最后错误: {last}")

    # ---------- 帧 ----------
    def _send(self, cmd: int, payload: bytes = b"") -> None:
        body = bytes([HEAD, cmd, len(payload)]) + payload
        chk = 0
        for b in body:
            chk ^= b
        self._ser.write(body + bytes([chk]))
        self._ser.flush()

    # ---------- 鼠标 ----------
    def mouse_move(self, dx: int, dy: int) -> None:
        dx, dy = int(dx), int(dy)
        if dx == 0 and dy == 0:
            return
        while dx or dy:
            sx = max(-127, min(127, dx))
            sy = max(-127, min(127, dy))
            self._send(CMD_MOUSE, struct.pack("<hhbB", sx, sy, 0, self._buttons))
            dx -= sx
            dy -= sy
            if dx or dy:
                # 大位移分片之间留一点微间隔，避免一整帧 127*N 的瞬移特征
                time.sleep(0.0007)

    def mouse_buttons(self, buttons: int, down: bool) -> None:
        if down:
            self._buttons |= buttons
        else:
            self._buttons &= ~buttons & 0xFF
        self._send(CMD_MOUSE, struct.pack("<hhbB", 0, 0, 0, self._buttons))

    def wheel(self, notches: int) -> None:
        step = 1 if notches > 0 else -1
        for _ in range(abs(int(notches))):
            self._send(CMD_MOUSE, struct.pack("<hhbB", 0, 0, step, self._buttons))
            time.sleep(0.012)

    # ---------- 键盘 ----------
    def _flush_keys(self) -> None:
        keys = self._keys[:6] + [0] * (6 - len(self._keys[:6]))
        self._send(CMD_KEY, bytes([self._mod] + keys))

    def key_vk(self, vk: int, down: bool) -> None:
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
                if len(self._keys) > 6:
                    self._keys = self._keys[-6:]
        else:
            if hid in self._keys:
                self._keys.remove(hid)
        self._flush_keys()

    def release_all(self) -> None:
        self._buttons = 0
        self._mod = 0
        self._keys = []
        self._send(CMD_RELEASE)

    # ---------- 健康 ----------
    def ping(self, timeout: float = 0.4) -> bool:
        try:
            self._send(CMD_PING)
            deadline = time.time() + timeout
            buf = b""
            while time.time() < deadline:
                buf += self._ser.read(4)
                if b"OK" in buf:
                    return True
            return False
        except Exception:
            return False

    def close(self) -> None:
        try:
            self.release_all()
        except Exception:
            pass
        try:
            self._ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    print("候选端口:", HidBridge.candidates())
    b = HidBridge.open()
    print("已连接:", b.port, "ping=", b.ping())
    b.close()
