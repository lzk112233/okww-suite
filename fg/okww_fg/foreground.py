"""真前台激活。

ok-ww 的 PostMessageInteraction 在每一次输入动作之前都会往游戏窗口
PostMessage 一条 WM_ACTIVATE(WA_ACTIVE)，用来「假装」游戏处于前台：

    post_message.py:143  def activate(self, hwnd=None):
    post_message.py:144      self.post(win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0, hwnd=hwnd)
    post_message.py:149  def try_activate(self):        <- 每个 send_key_down / update_mouse_pos 都调
    post_message.py:153      self.activate(base_hwnd)
    post_message.py:155          self.activate(current_hwnd)

真实的窗口激活从来不走 PostMessage。win32k!xxxActivateWindow 是用
SendMessage 语义同步投递的，而且只在这个窗口真的被激活时才有。所以游戏
侧只要在处理 WM_ACTIVATE 时反问一句：

    case WM_ACTIVATE:
        if (GetForegroundWindow() != hwnd) -> 伪造激活

就是零成本、100% 准确的判据。GetForegroundWindow 读的是内核里的
gpqForeground，用户态伪造不了。

这个模块只做一件事：把窗口真的切到前台，然后验证 GetForegroundWindow()
确实等于它。验证不通过就如实返回 False，不再用假消息哄自己。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 窗口状态
SW_RESTORE = 9
SW_SHOW = 5
SW_MAXIMIZE = 3

# 键盘
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002

# SetWindowPos
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

# SystemParametersInfo
SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
SPIF_SENDCHANGE = 0x0002

# ── 原型声明 ──────────────────────────────────────────────
# 64 位下句柄一律 c_void_p；不声明 restype 会被截成 int32，
# 高地址句柄静默失败（这个坑在 capture.py 的 GDI 上已经踩过一次）。
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetForegroundWindow.argtypes = []
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ScreenToClient.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.POINTER(wintypes.ULONG)]
user32.keybd_event.restype = None


def foreground_hwnd() -> int:
    """当前前台窗口。读的是 gpqForeground，伪造不了。"""
    return int(user32.GetForegroundWindow() or 0)


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(int(hwnd), buf, 512)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(int(hwnd), buf, 256)
    return buf.value


def screen_to_client(hwnd: int, x: int, y: int) -> tuple[int, int]:
    pt = wintypes.POINT(int(x), int(y))
    if not user32.ScreenToClient(int(hwnd), ctypes.byref(pt)):
        return (int(x), int(y))
    return (pt.x, pt.y)


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    pt = wintypes.POINT(int(x), int(y))
    if not user32.ClientToScreen(int(hwnd), ctypes.byref(pt)):
        return (int(x), int(y))
    return (pt.x, pt.y)


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return (0, 0)
    return (pt.x, pt.y)


def _tap_alt_local() -> None:
    """按一下再松开 ALT。

    Windows 的前台锁规则里有一条：刚收到过 ALT 键的进程有权
    SetForegroundWindow。这就是各种「一键切前台」工具用的老技巧。
    走 keybd_event 会带 LLMHF_INJECTED 标志，但激活操作频率极低
    （只在真的需要切的时候，不是每次按键），比每帧一条 WM_ACTIVATE
    干净得多。装了 HID 后端的话可以换成硬件按 ALT。
    """
    user32.keybd_event(VK_MENU, 0, 0, None)
    time.sleep(0.012)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, None)


def get_lock_timeout() -> int | None:
    val = wintypes.DWORD()
    if user32.SystemParametersInfoW(SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(val), 0):
        return int(val.value)
    return None


def set_lock_timeout(value: int) -> bool:
    return bool(user32.SystemParametersInfoW(
        SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(int(value)), SPIF_SENDCHANGE))


def force_foreground(hwnd: int, timeout: float = 0.40, tap_alt=None,
                     topmost: bool = False, unlock: bool = True) -> dict:
    """把 hwnd 真的切到前台，并验证。

    unlock=True 时会临时把 SPI_SETFOREGROUNDLOCKTIMEOUT 设成 0 ——
    本机实测这个值现在是 0x7FFFFFFF（无限），也就是说任何非前台进程
    调 SetForegroundWindow 都会被前台锁挡回去。这大概正是 ok-ww 当初
    改用 PostMessage 假装激活的原因：它真的切不过去。
    退出时恢复原值，不留系统副作用。
    """
    prev = None
    if unlock:
        prev = get_lock_timeout()
        if prev:
            set_lock_timeout(0)
    try:
        return _force_foreground(hwnd, timeout, tap_alt, topmost)
    finally:
        if prev:
            set_lock_timeout(prev)


def _force_foreground(hwnd: int, timeout: float = 0.40, tap_alt=None,
                      topmost: bool = False) -> dict:
    """真实的切换逻辑（由 force_foreground 包住前台锁处理）。

    返回 {"ok": bool, "hwnd", "before", "after", "elapsed", "steps": [...]}
    ok=False 表示真的切不过去 —— 这时候任何「假装在前台」都是自欺。
    """
    hwnd = int(hwnd)
    info: dict = {"hwnd": hwnd, "steps": []}
    if not hwnd or not user32.IsWindow(hwnd):
        info.update(ok=False, error="invalid hwnd")
        return info

    before = foreground_hwnd()
    info["before"] = before
    info["title"] = window_title(hwnd)
    if before == hwnd:
        info.update(ok=True, already=True, after=hwnd, elapsed=0.0)
        return info

    if not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_SHOW)
        info["steps"].append("show")
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        info["steps"].append("restore")

    if topmost:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        info["steps"].append("topmost")

    # 1) 前台锁解锁：先按 ALT
    try:
        if tap_alt is None:
            _tap_alt_local()
            info["steps"].append("alt(local)")
        else:
            tap_alt()
            info["steps"].append("alt(backend)")
    except Exception as exc:
        info["steps"].append(f"alt failed: {exc}")

    # 2) 挂到当前前台线程的输入队列，绕过 SetForegroundWindow 的进程归属检查
    fg_tid = 0
    if before:
        fg_tid = int(user32.GetWindowThreadProcessId(before, None))
    my_tid = int(kernel32.GetCurrentThreadId())
    attached = False
    if fg_tid and fg_tid != my_tid:
        attached = bool(user32.AttachThreadInput(fg_tid, my_tid, True))
        info["steps"].append(f"attach {fg_tid}->{my_tid} = {attached}")
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(fg_tid, my_tid, False)

    # 3) 轮询验证，不信返回值，只信 GetForegroundWindow
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if foreground_hwnd() == hwnd:
            info.update(ok=True, after=hwnd, elapsed=round(time.perf_counter() - t0, 4))
            return info
        time.sleep(0.005)

    after = foreground_hwnd()
    info.update(ok=False, after=after, elapsed=round(time.perf_counter() - t0, 4),
                after_title=window_title(after) if after else "")
    if topmost:
        # 切不过去就把置顶撤回去，别把桌面搞乱
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        info["steps"].append("topmost-revert")
    return info


def audit(hwnd: int) -> dict:
    """体检：这个窗口现在是不是真的前台，以及它为什么可能不是。"""
    hwnd = int(hwnd)
    fg = foreground_hwnd()
    return {
        "hwnd": hwnd,
        "title": window_title(hwnd),
        "class": window_class(hwnd),
        "is_window": bool(user32.IsWindow(hwnd)),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "iconic": bool(user32.IsIconic(hwnd)),
        "foreground": fg,
        "is_foreground": fg == hwnd,
        "foreground_title": window_title(fg) if fg else "",
        "lock_timeout": get_lock_timeout(),
        "cursor": cursor_pos(),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        import json
        print(json.dumps(audit(int(sys.argv[1])), ensure_ascii=False, indent=2))
    else:
        fg = foreground_hwnd()
        print(f"foreground = {fg}  title={window_title(fg)!r}  class={window_class(fg)!r}")
        print(f"lock_timeout = {get_lock_timeout()}")
        print(f"cursor = {cursor_pos()}")
