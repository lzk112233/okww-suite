"""端到端验证：抢到真焦点 + 真 SendInput 能不能把字符打进目标窗口。

跑法：
    python tools\\e2e_focus.py

它会：
  1. 建一个主窗口 + 一个 EDIT 子控件（当作"游戏窗口"），并跑真正的消息泵
  2. 用 SW_SHOWNOACTIVATE 显示，确认它**不是**前台
  3. 调 _FgState.ensure_foreground() 真抢前台
  4. 抢到之后用 InputSink 发三个字符
  5. 读 EDIT 的文字，看字是不是真的落进去了

第 4 步只在第 3 步成功时才做 —— 抢焦点失败就不发键，
免得把字符打到别的窗口里去。

注意：窗口必须有自己的 GetMessage/DispatchMessage 循环，
否则 SendInput 产生的键盘消息只会躺在目标线程的队列里没人处理，
EDIT 永远收不到字符（这是第一版卡住的地方）。
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from okww_fg import foreground as fg  # noqa: E402
from okww_fg.interaction import InputSink, _FgState  # noqa: E402

user32 = fg.user32
kernel32 = fg.kernel32

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
WS_TABSTOP = 0x00010000
ES_MULTILINE = 0x0004
ES_AUTOVSCROLL = 0x0040

WM_CLOSE = 0x0010
WM_APP_FOCUS_EDIT = 0x8001

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
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.UpdateWindow.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.GetFocus.argtypes = []
user32.GetFocus.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

_WNDPROC_REF: list = []
_EDIT = [0]


def wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_CLOSE:
        user32.DestroyWindow(hwnd)
        # DestroyWindow 不会产生 WM_QUIT，必须显式 PostQuitMessage，
        # 而且只能在窗口线程里调，否则消息泵永远退不出来。
        user32.PostQuitMessage(0)
        return 0
    if msg == WM_APP_FOCUS_EDIT:
        # 必须在窗口线程里 SetFocus，跨线程调不生效。
        user32.SetFocus(_EDIT[0])
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def build_target():
    hinst = kernel32.GetModuleHandleW(None)
    proc = WNDPROC(wndproc)
    _WNDPROC_REF.append(proc)          # 必须模块级持有，否则 thunk 被 GC
    cls = WNDCLASSW()
    cls.lpfnWndProc = proc
    cls.hInstance = hinst
    cls.lpszClassName = "OkwwFgE2EWnd"
    user32.RegisterClassW(ctypes.byref(cls))

    main = user32.CreateWindowExW(
        0, "OkwwFgE2EWnd", "okww-fg e2e target", WS_OVERLAPPEDWINDOW,
        260, 240, 640, 300, None, None, hinst, None,
    )
    if not main:
        raise OSError(f"main window failed err={ctypes.get_last_error()}")
    edit = user32.CreateWindowExW(
        0, "EDIT", "",
        WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP | ES_MULTILINE | ES_AUTOVSCROLL,
        10, 10, 600, 240, main, None, hinst, None,
    )
    if not edit:
        raise OSError(f"edit control failed err={ctypes.get_last_error()}")
    _EDIT[0] = int(edit)
    user32.ShowWindow(main, 4)          # SW_SHOWNOACTIVATE：显示但不抢焦点
    user32.UpdateWindow(main)
    return int(main), int(edit)


def read_edit(edit: int) -> str:
    buf = ctypes.create_unicode_buffer(4096)
    n = user32.GetWindowTextW(edit, buf, 4096)
    return buf.value[:n]


def worker(main_hwnd: int, edit_hwnd: int, out: queue.Queue) -> None:
    try:
        time.sleep(0.35)
        before = fg.foreground_hwnd()
        out.put(("before", before, fg.window_title(before)))

        sink = InputSink(None, None)
        st = _FgState(sink)
        out.put(("sink", sink.backend_name))

        t0 = time.perf_counter()
        ok = st.ensure_foreground(main_hwnd)
        dt = (time.perf_counter() - t0) * 1000
        after = fg.foreground_hwnd()
        out.put(("rise", ok, dt, after, fg.window_title(after)))

        if not ok or after != main_hwnd:
            out.put(("skip", "没抢到前台，不往下发键"))
            return

        user32.PostMessageW(main_hwnd, WM_APP_FOCUS_EDIT, 0, 0)
        time.sleep(0.15)
        focus = user32.GetFocus()
        out.put(("focus", int(focus or 0), focus == edit_hwnd))

        for ch in "ABC":
            sink.char(ord(ch))
            time.sleep(0.09)
        time.sleep(0.35)

        got = read_edit(edit_hwnd)
        out.put(("text", got))
    except Exception as exc:                                  # noqa: BLE001
        out.put(("error", repr(exc)))
    finally:
        # PostQuitMessage 是线程局部的，从 worker 调只会塞进 worker 自己的
        # 队列，主线程的 GetMessage 照样死等。所以改成给主窗口发 WM_CLOSE，
        # 让窗口线程自己退出消息泵。
        out.put(("done",))
        user32.PostMessageW(main_hwnd, WM_CLOSE, 0, 0)


def main() -> int:
    main_hwnd, edit_hwnd = build_target()
    print(f"target main={main_hwnd}  edit={edit_hwnd}", flush=True)

    out: queue.Queue = queue.Queue()
    th = threading.Thread(target=worker, args=(main_hwnd, edit_hwnd, out),
                          daemon=True)
    th.start()

    # 真消息泵：没有它，SendInput 打进来的键盘消息没人处理。
    # 用 PeekMessage + 硬超时，避免 worker 卡住时整个进程吊死。
    msg = wintypes.MSG()
    deadline = time.time() + 20.0
    while time.time() < deadline:
        got_any = False
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
            got_any = True
            if msg.message == 0x0012:                                 # WM_QUIT
                deadline = 0
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if deadline == 0:
            break
        if not th.is_alive() and not got_any:
            # worker 已经收工并且队列排空，再多泵一会儿收尾消息
            break
        time.sleep(0.010)

    th.join(timeout=2.0)

    results = []
    while not out.empty():
        results.append(out.get())

    print()
    for item in results:
        print(" ", item, flush=True)

    got = ""
    for item in results:
        if item[0] == "text":
            got = item[1]

    print()
    print("=" * 62)
    if got == "ABC":
        print("PASS：真前台 + 真 SendInput，字符准确落进目标窗口。")
        rc = 0
    else:
        print(f"FAIL：期望 'ABC'，实得 {got!r}")
        rc = 1
    print("=" * 62)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
