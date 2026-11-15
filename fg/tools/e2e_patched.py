"""用真 ok-ww 的 PostMessageInteraction 跑一遍打补丁后的链路。

和 e2e_focus.py 的区别：那个只测 InputSink；这个测**补丁本身**——
把真实的 post_message.PostMessageInteraction 换掉 6 个方法之后，
再走它自己的 try_activate / send_key_down / send_key_up，
看目标窗口收到的 WM_ACTIVATE 里 GetForegroundWindow 是不是它自己。

跑法：
    python tools\\e2e_patched.py

会打印一张对照：补丁前（原方法）发一轮、补丁后发一轮，
各自统计「收到几条 WM_ACTIVATE / 其中几条是伪造的」。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
import types
from ctypes import wintypes

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

OKWW_WORK = r"C:\Users\lzk11\AppData\Local\ok-ww\data\apps\ok-ww\working"
if OKWW_WORK not in sys.path:
    sys.path.append(OKWW_WORK)

from okww_fg import foreground as fg  # noqa: E402
import okww_fg.interaction as itc  # noqa: E402

user32 = fg.user32
kernel32 = fg.kernel32

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
ES_MULTILINE = 0x0004

WM_CLOSE = 0x0010
WM_ACTIVATE = 0x0006
WM_APP_FOCUS_EDIT = 0x8001

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
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
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
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
ACT = {"total": 0, "fake": 0, "active": [], "raw": []}


def wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_CLOSE:
        user32.DestroyWindow(hwnd)
        user32.PostQuitMessage(0)
        return 0
    if msg == WM_APP_FOCUS_EDIT:
        user32.SetFocus(_EDIT[0])
        return 0
    if msg == WM_ACTIVATE:
        ACT["raw"].append(int(wparam) & 0xFFFF)
        if int(wparam) & 0xFFFF != 0:      # WA_ACTIVE / WA_CLICKACTIVE
            fgc = user32.GetForegroundWindow()
            ACT["total"] += 1
            if int(fgc) != int(hwnd):
                ACT["fake"] += 1
            else:
                ACT["active"].append(int(fgc))
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def build_target(tag: str):
    hinst = kernel32.GetModuleHandleW(None)
    proc = WNDPROC(wndproc)
    _WNDPROC_REF.append(proc)
    cls = WNDCLASSW()
    cls.lpfnWndProc = proc
    cls.hInstance = hinst
    cls.lpszClassName = f"OkwwFgE2EPatch{tag}"
    user32.RegisterClassW(ctypes.byref(cls))
    main = user32.CreateWindowExW(
        0, f"OkwwFgE2EPatch{tag}", f"okww-fg patch target {tag}",
        WS_OVERLAPPEDWINDOW, 300, 260, 640, 320, None, None, hinst, None)
    edit = user32.CreateWindowExW(
        0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | ES_MULTILINE,
        10, 10, 600, 250, main, None, hinst, None)
    _EDIT[0] = int(edit)
    user32.ShowWindow(main, 4)          # SW_SHOWNOACTIVATE
    user32.UpdateWindow(main)
    return int(main), int(edit)


def read_edit(edit: int) -> str:
    buf = ctypes.create_unicode_buffer(2048)
    n = user32.GetWindowTextW(edit, buf, 2048)
    return buf.value[:n]


def pump(seconds: float) -> None:
    msg = wintypes.MSG()
    end = time.time() + seconds
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            if msg.message == 0x0012:      # WM_QUIT
                return
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.008)


def make_interaction(main_hwnd: int, edit: int):
    from ok.device.interaction_methods import post_message as pm

    hwnd_window = types.SimpleNamespace(
        hwnd=main_hwnd, top_hwnd=main_hwnd,
        client_rect=lambda: (0, 0, 640, 320),
    )
    capture = types.SimpleNamespace(hwnd_window=hwnd_window)
    inst = pm.PostMessageInteraction(capture, hwnd_window)
    # update_mouse_pos 用 target client 坐标算，直接指到 EDIT 上
    inst._dynamic_target_hwnd = edit
    return pm, inst


def turn(main_hwnd: int, edit: int, label: str, patched: bool, away: int = 0) -> dict:
    ACT["total"] = 0
    ACT["fake"] = 0
    ACT["active"].clear()
    ACT["raw"].clear()

    # 每轮都必须从"目标不在前台"开始：否则 ensure_foreground 会短路返回，
    # 一条事件都不会产生，两轮就没有可比性。
    if away and int(away) != main_hwnd:
        fg.force_foreground(int(away))
    for _ in range(20):
        if fg.foreground_hwnd() != main_hwnd:
            break
        pump(0.03)
    print(f"  [{label}] 起始前台 = {fg.foreground_hwnd()}", flush=True)

    pm, inst = make_interaction(main_hwnd, edit)
    if patched:
        ok = itc.install()
        print(f"  [{label}] install -> {ok.get('patched') or ok}", flush=True)
        pm, inst = make_interaction(main_hwnd, edit)

    fg_before = fg.foreground_hwnd()
    t0 = time.perf_counter()
    inst.try_activate()
    rise_ms = (time.perf_counter() - t0) * 1000
    piped = False
    for _ in range(30):
        pump(0.02)
        if fg.foreground_hwnd() == main_hwnd:
            piped = True
            break
    fg_after = fg.foreground_hwnd()

    user32.PostMessageW(main_hwnd, WM_APP_FOCUS_EDIT, 0, 0)
    pump(0.12)

    for ch in "XYZ":
        inst.send_key_down(ch)          # 原方法收的是"字符"，不是 vk
        inst.send_key_up(ch)
        pump(0.06)
    pump(0.25)

    text = read_edit(edit)
    return {
        "label": label,
        "patched": patched,
        "fg_before": fg_before,
        "fg_after": fg_after,
        "rise_ms": rise_ms,
        "piped": piped,
        "activations": ACT["total"],
        "fake": ACT["fake"],
        "raw": list(ACT["raw"]),
        "text": text,
    }


def main() -> int:
    tag = str(int(time.time()) % 10000)
    main_hwnd, edit = build_target(tag)
    print(f"target main={main_hwnd}  edit={edit}", flush=True)
    pump(0.30)

    # 先把前台推回浏览器，好让每一轮都从"不在前台"开始
    other = fg.foreground_hwnd()
    print(f"起始前台 = {other}", flush=True)

    rows = []
    rows.append(turn(main_hwnd, edit, "原版(PostMessage)", patched=False))
    # 关键：补丁轮开始前必须把前台让回别的窗口，否则窗口本来就在前台，
    # ensure_foreground 直接短路返回，两轮都没有可比性。
    if other and int(other) != main_hwnd:
        back = fg.force_foreground(int(other))
        print(f"  前台让回 {other} -> ok={back.get('ok')}", flush=True)
        pump(0.20)
    rows.append(turn(main_hwnd, edit, "补丁(真前台)", patched=True))

    print()
    print(f"{'阶段':<18}{'抢到前台':<10}{'耗时ms':<10}{'WM_ACTIVATE':<14}{'伪造':<8}{'EDIT文字'}")
    print("-" * 78)
    for r in rows:
        print(f"{r['label']:<18}{str(r['piped']):<10}{r['rise_ms']:<10.1f}"
              f"{r['activations']:<14}{r['fake']:<8}{r['text']!r}")
    print("-" * 78)

    ok = (rows[1]["piped"] and rows[1]["text"].lower() == "xyz"
          and rows[1]["fake"] == 0 and rows[1]["activations"] > 0)
    print()
    if ok:
        print("PASS：补丁后前台真的切过去了，WM_ACTIVATE 里 GetForegroundWindow 全是自己，")
        print("      键也真的打进了窗口 —— 判据从「伪造」变成「真实」。")
    else:
        print("FAIL：看上面哪一列不对。")
    if rows[0]["activations"] and rows[0]["fake"] == rows[0]["activations"]:
        print("      （对照组符合预期：原版每一条 WM_ACTIVATE 都是伪造的）")

    user32.PostMessageW(main_hwnd, WM_CLOSE, 0, 0)
    pump(0.3)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
