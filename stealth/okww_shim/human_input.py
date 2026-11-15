"""
human_input.py — 人类化输入层（drop-in 替换 pydirectinput / pyautogui / mouse）

三层意义：
  1) 后端：默认走硬件 HID 桥（无注入标志）> Interception 驱动 > SendInput（最差，兜底）
  2) 轨迹：三次贝塞尔 + 垂直弓形 + 末端过冲回拉 + 中段 1~2 次微停顿
  3) 时序：Fitts 定律定时长、高斯噪声；落点高斯散布；按键 dwell N(85,25)ms

对外 API 与 pydirectinput 对齐：
    moveTo(x, y) / moveRel(dx, dy) / click(x, y, button) / mouseDown / mouseUp
    / press(key) / keyDown / keyUp / typewrite(text) / position() / size()
"""
from __future__ import annotations

import ctypes
import math
import random
import threading
import time
from ctypes import wintypes

_rng = random.Random()
_user32 = ctypes.windll.user32

MOUSE_LEFT, MOUSE_RIGHT, MOUSE_MIDDLE = 0x01, 0x02, 0x04
_BTN = {"left": MOUSE_LEFT, "right": MOUSE_RIGHT, "middle": MOUSE_MIDDLE}

# 键盘名 -> VK（与 pydirectinput.KEYBOARD_MAPPING 兼容的子集 + 常用）
KEYBOARD_MAPPING: dict[str, int] = {
    "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "return": 0x0D,
    "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0xA2, "control": 0xA2, "lctrl": 0xA2, "rctrl": 0xA3,
    "shift": 0xA0, "lshift": 0xA0, "rshift": 0xA1,
    "alt": 0xA4, "lalt": 0xA4, "ralt": 0xA5,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
    "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA,
    "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "`": 0xC0,
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    KEYBOARD_MAPPING[_c] = ord(_c.upper())
for _d in "0123456789":
    KEYBOARD_MAPPING[_d] = ord(_d)
for _i in range(1, 13):
    KEYBOARD_MAPPING[f"f{_i}"] = 0x6F + _i


# ─────────────────────────── 后端 ───────────────────────────

class Backend:
    name = "base"

    def rel(self, dx: int, dy: int) -> None: ...
    def button(self, mask: int, down: bool) -> None: ...
    def key(self, vk: int, down: bool) -> None: ...
    def wheel(self, n: int) -> None: ...
    def release_all(self) -> None: ...
    def close(self) -> None: ...


class HidBackend(Backend):
    """硬件 HID 桥 —— 游戏侧零软件痕迹，首选。"""
    name = "hid"

    def __init__(self, port: str | None = None):
        from .hid_bridge import HidBridge
        self.bridge = HidBridge.open(port)

    def rel(self, dx, dy): self.bridge.mouse_move(dx, dy)
    def button(self, mask, down): self.bridge.mouse_buttons(mask, down)
    def key(self, vk, down): self.bridge.key_vk(vk, down)
    def wheel(self, n): self.bridge.wheel(n)
    def release_all(self): self.bridge.release_all()
    def close(self): self.bridge.close()


class InterceptionBackend(Backend):
    """Interception 内核驱动（kernel-mode 虚拟键鼠）。仍需装驱动，但无 INJECTED 标志。"""
    name = "interception"

    def __init__(self):
        self._dll = ctypes.WinDLL("interception.dll")
        self._dll.interception_create_context.restype = ctypes.c_void_p
        self.ctx = self._dll.interception_create_context()
        if not self.ctx:
            raise RuntimeError("interception_create_context 失败（驱动没装？）")
        self._kb = ctypes.create_string_buffer(8)
        self._ms = ctypes.create_string_buffer(24)
        self._held: set[int] = set()

    def rel(self, dx, dy):
        # InterceptionMouseStroke: flags, rolling, x, y, information, padding*2
        stroke = (wintypes.USHORT * 6)(0, 0, int(dx) & 0xFFFF, int(dy) & 0xFFFF, 0, 0)
        self._dll.interception_send(self.ctx, 11, ctypes.byref(stroke), 1)
        del stroke

    def button(self, mask, down):
        flag = 0
        if mask & MOUSE_LEFT:
            flag = 0x0001 if down else 0x0002
        elif mask & MOUSE_RIGHT:
            flag = 0x0004 if down else 0x0008
        elif mask & MOUSE_MIDDLE:
            flag = 0x0010 if down else 0x0020
        if not flag:
            return
        stroke = (wintypes.USHORT * 6)(flag, 0, 0, 0, 0, 0)
        self._dll.interception_send(self.ctx, 11, ctypes.byref(stroke), 1)
        del stroke

    def key(self, vk, down):
        from .hid_bridge import VK_TO_HID as _m  # noqa: F401  (保持依赖一致)
        stroke = (wintypes.USHORT * 6)(0x0001 if down else 0x0002, 0, 0, vk, 0, 0)
        self._dll.interception_send(self.ctx, 1, ctypes.byref(stroke), 1)
        del stroke

    def wheel(self, n):
        stroke = (wintypes.USHORT * 6)(0x0800 if n > 0 else 0x07FF, 0, 0, 0, n * 120, 0)
        self._dll.interception_send(self.ctx, 11, ctypes.byref(stroke), 1)
        del stroke

    def release_all(self):
        self.button(MOUSE_LEFT, False)
        self.button(MOUSE_RIGHT, False)
        self.button(MOUSE_MIDDLE, False)

    def close(self):
        try:
            self._dll.interception_destroy_context(self.ctx)
        except Exception:
            pass


# ── SendInput 兜底（带 INJECTED 标志，反作弊可见，仅在没有硬件时用）──
_ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class SendInputBackend(Backend):
    name = "sendinput"

    def __init__(self):
        self._extra = _rng.randrange(0x10000000, 0x7FFFFFFF)

    def _emit(self, inp: _INPUT):
        _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def rel(self, dx, dy):
        i = _INPUT(type=0)
        i.u.mi = _MOUSEINPUT(dx, dy, 0, 0x0001, 0, self._extra)
        self._emit(i)

    def button(self, mask, down):
        flags = {MOUSE_LEFT: (0x0002, 0x0004),
                 MOUSE_RIGHT: (0x0008, 0x0010),
                 MOUSE_MIDDLE: (0x0020, 0x0040)}[mask]
        i = _INPUT(type=0)
        i.u.mi = _MOUSEINPUT(0, 0, 0, flags[0] if down else flags[1], 0, self._extra)
        self._emit(i)

    def key(self, vk, down):
        i = _INPUT(type=1)
        i.u.ki = _KEYBDINPUT(0, vk, 0x0008 | (0 if down else 0x0002), 0, self._extra)
        self._emit(i)

    def wheel(self, n):
        i = _INPUT(type=0)
        i.u.mi = _MOUSEINPUT(0, 0, (n * 120) & 0xFFFFFFFF, 0x0800, 0, self._extra)
        self._emit(i)

    def release_all(self):
        for vk in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C):
            self.key(vk, False)
        for m in _BTN.values():
            self.button(m, False)

    def close(self):
        pass


# ─────────────────────────── 轨迹 ───────────────────────────

def _bezier(p0, p1, p2, p3, t):
    mt = 1.0 - t
    a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def _smoothstep(t):
    return t * t * (3.0 - 2.0 * t)


def plan_path(x0: float, y0: float, x1: float, y1: float, rng=None) -> tuple[list, float]:
    """返回 (路径点列表, 总时长秒)。路径同时返回绝对坐标，调用方换算成相对增量。"""
    rng = rng or _rng
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 2.0:
        return [(x1, y1)], rng.uniform(0.03, 0.06)

    ux, uy = dx / dist, dy / dist
    nx, ny = -uy, ux
    bow1 = rng.gauss(0, max(2.0, dist * 0.11))
    bow2 = rng.gauss(0, max(2.0, dist * 0.09))
    c1 = (x0 + dx * 0.34 + nx * bow1, y0 + dy * 0.34 + ny * bow1)
    c2 = (x0 + dx * 0.71 + nx * bow2, y0 + dy * 0.71 + ny * bow2)

    # 末端过冲再回拉：长距离移动里人类几乎必然过冲
    over = 0.0
    if dist > 70 and rng.random() < 0.6:
        over = rng.uniform(2.0, min(10.0, dist * 0.05))
        c2 = (c2[0] + ux * over, c2[1] + uy * over)

    # Fitts: T = a + b*log2(D/W + 1)，W 取目标半宽估计
    duration = 0.092 + 0.094 * math.log2(dist / 8.0 + 1.0)
    duration *= rng.gauss(1.0, 0.14)
    duration = max(0.085, duration)

    n = max(8, int(dist / 5.5) + rng.randint(-2, 5))
    pts = []
    for i in range(1, n + 1):
        t = _smoothstep(i / n)
        px, py = _bezier((x0, y0), c1, c2, (x1, y1), t)
        # 每点再叠一点手抖（越接近终点越小）
        amp = 0.9 * (1.0 - 0.7 * i / n)
        px += rng.gauss(0, amp)
        py += rng.gauss(0, amp)
        pts.append((px, py))
    return pts, duration


# ─────────────────────────── 主类 ───────────────────────────

class HumanInput:
    def __init__(self, backend: Backend | None = None, seed: int | None = None):
        self.rng = random.Random(seed)
        self._lock = threading.RLock()
        self.backend = backend or pick_backend()

    # ---- 系统信息（读光标/分辨率不产生输入事件，安全）----
    def position(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def size(self) -> tuple[int, int]:
        return (_user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1))

    # ---- 移动 ----
    def moveTo(self, x: float, y: float, duration: float | None = None, **_) -> None:
        x += self.rng.gauss(0, 0.7)   # 落点散布
        y += self.rng.gauss(0, 0.7)
        with self._lock:
            cx, cy = self.position()
            pts, dur = plan_path(cx, cy, x, y, self.rng)
            if duration:
                dur = duration
            # 中段 1~2 次微停顿（人手修正）
            pauses = {}
            if len(pts) > 12 and self.rng.random() < 0.5:
                for _ in range(self.rng.randint(1, 2)):
                    pauses[self.rng.randrange(len(pts) // 4, len(pts) * 3 // 4)] = \
                        self.rng.uniform(0.006, 0.022)

            t0 = time.perf_counter()
            px, py = cx, cy
            for i, (tx, ty) in enumerate(pts):
                ddx, ddy = tx - px, ty - py
                if ddx or ddy:
                    self.backend.rel(int(round(ddx)), int(round(ddy)))
                px, py = tx, ty
                target = t0 + dur * (i + 1) / len(pts)
                if i in pauses:
                    target += pauses[i]
                now = time.perf_counter()
                if target > now:
                    time.sleep(target - now)

    def moveRel(self, dx: float, dy: float, **_) -> None:
        cx, cy = self.position()
        self.moveTo(cx + dx, cy + dy)

    # ---- 点击 ----
    def mouseDown(self, x=None, y=None, button: str = "left", **_) -> None:
        if x is not None and y is not None:
            self.moveTo(x, y)
        self.backend.button(_BTN[button], True)

    def mouseUp(self, x=None, y=None, button: str = "left", **_) -> None:
        self.backend.button(_BTN[button], False)

    def click(self, x=None, y=None, button: str = "left", clicks: int = 1,
              interval: float | None = None, **_) -> None:
        for i in range(clicks):
            self.mouseDown(x if i == 0 else None, y if i == 0 else None, button)
            dwell = min(0.17, max(0.042, self.rng.gauss(0.082, 0.024)))
            time.sleep(dwell)
            self.mouseUp(button=button)
            if i < clicks - 1:
                time.sleep(interval if interval else
                           min(0.22, max(0.07, self.rng.gauss(0.115, 0.03))))

    def doubleClick(self, x=None, y=None, button="left", **kw):
        self.click(x, y, button, clicks=2, **kw)

    def scroll(self, clicks: int, x=None, y=None, **_) -> None:
        if x is not None and y is not None:
            self.moveTo(x, y)
        self.backend.wheel(clicks)

    # ---- 键盘 ----
    def keyDown(self, key: str, **_) -> None:
        self.backend.key(self._vk(key), True)

    def keyUp(self, key: str, **_) -> None:
        self.backend.key(self._vk(key), False)

    def press(self, key: str, presses: int = 1, interval: float | None = None, **_) -> None:
        for i in range(presses):
            self.keyDown(key)
            dwell = min(0.19, max(0.045, self.rng.gauss(0.086, 0.026)))
            time.sleep(dwell)
            self.keyUp(key)
            if i < presses - 1:
                time.sleep(interval if interval else
                           min(0.25, max(0.06, self.rng.gauss(0.12, 0.035))))

    def hotkey(self, *keys: str) -> None:
        """组合键：错峰按下 12~40ms，逆序释放。"""
        for k in keys:
            self.backend.key(self._vk(k), True)
            time.sleep(self.rng.uniform(0.012, 0.041))
        for k in reversed(keys):
            self.backend.key(self._vk(k), False)
            time.sleep(self.rng.uniform(0.008, 0.026))

    def typewrite(self, text: str, interval: float | None = None, **_) -> None:
        for ch in text:
            k = ch.lower() if ch.lower() in KEYBOARD_MAPPING else None
            if k is None:
                continue
            need_shift = ch.isupper() or ch in '~!@#$%^&*()_+{}|:"<>?'
            if need_shift:
                self.hotkey("shift", k)
            else:
                self.press(k)
            time.sleep(interval if interval else
                       min(0.28, max(0.045, self.rng.gauss(0.095, 0.033))))

    def release_all(self) -> None:
        self.backend.release_all()

    def close(self) -> None:
        try:
            self.backend.release_all()
        finally:
            self.backend.close()

    @staticmethod
    def _vk(key: str) -> int:
        if isinstance(key, int):
            return key
        k = key.lower().strip()
        if k in KEYBOARD_MAPPING:
            return KEYBOARD_MAPPING[k]
        if len(k) == 1:
            return ord(k.upper())
        raise KeyError(f"未知按键: {key}")


def pick_backend(prefer: str | None = None) -> Backend:
    order = [prefer] if prefer else ["hid", "interception", "sendinput"]
    last = None
    for name in order:
        try:
            if name == "hid":
                return HidBackend()
            if name == "interception":
                return InterceptionBackend()
            return SendInputBackend()
        except Exception as e:
            last = e
    print(f"[human_input] 全部后端失败，降级 SendInput: {last}")
    return SendInputBackend()


# 模块级单例 + 与 pydirectinput 同名的便捷函数
_DEFAULT: HumanInput | None = None
_DEFAULT_LOCK = threading.Lock()


def get() -> HumanInput:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = HumanInput()
        return _DEFAULT


def moveTo(x, y, **kw): get().moveTo(x, y, **kw)
def moveRel(dx, dy, **kw): get().moveRel(dx, dy, **kw)
def click(x=None, y=None, **kw): get().click(x, y, **kw)
def doubleClick(x=None, y=None, **kw): get().doubleClick(x, y, **kw)
def mouseDown(x=None, y=None, **kw): get().mouseDown(x, y, **kw)
def mouseUp(x=None, y=None, **kw): get().mouseUp(x, y, **kw)
def scroll(clicks, x=None, y=None, **kw): get().scroll(clicks, x, y, **kw)
def press(key, **kw): get().press(key, **kw)
def keyDown(key, **kw): get().keyDown(key, **kw)
def keyUp(key, **kw): get().keyUp(key, **kw)
def hotkey(*keys): get().hotkey(*keys)
def typewrite(text, **kw): get().typewrite(text, **kw)
def position(): return get().position()
def size(): return get().size()


PAUSE = 0.0          # pydirectinput 兼容占位
FAILSAFE = False
KEYBOARD_MAPPING = KEYBOARD_MAPPING
