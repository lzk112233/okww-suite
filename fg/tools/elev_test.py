"""提权后重测：管理员权限下 SendInput 能不能进游戏。

结论落盘到同目录 _elev_out.txt（提权进程的 stdout 抓不到，必须写文件）。
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

sys.path.insert(0, r"D:\ai\dfy\okww-fg")

OUT = r"D:\ai\dfy\okww-fg\_elev_out.txt"
lines = []


def log(s=""):
    lines.append(str(s))
    print(s)


try:
    from okww_fg import foreground as fg
    from PIL import ImageGrab
    import numpy as np
except Exception as e:
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(f"import failed: {type(e).__name__}: {e}\n")
    raise SystemExit(1)

GAME = 202028
u = fg.user32
KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

# 完整性自检
adv = ctypes.windll.advapi32
k32 = ctypes.windll.kernel32
h = k32.OpenProcess(0x1000, False, __import__("os").getpid())
tok = wt.HANDLE()
k32.OpenProcessToken(h, 0x0008, ctypes.byref(tok))
buf = wt.DWORD(0)
ret = wt.DWORD(0)
adv.GetTokenInformation(tok, 20, ctypes.byref(buf), ctypes.sizeof(buf), ctypes.byref(ret))
log(f"自己的完整性: {'提权(管理员)' if buf.value else '普通权限'}")


def shot():
    return np.asarray(ImageGrab.grab(all_screens=False).convert("L"), np.uint8)


def stats(a, b):
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(d.mean()), float((d > 24).mean() * 100)


try:
    fg.force_foreground(GAME)
    time.sleep(0.7)
    log(f"前台 = {fg.foreground_hwnd()}")

    def tap(vk, name, sc=0, flags_up=KEYUP, hold_ms=60):
        a = shot()
        u.keybd_event(vk, sc, 0, None)
        time.sleep(hold_ms / 1000.0)
        u.keybd_event(vk, sc, flags_up, None)
        time.sleep(0.75)
        b = shot()
        m, pct = stats(a, b)
        log(f"  {name:<26} 差异 mean={m:6.2f}  变化 {pct:6.2f}%  -> {'有反应' if pct > 2.0 else '没反应'}")
        return pct

    log("\n=== 管理员权限下重测 ===")
    tap(0x70, "F1")
    tap(0x1B, "Esc")
    tap(0x57, "W 按住 500ms", hold_ms=500)
    tap(0x57, "W scancode 按住 500ms", sc=0x11,
        flags_up=KEYEVENTF_SCANCODE | KEYUP, hold_ms=500)

    log("\n=== 鼠标 ===")
    a = shot()
    u.mouse_event(0x0001, 200, 0, 0, None)
    time.sleep(0.4)
    b = shot()
    m, pct = stats(a, b)
    log(f"  相对移动 +200px  差异 mean={m:6.2f}  变化 {pct:6.2f}%  -> {'有反应' if pct > 1.0 else '没反应'}")
finally:
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
