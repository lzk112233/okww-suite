#!/usr/bin/env python3
"""
hidg.py — 树莓派 USB HID gadget 输出（Linux configfs，/dev/hidg*）

树莓派 Zero 2 W / Pi 4 / Pi 5 的 USB-C（或 Zero 的第二个 micro-USB）口
可以工作在 device 模式，把自己枚举成一个复合 HID 设备。这样就不需要
RP2040 板子了 —— 树莓派同时干两件事：收网络的帧、当 USB 键鼠。

先在 /boot/firmware/config.txt 加 dtoverlay=dwc2，cmdline.txt 加
modules-load=dwc2,libcomposite，然后跑 setup_gadget.sh 建立 /dev/hidg0(键盘)
和 /dev/hidg1(鼠标)。

报告格式（boot protocol 兼容，Windows/Linux/macOS 都认）：
  键盘 8 字节：modifier, reserved(0), key0..key5
  鼠标 4 字节：buttons, dx(int8), dy(int8), wheel(int8)
"""
from __future__ import annotations

import os
import struct

# ── USB HID 报告描述符 ──

KB_REPORT_DESC = bytes([
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x06,        # Usage (Keyboard)
    0xA1, 0x01,        # Collection (Application)
    0x05, 0x07,        #   Usage Page (Key Codes)
    0x19, 0xE0,        #   Usage Minimum (224)
    0x29, 0xE7,        #   Usage Maximum (231)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x75, 0x01,        #   Report Size (1)
    0x95, 0x08,        #   Report Count (8)
    0x81, 0x02,        #   Input (Data, Variable, Absolute)  ← 8 个修饰键
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x08,        #   Report Size (8)
    0x81, 0x01,        #   Input (Constant)  ← 保留字节
    0x95, 0x06,        #   Report Count (6)
    0x75, 0x08,        #   Report Size (8)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x65,        #   Logical Maximum (101)
    0x05, 0x07,        #   Usage Page (Key Codes)
    0x19, 0x00,        #   Usage Minimum (0)
    0x29, 0x65,        #   Usage Maximum (101)
    0x81, 0x00,        #   Input (Data, Array)  ← 6 个同时按下的键
    0xC0,              # End Collection
])

MS_REPORT_DESC = bytes([
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x02,        # Usage (Mouse)
    0xA1, 0x01,        # Collection (Application)
    0x09, 0x01,        #   Usage (Pointer)
    0xA1, 0x00,        #   Collection (Physical)
    0x05, 0x09,        #     Usage Page (Buttons)
    0x19, 0x01,        #     Usage Minimum (1)
    0x29, 0x03,        #     Usage Maximum (3)
    0x15, 0x00,        #     Logical Minimum (0)
    0x25, 0x01,        #     Logical Maximum (1)
    0x95, 0x03,        #     Report Count (3)
    0x75, 0x01,        #     Report Size (1)
    0x81, 0x02,        #     Input (Data, Variable, Absolute)  ← 左右中键
    0x95, 0x01,        #     Report Count (1)
    0x75, 0x05,        #     Report Size (5)
    0x81, 0x01,        #     Input (Constant)  ← 补齐
    0x05, 0x01,        #     Usage Page (Generic Desktop)
    0x09, 0x30,        #     Usage (X)
    0x09, 0x31,        #     Usage (Y)
    0x09, 0x38,        #     Usage (Wheel)
    0x15, 0x81,        #     Logical Minimum (-127)
    0x25, 0x7F,        #     Logical Maximum (127)
    0x75, 0x08,        #     Report Size (8)
    0x95, 0x03,        #     Report Count (3)
    0x81, 0x06,        #     Input (Data, Variable, Relative)  ← 相对位移
    0xC0,              #   End Collection
    0xC0,              # End Collection
])


class HidGadget:
    """/dev/hidg0 = 键盘，/dev/hidg1 = 鼠标。"""

    def __init__(self, kb_path: str = "/dev/hidg0", ms_path: str = "/dev/hidg1"):
        self.kb_path = kb_path
        self.ms_path = ms_path
        self._kb = open(kb_path, "wb", buffering=0)
        self._ms = open(ms_path, "wb", buffering=0)
        self._mod = 0
        self._keys: list[int] = []
        self._buttons = 0
        self.release_all()

    # ---- 键盘 ----
    def keyboard_report(self, mod: int, keys: list[int]) -> None:
        k = (list(keys) + [0] * 6)[:6]
        self._kb.write(bytes([mod & 0xFF, 0] + k))

    def key(self, hid_usage: int, down: bool) -> None:
        if down:
            if hid_usage not in self._keys:
                self._keys.append(hid_usage)
                if len(self._keys) > 6:
                    self._keys = self._keys[-6:]
        else:
            if hid_usage in self._keys:
                self._keys.remove(hid_usage)
        self.keyboard_report(self._mod, self._keys)

    def modifier(self, bit: int, down: bool) -> None:
        self._mod = (self._mod | bit) if down else (self._mod & ~bit & 0xFF)
        self.keyboard_report(self._mod, self._keys)

    # ---- 鼠标 ----
    def mouse(self, buttons: int, dx: int, dy: int, wheel: int = 0) -> None:
        self._buttons = buttons & 0x07
        dx = max(-127, min(127, int(dx)))
        dy = max(-127, min(127, int(dy)))
        wh = max(-127, min(127, int(wheel)))
        self._ms.write(struct.pack("<Bbbb", self._buttons, dx, dy, wh))

    def buttons(self, mask: int, down: bool) -> None:
        if down:
            self._buttons |= mask
        else:
            self._buttons &= ~mask & 0xFF
        self.mouse(self._buttons, 0, 0, 0)

    # ---- 兜底 ----
    def release_all(self) -> None:
        self._mod = 0
        self._keys = []
        self._buttons = 0
        try:
            self.keyboard_report(0, [])
            self.mouse(0, 0, 0, 0)
        except Exception:
            pass

    def close(self) -> None:
        self.release_all()
        for f in (self._kb, self._ms):
            try:
                f.close()
            except Exception:
                pass


if __name__ == "__main__":
    # 现场验证：在树莓派上跑这个，看游戏机/电脑上光标动不动
    import time

    print("打开 /dev/hidg0(键盘) /dev/hidg1(鼠标) ...")
    g = HidGadget()
    print("PING OK —— 3 秒后做一次右移 60px + 左键点击")
    time.sleep(3)
    for _ in range(6):
        g.mouse(0, 10, 0)
        time.sleep(0.02)
    g.buttons(0x01, True)
    time.sleep(0.12)
    g.buttons(0x01, False)
    print("完成。如果目标机上光标右移并点击了一次，链路就通了。")
    g.close()
