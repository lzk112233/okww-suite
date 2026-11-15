"""证据工具：当场证明 ok-ww 的 WM_ACTIVATE 是伪造的。

跑法（不需要开游戏）：
    python tools\\probe_activation.py

它建一个真正的 Win32 窗口，然后在窗口过程里对每一条 WM_ACTIVATE
反问一句 GetForegroundWindow() 是不是自己。然后分三个阶段：

    A 阶段  用 ok-ww 的方式：PostMessage(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
    B 阶段  用真的：force_foreground(hwnd)
    C 阶段  窗口真的在前台时，再 PostMessage 同样的消息（对照组）

A 阶段的 WM_ACTIVATE 全都会被判定为伪造（GetForegroundWindow 指向
别的窗口），B 阶段不会。这就是 ACE 只需要三行代码就能拿到的判据。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from okww_fg import foreground as fg  # noqa: E402

user32 = fg.user32
kernel32 = fg.kernel32

WS_OVERLAPPEDWINDOW = 0x00CF0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_ACTIVATE = 0x0006
WA_ACTIVE = 1
WA_INACTIVE = 0
PM_REMOVE = 0x0001

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT,
                                wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.UpdateWindow.restype = wintypes.BOOL
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

# 关键：这个引用必须活在模块级。WNDPROC 对象是 Python 侧的 thunk，
# 一旦被 GC，系统里保存的函数指针就悬空了 —— 第一条消息进来就崩。
_WNDPROC_REF: list = []

records: list[dict] = []
stats = {"activations": 0, "fake": 0, "real": 0, "phase": "setup"}
_done = threading.Event()


def wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_ACTIVATE:
        stats["activations"] += 1
        cur = fg.foreground_hwnd()
        is_real = (cur == int(hwnd))
        stats["real" if is_real else "fake"] += 1
        records.append({
            "phase": stats["phase"],
            "wParam": int(wparam) & 0xFFFF,
            "foreground": cur,
            "foreground_title": fg.window_title(cur) if cur else "",
            "real": is_real,
        })
        return 0
    if msg == WM_CLOSE:
        user32.DestroyWindow(hwnd)
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def create_probe_window() -> int:
    hinst = kernel32.GetModuleHandleW(None)
    proc = WNDPROC(wndproc)
    _WNDPROC_REF.append(proc)
    cls = WNDCLASSW()
    cls.style = 0
    cls.lpfnWndProc = proc
    cls.cbClsExtra = 0
    cls.cbWndExtra = 0
    cls.hInstance = hinst
    cls.hIcon = None
    cls.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
    cls.hbrBackground = None
    cls.lpszMenuName = None
    cls.lpszClassName = "OkwwFgProbeWnd"
    atom = user32.RegisterClassW(ctypes.byref(cls))
    if not atom:
        err = ctypes.get_last_error()
        if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
            raise OSError(f"RegisterClassW failed err={err}")
    hwnd = user32.CreateWindowExW(
        0, "OkwwFgProbeWnd", "okww-fg WM_ACTIVATE probe", WS_OVERLAPPEDWINDOW,
        220, 220, 520, 260, None, None, hinst, None,
    )
    if not hwnd:
        raise OSError(f"CreateWindowExW failed err={ctypes.get_last_error()}")
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.UpdateWindow(hwnd)
    return int(hwnd)


def driver(hwnd: int) -> None:
    try:
        time.sleep(0.50)

        # ── A 阶段：ok-ww 的做法 ────────────────────────────
        stats["phase"] = "A_postmessage"
        for _ in range(20):
            # post_message.py:144 原样复刻
            user32.PostMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
            time.sleep(0.05)
        time.sleep(0.35)

        # ── B 阶段：真前台 ─────────────────────────────────
        stats["phase"] = "B_real"
        info = fg.force_foreground(hwnd)
        print(f"  force_foreground -> ok={info.get('ok')} "
              f"after={info.get('after')} elapsed={info.get('elapsed')}", flush=True)
        time.sleep(0.45)

        # ── C 阶段：此刻窗口真的是前台，再发同样的消息 ──────
        stats["phase"] = "C_postmessage_when_fg"
        for _ in range(5):
            user32.PostMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
            time.sleep(0.05)
        time.sleep(0.25)
    except Exception as e:  # noqa: BLE001
        print(f"driver error: {type(e).__name__}: {e}", flush=True)
    finally:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        _done.set()


def main() -> int:
    hwnd = create_probe_window()
    print(f"probe hwnd = {hwnd}  title={fg.window_title(hwnd)!r}", flush=True)
    fg0 = fg.foreground_hwnd()
    print(f"foreground at start = {fg0} ({fg.window_title(fg0)!r})", flush=True)
    print(flush=True)

    th = threading.Thread(target=driver, args=(hwnd,), daemon=True)
    th.start()

    msg = MSG()
    deadline = time.time() + 15
    while time.time() < deadline and not _done.is_set():
        handled = False
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            handled = True
            if msg.message == WM_QUIT:
                _done.set()
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if not handled:
            time.sleep(0.003)

    print()
    print("=" * 64)
    print("按阶段统计")
    print("=" * 64)
    phases: dict[str, dict] = {}
    for r in records:
        b = phases.setdefault(r["phase"], {"n": 0, "fake": 0, "real": 0})
        b["n"] += 1
        b["fake" if not r["real"] else "real"] += 1
    for name, b in phases.items():
        n = b["n"]
        print(f"  {name:24s} 共 {n:3d} 条 | 被判伪造 {b['fake']:3d} "
              f"({b['fake'] * 100 // max(n, 1):3d}%) | 真的 {b['real']:3d}")

    print()
    print(f"总计 WM_ACTIVATE {stats['activations']} 条，"
          f"其中 GetForegroundWindow() != 自己 的有 {stats['fake']} 条")
    print()
    print("A 阶段那些就是 ACE 眼里的证据：消息声称「我被激活了」，")
    print("而内核的 gpqForeground 指着别的窗口。两句话对不上。")
    print()
    print("A 阶段明细（前 6 条）：")
    shown = 0
    for r in records:
        if r["phase"] != "A_postmessage":
            continue
        print(f"  WM_ACTIVATE 真实前台={r['foreground']} "
              f"({r['foreground_title']!r})  伪造={not r['real']}")
        shown += 1
        if shown >= 6:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
