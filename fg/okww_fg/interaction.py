"""把 ok-ww 的 PostMessage 后台输入换成「真前台 + 真注入」。

不打 ok-ww 的补丁文件，只给 PostMessageInteraction 打方法补丁：
post() 是它所有消息的唯一出口（post_message.py:109-115），所以只要
换掉这一个方法，send_key / click / move / scroll / swipe 全都自动改道，
ok-ww 的业务代码一行不动。

消息映射
    WM_ACTIVATE / WM_NCACTIVATE   丢掉 -> 真的 force_foreground（带节流）
    WM_MOUSEMOVE                  丢掉 -> 真实鼠标移动（贝塞尔轨迹）
    WM_*BUTTONDOWN / WM_*BUTTONUP 丢掉 -> 真实按键按下/抬起
    WM_MOUSEWHEEL                 丢掉 -> 真实滚轮
    WM_KEYDOWN / WM_KEYUP         丢掉 -> 真实键盘（scancode）
    WM_CHAR                       丢掉 -> 真实 unicode 字符
    其余（自定义消息等）          原样 PostMessage 兜底

为什么 WM_ACTIVATE 必须真做而不是假发
    GetForegroundWindow() 读的是内核 gpqForeground，用户态改不了。
    游戏收到一条 PostMessage 来的 WM_ACTIVATE 后只要自己反问一句
    GetForegroundWindow() != hwnd 就知道是伪造的，零成本、零误判。
    ok-ww 每个输入动作前发 1~2 条，频率还高得不正常，等于自己举手。
"""

from __future__ import annotations

import ctypes
import importlib
import os
import sys
import time
from ctypes import wintypes

from . import foreground as fg

# ── 消息常量（不依赖 win32con，减少导入失败面）──────────────
WM_ACTIVATE = 0x0006
WM_NCACTIVATE = 0x0086
WM_SETFOCUS = 0x0007
WM_KILLFOCUS = 0x0008
WM_CHAR = 0x0102
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C

BUTTON_DOWN = {
    WM_LBUTTONDOWN: 0x01,
    WM_RBUTTONDOWN: 0x02,
    WM_MBUTTONDOWN: 0x04,
}
BUTTON_UP = {
    WM_LBUTTONUP: 0x01,
    WM_RBUTTONUP: 0x02,
    WM_MBUTTONUP: 0x04,
}
KEY_MSG = {WM_KEYDOWN: True, WM_KEYUP: False, WM_SYSKEYDOWN: True, WM_SYSKEYUP: False}
# 这些消息被整体吞掉，绝不下发
DROP = {WM_ACTIVATE, WM_NCACTIVATE, WM_SETFOCUS, WM_KILLFOCUS}


def _loword(value: int) -> int:
    return ctypes.c_short(value & 0xFFFF).value


def _hiword(value: int) -> int:
    return ctypes.c_short((value >> 16) & 0xFFFF).value


# ── 输入出口 ────────────────────────────────────────────────
# ── SendInput 的 unicode 通道 ───────────────────────────────
# human_input 的 SendInputBackend 只有 key(vk)/button/wheel，没有 unicode；
# VkKeyScanW 又只能处理当前键盘布局里存在的字符（中文、符号一律 -1）。
# 所以这里自己封一层 KEYEVENTF_UNICODE，ok-ww 的 input_text 才真的能打出字。
_INPUT_KEYBOARD = 1
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP = 0x0002

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


fg.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
fg.user32.SendInput.restype = wintypes.UINT


def send_unicode(code: int) -> bool:
    """把一个 unicode 码点打成真正的按键（KEYEVENTF_UNICODE，绕过键盘布局）。"""
    code = int(code)
    scans = [code]
    if code > 0xFFFF:                       # 补充平面：拆代理对
        v = code - 0x10000
        scans = [0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF)]
    total = len(scans) * 2
    ins = (_INPUT * total)()
    i = 0
    for sc in scans:                        # 先全部按下
        ins[i].type = _INPUT_KEYBOARD
        ins[i].ki = _KEYBDINPUT(0, sc, _KEYEVENTF_UNICODE, 0, 0)
        i += 1
    for sc in scans:                        # 再全部抬起
        ins[i].type = _INPUT_KEYBOARD
        ins[i].ki = _KEYBDINPUT(0, sc, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, 0, 0)
        i += 1
    sent = fg.user32.SendInput(total, ins, ctypes.sizeof(_INPUT))
    return int(sent) == total


class InputSink:
    """真实输入出口。

    优先复用 okww-stealth 的 human_input（贝塞尔轨迹、Fitts 时长、
    dwell 抖动、可切 hid / sendinput 后端）。找不到就退回裸 SendInput。
    """

    def __init__(self, backend: str | None = None, stealth_dir: str | None = None):
        self.backend_name = backend or "sendinput"
        self._hi = None
        self._backend = None
        self._last_move = 0.0
        self._rng = None
        self._load(stealth_dir)

    def _load(self, stealth_dir: str | None) -> None:
        candidates = []
        if stealth_dir:
            candidates.append(stealth_dir)
        env = os.environ.get("OKWW_STEALTH_DIR")
        if env:
            candidates.append(env)
        candidates.append(r"D:\ai\dfy\okww-stealth")
        candidates.append(os.path.expanduser(r"~\okww-stealth"))
        for path in candidates:
            if not path or not os.path.isdir(path):
                continue
            if path not in sys.path:
                sys.path.insert(0, path)
            try:
                mod = importlib.import_module("okww_shim.human_input")
                backend = mod.pick_backend(self.backend_name)
                self._hi = mod.HumanInput(backend=backend)
                self._backend = backend
                self.backend_name = getattr(backend, "name", self.backend_name)
                return
            except Exception:
                continue
        self._backend = _RawSendInput()

    # ── 查询 ──
    def position(self) -> tuple[int, int]:
        return fg.cursor_pos()

    # ── 动作 ──
    def move_to(self, x: int, y: int) -> None:
        x, y = int(x), int(y)
        now = time.perf_counter()
        cx, cy = self.position()
        dx, dy = x - cx, y - cy
        if not dx and not dy:
            return
        dist = (dx * dx + dy * dy) ** 0.5
        # 连续小步（swipe 里 100 次/秒那种）直接分片走，不重新规划轨迹，
        # 否则每次 move 都要 0.1~0.3s 的贝塞尔，滑一次要十几秒。
        fast = (now - self._last_move) < 0.060 or dist <= 6.0
        self._last_move = now
        if fast:
            self._rel_path(dx, dy)
            return
        if self._hi is not None:
            try:
                self._hi.moveTo(x, y)
                return
            except Exception:
                pass
        self._rel_path(dx, dy)
        fg.user32.SetCursorPos(x, y)

    def _rel_path(self, dx: int, dy: int) -> None:
        steps = max(1, int(max(abs(dx), abs(dy)) / 96) + 1)
        for i in range(steps):
            sx = int(round(dx / steps))
            sy = int(round(dy / steps))
            if i == steps - 1:
                sx = dx - int(round(dx / steps)) * (steps - 1)
                sy = dy - int(round(dy / steps)) * (steps - 1)
            self.rel(sx, sy)

    def rel(self, dx: int, dy: int) -> None:
        while dx or dy:
            sx = max(-127, min(127, dx))
            sy = max(-127, min(127, dy))
            try:
                self._backend.rel(sx, sy)
            except Exception:
                fg.user32.SetCursorPos(*fg.cursor_pos() and
                                       (fg.cursor_pos()[0] + sx, fg.cursor_pos()[1] + sy))
            dx -= sx
            dy -= sy

    def button(self, mask: int, down: bool) -> None:
        try:
            self._backend.button(mask, down)
        except Exception:
            pass

    def key(self, vk: int, down: bool) -> None:
        try:
            self._backend.key(int(vk), bool(down))
        except Exception:
            pass

    def wheel(self, clicks: int) -> None:
        try:
            self._backend.wheel(int(clicks))
        except Exception:
            pass

    def char(self, code: int) -> None:
        """输入一个 unicode 字符（对应 WM_CHAR）。"""
        code = int(code)
        if code <= 0 or code > 0x10FFFF:
            return
        # 1) 先自己用 KEYEVENTF_UNICODE 打 —— 这条路不挑键盘布局
        try:
            if send_unicode(code):
                return
        except Exception:
            pass
        # 2) 再试后端自己的 unicode（HID 桥那边有）
        fn = getattr(self._backend, "unicode", None)
        if callable(fn):
            try:
                fn(code)
                return
            except Exception:
                pass
        # 3) 最后退化到 vk（只对 ASCII 有效）
        try:
            vk = fg.user32.VkKeyScanW(chr(code))
        except Exception:
            return
        vk = int(vk or 0)
        if vk != -1:
            self.key(vk & 0xFF, True)
            time.sleep(0.012)
            self.key(vk & 0xFF, False)

    def tap_alt(self) -> None:
        try:
            self.key(0x12, True)
            time.sleep(0.014)
            self.key(0x12, False)
        except Exception:
            fg._tap_alt_local()

    def close(self) -> None:
        try:
            self._backend.release_all()
        except Exception:
            pass
        try:
            self._backend.close()
        except Exception:
            pass


class _RawSendInput:
    """人类化层不可用时的最小实现：直接 SendInput。"""

    name = "raw_sendinput"

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)

    def rel(self, dx, dy):
        self.user32.mouse_event(0x0001, int(dx), int(dy), 0, 0)

    def button(self, mask, down):
        table = {0x01: (0x0002, 0x0004), 0x02: (0x0008, 0x0010), 0x04: (0x0020, 0x0040)}
        flags = table.get(int(mask))
        if not flags:
            return
        self.user32.mouse_event(flags[0] if down else flags[1], 0, 0, 0, 0)

    def key(self, vk, down):
        self.user32.keybd_event(int(vk), 0, 0 if down else 0x0002, None)

    def wheel(self, clicks):
        self.user32.mouse_event(0x0800, 0, 0, int(clicks) * 120, 0)

    def release_all(self):
        pass

    def close(self):
        pass


# ── 每实例状态 ──────────────────────────────────────────────
class _FgState:
    def __init__(self, sink: InputSink):
        self.sink = sink
        self.last_rise = 0.0
        self.rises = 0
        self.failures = 0
        self.last: dict = {}
        self.dropped = 0
        self.converted = 0
        self.min_raise_gap = 0.60      # 两次真前台切换的最小间隔
        self.want_foreground = True

    def ensure_foreground(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        if fg.foreground_hwnd() == hwnd:
            return True
        if not self.want_foreground:
            self.dropped += 1
            return False
        now = time.perf_counter()
        if now - self.last_rise < self.min_raise_gap:
            self.dropped += 1
            return False
        self.last_rise = now
        self.rises += 1
        self.last = fg.force_foreground(hwnd, tap_alt=self.sink.tap_alt)
        if not self.last.get("ok"):
            self.failures += 1
        return bool(self.last.get("ok"))


def _state(interaction) -> _FgState:
    st = getattr(interaction, "_fg_state", None)
    if st is None:
        sink = getattr(interaction, "_fg_sink", None)
        if sink is None:
            sink = InputSink(os.environ.get("OKWW_FG_BACKEND"),
                             os.environ.get("OKWW_STEALTH_DIR"))
            interaction._fg_sink = sink
        st = _FgState(sink)
        interaction._fg_state = st
    return st


# ── 补丁主体 ────────────────────────────────────────────────
def _fg_post(self, message, wParam=0, lParam=0, hwnd=None):
    if hwnd is None:
        hwnd = self.hwnd
    hwnd = int(hwnd or 0)
    if not hwnd:
        return

    st = _state(self)

    if message in DROP:
        st.ensure_foreground(hwnd)
        return

    if message == WM_MOUSEMOVE:
        # 移动不抢焦点（抢了反而抽风），但只在目标真的在前台时才移动，
        # 否则鼠标会飘到别人的窗口上去触发 hover。
        cx, cy = _loword(lParam), _hiword(lParam)
        sx, sy = fg.client_to_screen(hwnd, cx, cy)
        if fg.foreground_hwnd() != hwnd:
            st.dropped += 1
            return
        st.sink.move_to(sx, sy)
        st.converted += 1
        return

    if message in BUTTON_DOWN or message in BUTTON_UP:
        if not st.ensure_foreground(hwnd):
            return
        mask = BUTTON_DOWN.get(message) or BUTTON_UP.get(message)
        st.sink.button(mask, message in BUTTON_DOWN)
        st.converted += 1
        return

    if message in (WM_XBUTTONDOWN, WM_XBUTTONUP):
        if not st.ensure_foreground(hwnd):
            return
        # wParam 高字是 XBUTTON 编号（1 或 2）
        idx = (int(wParam) >> 16) & 0xFFFF
        mask = 0x08 if idx == 1 else 0x10
        st.sink.button(mask, message == WM_XBUTTONDOWN)
        st.converted += 1
        return

    if message == WM_MOUSEWHEEL:
        if not st.ensure_foreground(hwnd):
            return
        delta = _hiword(wParam)
        st.sink.wheel(int(round(delta / 120.0)))
        st.converted += 1
        return

    if message in KEY_MSG:
        # 焦点没拿到就丢：真实 SendInput 是全局投递，硬发会把键打到
        # 别的窗口里去（聊天框、浏览器地址栏），这比不操作更危险。
        if not st.ensure_foreground(hwnd):
            return
        st.sink.key(int(wParam) & 0xFF, KEY_MSG[message])
        st.converted += 1
        return

    if message == WM_CHAR:
        if not st.ensure_foreground(hwnd):
            return
        st.sink.char(int(wParam))
        st.converted += 1
        return

    # 兜底：真正需要投递的自定义消息照旧
    try:
        import win32gui
        win32gui.PostMessage(hwnd, message, wParam, lParam)
    except Exception:
        pass


def _fg_activate(self, hwnd=None):
    target = int(hwnd or self.hwnd or 0)
    _state(self).ensure_foreground(target)


def _fg_deactivate(self, hwnd=None):
    # 不再发 WM_ACTIVATE(WA_INACTIVE)：真前台由系统自己管。
    return


def _fg_try_activate(self):
    base = getattr(self.hwnd_window, "hwnd", 0)
    cur = self.hwnd
    st = _state(self)
    if base:
        st.ensure_foreground(int(base))
    if cur and int(cur) != int(base or 0):
        st.ensure_foreground(int(cur))


def _fg_send_key_down(self, key, activate=True):
    if activate:
        _fg_try_activate(self)
    import win32api
    import win32con
    from ok.device.interaction_methods.keys import (
        is_mouse_button, get_mouse_button, XBUTTON1, MK_XBUTTON1, MK_XBUTTON2,
    )
    st = _state(self)
    if is_mouse_button(key):
        btn = get_mouse_button(key)
        mask = 0x08 if btn == XBUTTON1 else 0x10
        st.sink.button(mask, True)
        return
    vk = self.get_key_by_str(key)
    st.sink.key(int(vk) & 0xFF, True)


def _fg_send_key_up(self, key):
    from ok.device.interaction_methods.keys import (
        is_mouse_button, get_mouse_button, XBUTTON1,
    )
    st = _state(self)
    if is_mouse_button(key):
        btn = get_mouse_button(key)
        mask = 0x08 if btn == XBUTTON1 else 0x10
        st.sink.button(mask, False)
        return
    vk = self.get_key_by_str(key)
    st.sink.key(int(vk) & 0xFF, False)


def install(sink: str | None = None, stealth_dir: str | None = None,
            foreground: bool = True, quiet: bool = False) -> dict:
    """给 PostMessageInteraction 打补丁。

    sink        sendinput / interception / hid / net_hid，或 None 自动
    stealth_dir okww-stealth 目录（提供 human_input 与 hid 后端）
    foreground  False 时只换输入通道、完全不切前台（纯测试用）
    """
    if sink:
        os.environ["OKWW_FG_BACKEND"] = sink
    if stealth_dir:
        os.environ["OKWW_STEALTH_DIR"] = stealth_dir

    try:
        mod = importlib.import_module("ok.device.interaction_methods.post_message")
    except Exception as exc:
        return {"ok": False, "error": f"import failed: {exc}"}

    cls = mod.PostMessageInteraction
    if getattr(cls, "_okww_fg_patched", False):
        return {"ok": True, "already": True}

    cls._okww_fg_post = cls.post
    cls.post = _fg_post
    cls.activate = _fg_activate
    cls.deactivate = _fg_deactivate
    cls.try_activate = _fg_try_activate
    cls.send_key_down = _fg_send_key_down
    cls.send_key_up = _fg_send_key_up
    cls._okww_fg_patched = True

    if not foreground:
        orig_state = _state

        def _no_fg(interaction):
            st = orig_state(interaction)
            st.want_foreground = False
            return st

        globals()["_state"] = _no_fg

    probe = InputSink(os.environ.get("OKWW_FG_BACKEND"), os.environ.get("OKWW_STEALTH_DIR"))
    result = {
        "ok": True,
        "patched": ["post", "activate", "deactivate", "try_activate",
                    "send_key_down", "send_key_up"],
        "sink": probe.backend_name,
        "foreground": foreground,
        "humanized": probe._hi is not None,
        "foreground_now": fg.foreground_hwnd(),
    }
    probe.close()
    if not quiet:
        print(f"[okww-fg] patched PostMessageInteraction -> foreground+sink={result['sink']}")
    return result


def status() -> dict:
    return {
        "foreground": fg.audit(fg.foreground_hwnd()),
        "lock_timeout": fg.get_lock_timeout(),
    }
