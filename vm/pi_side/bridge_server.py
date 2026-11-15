#!/usr/bin/env python3
"""
bridge_server.py — 桥端服务：TCP 收 HID 帧 → 落地成真实 USB HID

跑在哪
------
  A) 树莓派（推荐）：USB-C 口当 device 模式，直接出 HID，不需要额外板子
       sudo python3 bridge_server.py --mode gadget
  B) 副机 + RP2040：串口转发给已经烧好 okww_hid.ino 的板子
       python3 bridge_server.py --mode serial --serial /dev/ttyACM0
  C) 只调试不发键：把收到的帧打印出来
       python3 bridge_server.py --mode dry

协议（与 vm_side/net_hid.py 一致）
    AA | CMD | LEN | PAYLOAD | XOR
    0x01 MOUSE  : <hhbB  dx, dy, wheel, buttons
    0x02 KEY    : modifier, k0..k5        ← 已经是 HID usage id，不用再转 VK
    0x03 PING   : 无 payload，回 0x83 + b"OK"
    0x04 RELEASE: 全部释放

断连保护
--------
网络一断，"某个键还按着"会让角色一直往前走。所以连接结束或 5 秒没收到帧，
都会强制 RELEASE 一次。
"""
from __future__ import annotations

import argparse
import signal
import socket
import struct
import sys
import threading
import time

HEAD = 0xAA
CMD_MOUSE = 0x01
CMD_KEY = 0x02
CMD_PING = 0x03
CMD_RELEASE = 0x04
RESP_OK = bytes([0x83]) + b"OK"

MAX_PAYLOAD = 16
IDLE_RELEASE = 5.0        # 秒，无帧就松手


# ────────────────────── 帧解析 ──────────────────────

class FrameReader:
    """流式拆帧。TCP 不保证一次 recv 正好一帧。"""

    def __init__(self):
        self.buf = bytearray()
        self.bad = 0

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while True:
            # 找帧头；前面的垃圾丢掉（多半是上一次断线残留）
            i = self.buf.find(HEAD)
            if i < 0:
                self.buf.clear()
                break
            if i > 0:
                del self.buf[:i]
            if len(self.buf) < 3:
                break
            cmd = self.buf[1]
            ln = self.buf[2]
            if ln > MAX_PAYLOAD:
                self.bad += 1
                del self.buf[:1]
                continue
            total = 3 + ln + 1
            if len(self.buf) < total:
                break
            body = bytes(self.buf[:total - 1])
            chk = self.buf[total - 1]
            x = 0
            for b in body:
                x ^= b
            if x != chk:
                self.bad += 1
                del self.buf[:1]          # 只丢一个字节，重新同步
                continue
            out.append((cmd, body[3:]))
            del self.buf[:total]
        return out


# ────────────────────── 输出后端 ──────────────────────

class GadgetOut:
    name = "gadget"

    def __init__(self):
        from hidg import HidGadget
        self.g = HidGadget()

    def mouse(self, dx, dy, wheel, buttons):
        while dx or dy:
            sx = max(-127, min(127, dx))
            sy = max(-127, min(127, dy))
            self.g.mouse(buttons, sx, sy, 0)
            dx -= sx
            dy -= sy
            if dx or dy:
                time.sleep(0.0007)

    def wheel(self, n, buttons):
        step = 1 if n > 0 else -1
        for _ in range(abs(int(n))):
            self.g.mouse(buttons, 0, 0, step)
            time.sleep(0.012)

    def buttons(self, buttons):
        self.g.mouse(buttons, 0, 0, 0)

    def key(self, mod, keys):
        self.g.keyboard_report(mod, [k for k in keys if k])

    def release(self):
        self.g.release_all()

    def close(self):
        self.g.close()


class SerialOut:
    name = "serial"

    def __init__(self, port: str, baud: int = 115200):
        import serial
        self.ser = serial.Serial(port, baud, timeout=0.02, write_timeout=0.05)
        self._buttons = 0
        time.sleep(0.35)          # 等 CDC 枚举
        self._raw(CMD_PING)

    def _raw(self, cmd, payload=b""):
        body = bytes([HEAD, cmd, len(payload)]) + payload
        chk = 0
        for b in body:
            chk ^= b
        self.ser.write(body + bytes([chk]))
        self.ser.flush()

    # 板子那边自己做分片，但这里也分一次，减少板上循环
    def mouse(self, dx, dy, wheel, buttons):
        self._buttons = buttons
        self._raw(CMD_MOUSE, struct.pack("<hhbB", int(dx), int(dy), int(wheel), int(buttons)))

    def wheel(self, n, buttons):
        self._buttons = buttons
        self._raw(CMD_MOUSE, struct.pack("<hhbB", 0, 0, (1 if n > 0 else -1), int(buttons)))

    def buttons(self, buttons):
        self._buttons = buttons
        self._raw(CMD_MOUSE, struct.pack("<hhbB", 0, 0, 0, int(buttons)))

    def key(self, mod, keys):
        k = (list(keys) + [0] * 6)[:6]
        self._raw(CMD_KEY, bytes([mod] + k))

    def release(self):
        self._raw(CMD_RELEASE)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


# HID usage id -> Windows VK。只覆盖游戏里会用到的键。
_HID2VK = {}
for _i in range(26):                       # A-Z  usage 0x04..0x1D  -> VK 0x41..0x5A
    _HID2VK[0x04 + _i] = 0x41 + _i
_DIGITS = [0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26]   # 1..9
for _i, _h in enumerate(_DIGITS):
    _HID2VK[_h] = 0x31 + _i
_HID2VK[0x27] = 0x30                       # 0
_HID2VK.update({
    0x28: 0x0D, 0x29: 0x1B, 0x2A: 0x08, 0x2B: 0x09, 0x2C: 0x20,
    0x2D: 0xBD, 0x2E: 0xBB, 0x2F: 0xDB, 0x30: 0xDD, 0x31: 0xDC,
    0x33: 0xBA, 0x34: 0xDE, 0x35: 0xC0, 0x36: 0xBC, 0x37: 0xBE, 0x38: 0xBF,
    0x39: 0x14,
    0x49: 0x2D, 0x4A: 0x24, 0x4B: 0x21, 0x4C: 0x2E, 0x4D: 0x23, 0x4E: 0x22,
    0x4F: 0x27, 0x50: 0x25, 0x51: 0x28, 0x52: 0x26,
})
for _i in range(12):                       # F1-F12  usage 0x3A..0x45 -> VK 0x70..0x7B
    _HID2VK[0x3A + _i] = 0x70 + _i

_MOD_BITS = ((0x01, 0xA2), (0x02, 0xA0), (0x04, 0xA4), (0x08, 0x5B),
             (0x10, 0xA3), (0x20, 0xA1), (0x40, 0xA5), (0x80, 0x5C))


class SendInputOut:
    """Windows 上直接用 SendInput 落地。

    没有树莓派/单片机时的降级输出：python 依旧在别的进程（或别的机器）里，
    但按下的是软件注入事件，带 LLMHF_INJECTED 标记。要过 HIDPARSE 双路比对
    还是得换 GadgetOut / SerialOut。
    """
    name = "sendinput"

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        self.wt = wintypes

        ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 \
            else ctypes.c_ulong

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD),
                        ("dwExtraInfo", ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD),
                        ("wParamL", wintypes.WORD),
                        ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT),
                        ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self.MOUSEINPUT, self.KEYBDINPUT, self.INPUT = MOUSEINPUT, KEYBDINPUT, INPUT
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SendInput.argtypes = (wintypes.UINT,
                                          ctypes.POINTER(INPUT),
                                          ctypes.c_int)
        self.user32.SendInput.restype = wintypes.UINT
        self._held_keys = set()
        self._buttons = 0
        self.sent = 0

    # ---------- 内层 ----------
    def _mouse(self, flags, dx=0, dy=0, data=0):
        inp = self.INPUT()
        inp.type = 0                       # INPUT_MOUSE
        inp.u.mi = self.MOUSEINPUT(int(dx), int(dy), int(data), int(flags), 0, 0)
        self.user32.SendInput(1, self.ct.byref(inp), self.ct.sizeof(inp))
        self.sent += 1

    def _key(self, vk, up=False):
        inp = self.INPUT()
        inp.type = 1                       # INPUT_KEYBOARD
        inp.u.ki = self.KEYBDINPUT(int(vk), 0, 0x0002 if up else 0, 0, 0)
        self.user32.SendInput(1, self.ct.byref(inp), self.ct.sizeof(inp))
        self.sent += 1

    # ---------- 对外 ----------
    def mouse(self, dx, dy, wheel, buttons):
        self._sync_buttons(buttons)
        if dx or dy:
            self._mouse(0x0001, int(dx), int(dy))
        if wheel:
            self._mouse(0x0800, data=(0x120 if wheel > 0 else -0x120) & 0xFFFFFFFF)

    def wheel(self, n, buttons):
        self._sync_buttons(buttons)
        self._mouse(0x0800, data=(0x120 if n > 0 else -0x120) & 0xFFFFFFFF)

    def buttons(self, buttons):
        self._sync_buttons(buttons)

    def _sync_buttons(self, want):
        want = int(want) & 0x07
        diff = want ^ self._buttons
        up = (self._buttons & 1) and not (want & 1)
        down = (want & 1) and not (self._buttons & 1)
        if down:
            self._mouse(0x0002)
        if up:
            self._mouse(0x0004)
        up = (self._buttons & 2) and not (want & 2)
        down = (want & 2) and not (self._buttons & 2)
        if down:
            self._mouse(0x0008)
        if up:
            self._mouse(0x0010)
        up = (self._buttons & 4) and not (want & 4)
        down = (want & 4) and not (self._buttons & 4)
        if down:
            self._mouse(0x0020)
        if up:
            self._mouse(0x0040)
        self._buttons = want
        _ = diff

    def key(self, mod, keys):
        want_mod = set(vk for bit, vk in _MOD_BITS if mod & bit)
        want_keys = set(_HID2VK[k] for k in keys if k and k in _HID2VK)
        target = want_mod | want_keys
        for vk in list(self._held_keys - target):
            self._key(vk, up=True)
            self._held_keys.discard(vk)
        # 先按修饰键，再按普通键
        for vk in sorted(target - self._held_keys, key=lambda v: 0 if v >= 0xA0 else 1):
            self._key(vk, up=False)
            self._held_keys.add(vk)

    def release(self):
        for vk in list(self._held_keys):
            self._key(vk, up=True)
        self._held_keys.clear()
        if self._buttons:
            self._sync_buttons(0)

    def close(self):
        self.release()


class DryOut:
    name = "dry"

    def mouse(self, dx, dy, wheel, buttons):
        print(f"  MOUSE dx={dx} dy={dy} wheel={wheel} btn={buttons}")

    def wheel(self, n, buttons):
        print(f"  WHEEL {n} btn={buttons}")

    def buttons(self, buttons):
        print(f"  BTN {buttons}")

    def key(self, mod, keys):
        print(f"  KEY mod={mod:02x} keys={[hex(k) for k in keys if k]}")

    def release(self):
        print("  RELEASE")

    def close(self):
        pass


# ────────────────────── 服务 ──────────────────────

class BridgeServer:

    def __init__(self, out, host: str = "0.0.0.0", port: int = 8765,
                 verbose: bool = True):
        self.out = out
        self.host = host
        self.port = port
        self.verbose = verbose
        self._lock = threading.Lock()     # 多客户端时串行化，别把报告写花
        self._stop = threading.Event()
        self._clients = 0
        self.frames = 0
        self.bad = 0

    def dispatch(self, cmd: int, payload: bytes) -> None:
        with self._lock:
            if cmd == CMD_MOUSE:
                if len(payload) < 6:
                    return
                dx, dy, wh, btn = struct.unpack("<hhbB", payload[:6])
                if dx or dy:
                    self.out.mouse(dx, dy, wh, btn)
                elif wh:
                    self.out.wheel(wh, btn)
                else:
                    self.out.buttons(btn)
            elif cmd == CMD_KEY:
                if len(payload) < 7:
                    return
                self.out.key(payload[0], list(payload[1:7]))
            elif cmd == CMD_RELEASE:
                self.out.release()
            elif cmd == CMD_PING:
                pass                         # 在 handle 里直接回

    def handle(self, conn: socket.socket, addr) -> None:
        self._clients += 1
        rid = f"{addr[0]}:{addr[1]}"
        if self.verbose:
            print(f"[+] 连接 {rid}（当前 {self._clients}）")
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        reader = FrameReader()
        last = time.time()
        try:
            while not self._stop.is_set():
                conn.settimeout(0.5)
                try:
                    data = conn.recv(4096)
                except socket.timeout:
                    if time.time() - last > IDLE_RELEASE:
                        with self._lock:
                            self.out.release()
                        last = time.time()
                    continue
                if not data:
                    break
                last = time.time()
                for cmd, payload in reader.feed(data):
                    if cmd == CMD_PING:
                        try:
                            conn.sendall(RESP_OK)
                        except Exception:
                            pass
                        continue
                    self.frames += 1
                    self.dispatch(cmd, payload)
                if reader.bad:
                    self.bad += reader.bad
                    reader.bad = 0
        except Exception as e:
            if self.verbose:
                print(f"[!] {rid} 异常: {e}")
        finally:
            with self._lock:
                self.out.release()          # 断连必须松手
            try:
                conn.close()
            except Exception:
                pass
            self._clients -= 1
            if self.verbose:
                print(f"[-] 断开 {rid}（剩 {self._clients}）")

    def serve_forever(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(4)
        srv.settimeout(0.5)
        print(f"桥就绪  {self.host}:{self.port}  输出={self.out.name}  "
              f"（Ctrl-C 退出）")
        try:
            while not self._stop.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                threading.Thread(target=self.handle, args=(conn, addr),
                                 daemon=True).start()
        finally:
            self._stop.set()
            srv.close()
            self.out.close()
            print(f"已退出  帧={self.frames} 校验错={self.bad}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ok-ww 网络 HID 桥")
    ap.add_argument("--mode", choices=["gadget", "serial", "sendinput", "dry"],
                    default="gadget")
    ap.add_argument("--serial", default="/dev/ttyACM0", help="serial 模式下的串口")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    if a.mode == "gadget":
        out = GadgetOut()
    elif a.mode == "serial":
        out = SerialOut(a.serial, a.baud)
    elif a.mode == "sendinput":
        out = SendInputOut()
    else:
        out = DryOut()

    srv = BridgeServer(out, a.host, a.port, verbose=not a.quiet)

    def _sig(*_):
        print("\n收到退出信号 ...")
        srv._stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
